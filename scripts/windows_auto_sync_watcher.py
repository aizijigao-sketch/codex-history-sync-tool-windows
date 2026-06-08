from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


DEFAULT_POLL_SECONDS = 3.0
DEFAULT_INITIAL_DELAY_SECONDS = 2.0
DEFAULT_PROCESS_NAME = "Codex.exe"
DEFAULT_DEBOUNCE_SECONDS = 2.0
DEFAULT_COOLDOWN_SECONDS = 60.0


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
    if str(backend) == "__bundled__":
        cmd = [sys.executable, "--run-backend", "--json"]
    else:
        cmd = [sys.executable, str(backend), "--json"]
    if codex_home:
        cmd.extend(["--codex-home", codex_home])
    cmd.append(command)
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


def pending_work_count(payload: dict[str, object]) -> int:
    return (
        int(payload.get("movable_threads") or 0)
        + int(payload.get("model_movable_threads") or 0)
        + int(payload.get("movable_session_meta_entries") or 0)
    )


def sync_if_needed(backend: Path, codex_home: str | None, log_path: Path | None, reason: str) -> bool:
    status = run_backend(backend, "status", codex_home, timeout_seconds=30)
    pending_threads = pending_work_count(status)
    current_provider = str(status.get("current_provider") or "").strip()
    append_log(log_path, f"{reason}: provider={current_provider}, pending={pending_threads}")
    if not current_provider:
        append_log(log_path, "Auto sync skipped: current provider is empty")
        return False
    if pending_threads <= 0:
        append_log(log_path, "Auto sync skipped: no pending work")
        return False

    payload = run_backend(backend, "sync", codex_home, timeout_seconds=120)
    append_log(
        log_path,
        "Auto sync completed: "
        f"updated_rows={payload.get('updated_rows')}, "
        f"visibility_updates={payload.get('visibility_updates')}, "
        f"updated_session_files={payload.get('updated_session_files')}, "
        f"backup={payload.get('backup_path')}",
    )
    return True


def resolve_codex_home_for_fingerprint(codex_home: str | None) -> Path:
    return Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"


def file_fingerprint(path: Path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path), int(stat.st_mtime_ns), int(stat.st_size))


def iter_fingerprint_paths(codex_home: Path) -> list[Path]:
    paths = [
        codex_home / "config.toml",
        codex_home / "state_5.sqlite",
        codex_home / "state_5.sqlite-wal",
        codex_home / "state_5.sqlite-shm",
        codex_home / "session_index.jsonl",
    ]
    for directory_name in ("sessions", "archived_sessions"):
        directory = codex_home / directory_name
        if directory.exists():
            paths.extend(directory.rglob("rollout-*.jsonl"))
    return sorted(paths, key=lambda item: str(item).casefold())


def codex_home_fingerprint(codex_home: str | None) -> tuple[tuple[str, int, int], ...]:
    home = resolve_codex_home_for_fingerprint(codex_home)
    parts = []
    for path in iter_fingerprint_paths(home):
        item = file_fingerprint(path)
        if item is not None:
            parts.append(item)
    return tuple(parts)


def watch(args: argparse.Namespace) -> int:
    backend = Path(args.backend).expanduser()
    log_path = Path(args.log).expanduser() if args.log else None
    lock_path = Path(args.lock).expanduser() if args.lock else None
    if args.backend != "__bundled__" and not backend.exists():
        append_log(log_path, f"backend does not exist: {backend}")
        return 1

    with SingleInstanceLock(lock_path):
        initial_open = windows_process_running(args.process_name)
        previous_open = False
        last_fingerprint = codex_home_fingerprint(args.codex_home) if args.fingerprint else tuple()
        pending_changed_at: float | None = None
        next_retry_at: float | None = None
        last_write_sync_at = 0.0
        cycles = 0
        append_log(log_path, f"watcher started; process={args.process_name}; codex_open={initial_open}")

        if args.once:
            if initial_open:
                time.sleep(args.initial_delay)
                sync_if_needed(backend, args.codex_home, log_path, "Codex opened")
            else:
                append_log(log_path, "Auto sync skipped: Codex process is not running")
            return 0

        while True:
            cycles += 1
            is_open = windows_process_running(args.process_name)
            if is_open and not previous_open:
                time.sleep(args.initial_delay)
                try:
                    if sync_if_needed(backend, args.codex_home, log_path, "Codex opened"):
                        last_write_sync_at = time.monotonic()
                except Exception as exc:
                    append_log(log_path, f"auto sync failed: {exc}")
                if args.fingerprint:
                    last_fingerprint = codex_home_fingerprint(args.codex_home)

            if is_open and args.fingerprint:
                current_fingerprint = codex_home_fingerprint(args.codex_home)
                if current_fingerprint != last_fingerprint:
                    last_fingerprint = current_fingerprint
                    pending_changed_at = time.monotonic()
                    next_retry_at = None
                    append_log(log_path, "Codex files changed: queued smart auto repair check")

                if pending_changed_at is not None:
                    now = time.monotonic()
                    due_at = pending_changed_at + args.debounce
                    if next_retry_at is not None:
                        due_at = max(due_at, next_retry_at)
                    if now < due_at:
                        pass
                    elif now - last_write_sync_at < args.cooldown:
                        next_retry_at = last_write_sync_at + args.cooldown
                        append_log(log_path, "Smart auto repair delayed: cooldown active")
                    else:
                        pending_changed_at = None
                        next_retry_at = None
                        try:
                            if sync_if_needed(backend, args.codex_home, log_path, "Codex files changed"):
                                last_write_sync_at = time.monotonic()
                        except Exception as exc:
                            append_log(log_path, f"smart auto repair failed: {exc}")
            previous_open = is_open
            if args.max_cycles and cycles >= args.max_cycles:
                append_log(log_path, f"watcher stopped after max cycles: {args.max_cycles}")
                return 0
            time.sleep(args.poll)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch Codex Desktop launches and auto-sync local history on Windows.")
    parser.add_argument("--backend", required=True, help="Path to sync_backend.py")
    parser.add_argument("--codex-home", help="Codex home directory; defaults to backend default")
    parser.add_argument("--log", help="Log file path")
    parser.add_argument("--lock", help="Single-instance lock file path")
    parser.add_argument("--process-name", default=DEFAULT_PROCESS_NAME, help="Windows process image name to watch")
    parser.add_argument("--poll", type=float, default=DEFAULT_POLL_SECONDS, help="Process polling interval")
    parser.add_argument("--initial-delay", type=float, default=DEFAULT_INITIAL_DELAY_SECONDS)
    parser.add_argument("--debounce", type=float, default=DEFAULT_DEBOUNCE_SECONDS, help="Seconds to wait after file changes before checking")
    parser.add_argument("--cooldown", type=float, default=DEFAULT_COOLDOWN_SECONDS, help="Minimum seconds between write repairs")
    parser.add_argument("--no-fingerprint", dest="fingerprint", action="store_false", help="Disable smart file-change checks")
    parser.add_argument("--max-cycles", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--once", action="store_true", help="Run one detection pass and exit")
    parser.set_defaults(fingerprint=True)
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
