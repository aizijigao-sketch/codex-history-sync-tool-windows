from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import windows_autosync_settings


DEFAULT_POLL_SECONDS = 3.0
DEFAULT_INITIAL_DELAY_SECONDS = 2.0
DEFAULT_DEBOUNCE_SECONDS = 2.0
DEFAULT_COOLDOWN_SECONDS = 60.0
DEFAULT_PROCESS_NAME = "Codex.exe"


def append_log(path: Path | None, message: str) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    line = f"[{timestamp}] {message}"
    if path is None:
        print(line, flush=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


class SingleInstanceLock:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> "SingleInstanceLock":
        if self.path is None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            self.fd = os.open(str(self.path), flags)
        except FileExistsError as exc:
            raise RuntimeError(f"another watcher instance is already running: {self.path}") from exc
        os.write(self.fd, str(os.getpid()).encode("utf-8"))
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.path is not None:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


def windows_process_running(process_name: str) -> bool:
    completed = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        error_text = f"{completed.stdout}\n{completed.stderr}".lower()
        if "access denied" in error_text:
            return True
        return False
    output = completed.stdout.strip()
    if not output or "INFO:" in output:
        return False
    for row in csv.reader(output.splitlines()):
        if row and row[0].lower() == process_name.lower():
            return True
    return False


def run_backend(
    backend: Path,
    command: str,
    codex_home: str | None,
    timeout_seconds: int | None = None,
) -> dict[str, object]:
    return run_backend_args(backend, [command], codex_home, timeout_seconds)


def run_backend_args(
    backend: Path,
    backend_args: list[str],
    codex_home: str | None,
    timeout_seconds: int | None = None,
) -> dict[str, object]:
    if str(backend) == "__bundled__":
        cmd = [sys.executable, "--run-backend", "--json"]
    else:
        cmd = [sys.executable, str(backend), "--json"]
    if codex_home:
        cmd.extend(["--codex-home", codex_home])
    cmd.extend(backend_args)
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        seconds = timeout_seconds if timeout_seconds is not None else "unknown"
        raise RuntimeError(f"backend timed out after {seconds} seconds") from exc
    text = (completed.stdout or completed.stderr).strip()
    if not text:
        raise RuntimeError("backend returned no output")
    payload = json.loads(text)
    if completed.returncode != 0 or not payload.get("ok"):
        raise RuntimeError(str(payload.get("error") or text))
    return payload


def watched_codex_homes(codex_home: str | None, dual_home: bool) -> list[str | None]:
    if codex_home:
        return [codex_home]
    if not dual_home:
        return [None]
    homes = [Path.home() / ".codex", Path.home() / ".codex-official"]
    existing = [str(path) for path in homes if path.exists()]
    return existing or [None]


def resolve_codex_home_for_fingerprint(codex_home: str | None) -> Path:
    if codex_home:
        return Path(codex_home).expanduser()
    return Path.home() / ".codex"


def file_fingerprint(path: Path) -> str:
    stat = path.stat()
    digest = hashlib.sha256()
    digest.update(str(path.relative_to(path.parents[0]) if path.parent != path else path).encode("utf-8", "ignore"))
    digest.update(str(stat.st_size).encode("ascii"))
    digest.update(str(stat.st_mtime_ns).encode("ascii"))
    return digest.hexdigest()


def iter_fingerprint_paths(codex_home: Path):
    for name in ("config.toml", "state_5.sqlite", "state_5.sqlite-wal", "state_5.sqlite-shm", "session_index.jsonl"):
        path = codex_home / name
        if path.exists() and path.is_file():
            yield path
    for folder in ("sessions", "archived_sessions"):
        root = codex_home / folder
        if root.exists():
            yield from sorted(path for path in root.rglob("*.jsonl") if path.is_file())


def codex_home_fingerprint(codex_home: str | None) -> str:
    home = resolve_codex_home_for_fingerprint(codex_home)
    digest = hashlib.sha256()
    for path in iter_fingerprint_paths(home):
        digest.update(str(path.relative_to(home)).replace("\\", "/").encode("utf-8", "ignore"))
        digest.update(file_fingerprint(path).encode("ascii"))
    return digest.hexdigest()


def pending_work_count(payload: dict[str, object]) -> int:
    return (
        int(payload.get("movable_threads") or 0)
        + int(payload.get("model_movable_threads") or 0)
        + int(payload.get("movable_session_meta_entries") or 0)
    )


def project_repair_needed(status: dict[str, object]) -> bool:
    diagnostics = status.get("project_diagnostics") or {}
    if not isinstance(diagnostics, dict):
        return False
    duplicate_count = len(diagnostics.get("duplicate_local_project_paths") or [])
    project_count = int(diagnostics.get("project_root_count") or 0)
    recent_project_threads = int(diagnostics.get("recent_50_project_thread_count") or 0)
    return duplicate_count > 0 or (project_count > 0 and recent_project_threads == 0)


def sync_if_needed(
    backend: Path,
    codex_home: str | None,
    log_path: Path | None,
    settings: dict[str, bool],
    reason: str,
) -> bool:
    status = run_backend(backend, "status", codex_home, timeout_seconds=30)
    pending_threads = pending_work_count(status)
    current_provider = str(status.get("current_provider") or "").strip()
    home_label = codex_home or "default"
    append_log(log_path, f"{reason}: home={home_label}, provider={current_provider}, pending={pending_threads}")
    if not settings["auto_detect"]:
        append_log(log_path, "Auto sync skipped: background detection is disabled")
        return False
    if not current_provider:
        append_log(log_path, "Auto sync skipped: current provider is empty")
        return False
    if settings["detect_only"]:
        append_log(log_path, "Detect-only mode: no data was changed")
        return False
    if settings["auto_fix_projects"] and project_repair_needed(status):
        repair = run_backend(backend, "project-repair", codex_home, timeout_seconds=120)
        append_log(
            log_path,
            "Project repair completed: "
            f"added={repair.get('added_saved_workspace_roots')}, "
            f"removed_duplicates={repair.get('removed_local_project_duplicates')}, "
            f"backup={repair.get('safety_backup')}",
        )
    if pending_threads <= 0:
        append_log(log_path, "Auto sync skipped: no pending work")
        return False
    if not settings["auto_fix_chats"]:
        append_log(log_path, "Auto sync skipped: chat auto-fix is disabled")
        return False

    payload = run_backend(backend, "sync", codex_home, timeout_seconds=120)
    visibility_updates = payload.get("visibility_updates") or {}
    append_log(
        log_path,
        "Auto sync completed: "
        f"updated_rows={payload.get('updated_rows')}, "
        f"updated_session_files={payload.get('updated_session_files')}, "
        f"visibility_updates={json.dumps(visibility_updates, ensure_ascii=False)}, "
        f"backup={payload.get('backup_path')}",
    )
    return True


def watch(args: argparse.Namespace) -> int:
    backend = Path(args.backend).expanduser()
    log_path = Path(args.log).expanduser() if args.log else None
    lock_path = Path(args.lock).expanduser() if args.lock else None
    settings = windows_autosync_settings.load_settings(args.settings_state_dir)
    if args.backend != "__bundled__" and not backend.exists():
        append_log(log_path, f"backend does not exist: {backend}")
        return 1

    with SingleInstanceLock(lock_path):
        initial_open = windows_process_running(args.process_name)
        previous_open = False
        homes = watched_codex_homes(args.codex_home, settings["dual_home"])
        append_log(
            log_path,
            f"watcher started; process={args.process_name}; codex_open={initial_open}; "
            f"homes={len(homes)}; settings={json.dumps(settings, ensure_ascii=False)}",
        )

        if args.once:
            if initial_open:
                time.sleep(args.initial_delay)
                for home in homes:
                    sync_if_needed(backend, home, log_path, settings, "Codex opened")
            else:
                append_log(log_path, "Auto sync skipped: Codex process is not running")
            return 0

        last_fingerprints: dict[str, str] = {}
        pending_since: dict[str, float] = {}
        cooldown_until: dict[str, float] = {}
        cycles = 0
        while True:
            cycles += 1
            is_open = windows_process_running(args.process_name)
            if is_open and not previous_open:
                time.sleep(args.initial_delay)
                try:
                    settings = windows_autosync_settings.load_settings(args.settings_state_dir)
                    homes = watched_codex_homes(args.codex_home, settings["dual_home"])
                    for home in homes:
                        sync_if_needed(backend, home, log_path, settings, "Codex opened")
                        if not args.no_fingerprint:
                            try:
                                last_fingerprints[home or "default"] = codex_home_fingerprint(home)
                            except OSError as exc:
                                append_log(log_path, f"Fingerprint skipped: {exc}")
                except Exception as exc:
                    append_log(log_path, f"auto sync failed: {exc}")
            elif is_open and not args.no_fingerprint:
                now = time.monotonic()
                try:
                    settings = windows_autosync_settings.load_settings(args.settings_state_dir)
                    homes = watched_codex_homes(args.codex_home, settings["dual_home"])
                    for home in homes:
                        home_key = home or "default"
                        current_fingerprint = codex_home_fingerprint(home)
                        previous_fingerprint = last_fingerprints.get(home_key)
                        if previous_fingerprint is None:
                            last_fingerprints[home_key] = current_fingerprint
                            continue
                        if current_fingerprint != previous_fingerprint:
                            last_fingerprints[home_key] = current_fingerprint
                            if home_key not in pending_since:
                                pending_since[home_key] = now
                                append_log(log_path, "Codex files changed: queued smart auto repair check")
                        queued_at = pending_since.get(home_key)
                        if queued_at is None or now - queued_at < args.debounce:
                            continue
                        if now < cooldown_until.get(home_key, 0.0):
                            append_log(log_path, "Smart auto repair delayed: cooldown active")
                            continue
                        pending_since.pop(home_key, None)
                        did_sync = sync_if_needed(backend, home, log_path, settings, "Smart auto repair check")
                        cooldown_until[home_key] = time.monotonic() + args.cooldown
                        if did_sync:
                            try:
                                last_fingerprints[home_key] = codex_home_fingerprint(home)
                            except OSError as exc:
                                append_log(log_path, f"Fingerprint refresh skipped: {exc}")
                except Exception as exc:
                    append_log(log_path, f"smart auto repair failed: {exc}")
            previous_open = is_open
            if args.max_cycles is not None and cycles >= args.max_cycles:
                return 0
            time.sleep(args.poll)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch Codex Desktop launches and auto-sync local history on Windows.")
    parser.add_argument("--backend", required=True, help="Path to sync_backend.py")
    parser.add_argument("--codex-home", help="Codex home directory; defaults to backend default")
    parser.add_argument("--log", help="Log file path")
    parser.add_argument("--lock", help="Single-instance lock file path")
    parser.add_argument("--settings-state-dir", help="Directory containing autosync-settings.json")
    parser.add_argument("--process-name", default=DEFAULT_PROCESS_NAME, help="Windows process image name to watch")
    parser.add_argument("--poll", type=float, default=DEFAULT_POLL_SECONDS, help="Process polling interval")
    parser.add_argument("--initial-delay", type=float, default=DEFAULT_INITIAL_DELAY_SECONDS)
    parser.add_argument("--debounce", type=float, default=DEFAULT_DEBOUNCE_SECONDS)
    parser.add_argument("--cooldown", type=float, default=DEFAULT_COOLDOWN_SECONDS)
    parser.add_argument("--no-fingerprint", action="store_true", help="Disable low-frequency file fingerprint checks")
    parser.add_argument("--max-cycles", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--once", action="store_true", help="Run one detection pass and exit")
    return parser.parse_args()


def main() -> int:
    try:
        return watch(parse_args())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"watcher failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
