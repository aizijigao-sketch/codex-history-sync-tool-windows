from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKEND = ROOT / "sync_backend.py"
DEFAULT_WATCHER = ROOT / "scripts" / "windows_auto_sync_watcher.py"
DEFAULT_TASK_NAME = "CodexHistorySyncToolAutoSync"
DEFAULT_APPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
DEFAULT_STATE_DIR = DEFAULT_APPDATA / "Codex History Sync Tool"
DEFAULT_STARTUP_DIR = (
    Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    / "Microsoft"
    / "Windows"
    / "Start Menu"
    / "Programs"
    / "Startup"
)


def run_schtasks(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["schtasks.exe", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def task_query(task_name: str) -> dict[str, object]:
    try:
        completed = run_schtasks("/Query", "/TN", task_name, "/FO", "LIST", "/V")
    except OSError as exc:
        return {
            "exists": False,
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "task_query_ok": False,
            "task_query_error": str(exc),
        }
    exists = completed.returncode == 0
    return {
        "exists": exists,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "task_query_ok": exists,
        "task_query_error": "" if exists else (completed.stderr or completed.stdout).strip(),
    }


def task_command(
    watcher: Path,
    backend: Path,
    codex_home: str | None,
    log_path: Path,
    lock_path: Path,
    process_name: str,
) -> str:
    parts = [
        f'"{sys.executable}"',
        f'"{watcher}"',
        "--backend",
        f'"{backend}"',
        "--log",
        f'"{log_path}"',
        "--lock",
        f'"{lock_path}"',
        "--process-name",
        f'"{process_name}"',
    ]
    if codex_home:
        parts.extend(["--codex-home", f'"{codex_home}"'])
    return " ".join(parts)


def bundled_task_command(
    codex_home: str | None,
    log_path: Path,
    lock_path: Path,
    process_name: str,
) -> str:
    runner = Path(sys.executable).with_name("CodexHistorySyncToolCli.exe")
    if not runner.exists():
        runner = Path(sys.executable)
    parts = [
        f'"{runner}"',
        "--run-watcher",
        "--backend",
        "__bundled__",
        "--log",
        f'"{log_path}"',
        "--lock",
        f'"{lock_path}"',
        "--process-name",
        f'"{process_name}"',
    ]
    if codex_home:
        parts.extend(["--codex-home", f'"{codex_home}"'])
    return " ".join(parts)


def write_task_launcher(
    state_dir: Path,
    watcher: Path,
    backend: Path,
    codex_home: str | None,
    log_path: Path,
    lock_path: Path,
    process_name: str,
) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    launcher_path = state_dir / "autosync-task.cmd"
    if getattr(sys, "frozen", False):
        command = bundled_task_command(codex_home, log_path, lock_path, process_name)
    else:
        command = task_command(watcher, backend, codex_home, log_path, lock_path, process_name)
    launcher_path.write_text(f"@echo off\r\n{command}\r\n", encoding="utf-8")
    return launcher_path


def startup_launcher_path(startup_dir: Path) -> Path:
    return startup_dir / "CodexHistorySyncToolAutoSync.vbs"


def write_startup_launcher(startup_dir: Path, task_launcher: Path) -> Path:
    startup_dir.mkdir(parents=True, exist_ok=True)
    launcher_path = startup_launcher_path(startup_dir)
    escaped = str(task_launcher).replace('"', '""')
    launcher_path.write_text(
        'Set shell = CreateObject("WScript.Shell")\r\n'
        f'shell.Run """{escaped}""", 0, False\r\n',
        encoding="utf-8",
    )
    return launcher_path


def remove_startup_launcher(startup_dir: Path) -> bool:
    launcher_path = startup_launcher_path(startup_dir)
    if not launcher_path.exists():
        return False
    launcher_path.unlink()
    return True


def startup_query(startup_dir: Path) -> dict[str, object]:
    launcher_path = startup_launcher_path(startup_dir)
    return {
        "startup_exists": launcher_path.exists(),
        "startup_launcher_path": str(launcher_path),
    }


def scheduler_error_text(error: object) -> str:
    if isinstance(error, subprocess.CompletedProcess):
        return (error.stderr or error.stdout).strip()
    return str(error)


def scheduler_install_failed_payload(
    args: argparse.Namespace,
    command: str,
    launcher_path: Path,
    startup_path: Path,
    scheduler_error: object,
    log_path: Path,
    lock_path: Path,
) -> dict[str, object]:
    task_error = scheduler_error_text(scheduler_error)
    return {
        "ok": True,
        "action": "install",
        "method": "startup",
        "task_name": args.task_name,
        "command": command,
        "launcher_path": str(launcher_path),
        "task_ok": False,
        "startup_ok": True,
        "task_error": task_error,
        "scheduler_error": task_error,
        "startup_launcher_path": str(startup_path),
        "log_path": str(log_path),
        "lock_path": str(lock_path),
    }


def install_task(args: argparse.Namespace) -> dict[str, object]:
    watcher = Path(args.watcher).expanduser().resolve()
    backend = Path(args.backend).expanduser().resolve()
    state_dir = Path(args.state_dir).expanduser().resolve()
    startup_dir = Path(args.startup_dir).expanduser().resolve()
    log_path = Path(args.log).expanduser().resolve() if args.log else state_dir / "autosync.log"
    lock_path = Path(args.lock).expanduser().resolve() if args.lock else state_dir / "autosync.lock"

    if not getattr(sys, "frozen", False) and not watcher.exists():
        raise RuntimeError(f"watcher does not exist: {watcher}")
    if not getattr(sys, "frozen", False) and not backend.exists():
        raise RuntimeError(f"backend does not exist: {backend}")

    state_dir.mkdir(parents=True, exist_ok=True)
    launcher_path = write_task_launcher(
        state_dir,
        watcher,
        backend,
        args.codex_home,
        log_path,
        lock_path,
        args.process_name,
    )
    command = f'"{launcher_path}"'
    try:
        completed = run_schtasks(
            "/Create",
            "/TN",
            args.task_name,
            "/SC",
            "ONLOGON",
            "/TR",
            command,
            "/RL",
            "LIMITED",
            "/F",
        )
    except OSError as exc:
        startup_path = write_startup_launcher(startup_dir, launcher_path)
        return scheduler_install_failed_payload(args, command, launcher_path, startup_path, exc, log_path, lock_path)
    if completed.returncode != 0:
        startup_path = write_startup_launcher(startup_dir, launcher_path)
        return scheduler_install_failed_payload(args, command, launcher_path, startup_path, completed, log_path, lock_path)
    remove_startup_launcher(startup_dir)
    return {
        "ok": True,
        "action": "install",
        "method": "task_scheduler",
        "task_name": args.task_name,
        "command": command,
        "launcher_path": str(launcher_path),
        "task_ok": True,
        "startup_ok": False,
        "task_error": "",
        "log_path": str(log_path),
        "lock_path": str(lock_path),
    }


def uninstall_task(args: argparse.Namespace) -> dict[str, object]:
    startup_dir = Path(args.startup_dir).expanduser().resolve()
    startup_removed = remove_startup_launcher(startup_dir)
    completed = run_schtasks("/Delete", "/TN", args.task_name, "/F")
    if completed.returncode != 0:
        status = task_query(args.task_name)
        if status["exists"]:
            raise RuntimeError((completed.stderr or completed.stdout).strip())
    return {
        "ok": True,
        "action": "uninstall",
        "task_name": args.task_name,
        "removed": completed.returncode == 0 or startup_removed,
        "task_removed": completed.returncode == 0,
        "startup_removed": startup_removed,
    }


def status_task(args: argparse.Namespace) -> dict[str, object]:
    startup_dir = Path(args.startup_dir).expanduser().resolve()
    status = task_query(args.task_name)
    startup = startup_query(startup_dir)
    exists = bool(status["exists"] or startup["startup_exists"])
    method = "task_scheduler" if status["exists"] else "startup" if startup["startup_exists"] else "none"
    return {
        "ok": True,
        "action": "status",
        "task_name": args.task_name,
        **status,
        **startup,
        "exists": exists,
        "method": method,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install or remove the Windows auto-sync scheduled task.")
    parser.add_argument("command", choices=["install", "uninstall", "status"])
    parser.add_argument("--task-name", default=DEFAULT_TASK_NAME)
    parser.add_argument("--watcher", default=str(DEFAULT_WATCHER))
    parser.add_argument("--backend", default=str(DEFAULT_BACKEND))
    parser.add_argument("--codex-home", help="Codex home directory; omit to use the backend default")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--startup-dir", default=str(DEFAULT_STARTUP_DIR))
    parser.add_argument("--log")
    parser.add_argument("--lock")
    parser.add_argument("--process-name", default="Codex.exe")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def emit(payload: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def main() -> int:
    args = parse_args()
    try:
        if args.command == "install":
            payload = install_task(args)
        elif args.command == "uninstall":
            payload = uninstall_task(args)
        elif args.command == "status":
            payload = status_task(args)
        else:
            raise RuntimeError(f"unsupported command: {args.command}")
    except Exception as exc:
        payload = {"ok": False, "error": str(exc)}
        emit(payload, args.json)
        return 1
    emit(payload, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
