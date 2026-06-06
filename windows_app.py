from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def run_backend(argv: list[str]) -> int:
    import sync_backend

    original_argv = sys.argv[:]
    try:
        sys.argv = ["sync_backend.py", *argv]
        return int(sync_backend.main())
    finally:
        sys.argv = original_argv


def run_watcher(argv: list[str]) -> int:
    from scripts import windows_auto_sync_watcher

    watcher_argv = list(argv)
    if "--backend" not in watcher_argv:
        watcher_argv = ["--backend", "__bundled__", *watcher_argv]
    original_argv = sys.argv[:]
    try:
        sys.argv = ["windows_auto_sync_watcher.py", *watcher_argv]
        return int(windows_auto_sync_watcher.main())
    finally:
        sys.argv = original_argv


def run_task(argv: list[str]) -> int:
    from scripts import windows_task_scheduler

    original_argv = sys.argv[:]
    try:
        sys.argv = ["windows_task_scheduler.py", *argv]
        return int(windows_task_scheduler.main())
    finally:
        sys.argv = original_argv


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    mode = raw_args[0] if raw_args else ""
    remaining = raw_args[1:] if raw_args else []
    if mode == "--run-backend":
        return run_backend(remaining)
    if mode == "--run-watcher":
        return run_watcher(remaining)
    if mode == "--task":
        return run_task(remaining)
    if mode in ("-h", "--help"):
        print("Usage: CodexHistorySyncTool.exe [--run-backend|--run-watcher|--task] ...")
        return 0

    import launch_ui_windows

    return int(launch_ui_windows.main())


if __name__ == "__main__":
    raise SystemExit(main())
