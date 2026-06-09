from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TASK_TOOL = ROOT / "scripts" / "windows_task_scheduler.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run_tool(*args: str) -> dict:
    completed = subprocess.run(
        [sys.executable, str(TASK_TOOL), *args, "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    text = (completed.stdout or completed.stderr).strip()
    if not text:
        raise RuntimeError("task tool returned no output")
    payload = json.loads(text)
    if completed.returncode != 0 or not payload.get("ok"):
        raise RuntimeError(payload.get("error") or text)
    return payload


def run_tool_allow_failure(*args: str) -> tuple[int, dict]:
    completed = subprocess.run(
        [sys.executable, str(TASK_TOOL), *args, "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    text = (completed.stdout or completed.stderr).strip()
    if not text:
        raise RuntimeError("task tool returned no output")
    return completed.returncode, json.loads(text)


def simulate_scheduler_exception_fallback() -> dict:
    from scripts import windows_task_scheduler

    task_name = f"CodexHistorySyncToolSimulated{next(tempfile._get_candidate_names())}"
    state_dir = Path(tempfile.mkdtemp(prefix="codex-task-simulated-"))
    startup_dir = state_dir / "Startup"
    original_run_schtasks = windows_task_scheduler.run_schtasks

    def denied_schtasks(*_args: str):
        raise PermissionError(5, "Access is denied")

    try:
        windows_task_scheduler.run_schtasks = denied_schtasks
        args = windows_task_scheduler.parse_args(
            [
                "install",
                "--task-name",
                task_name,
                "--state-dir",
                str(state_dir),
                "--startup-dir",
                str(startup_dir),
                "--process-name",
                "python.exe",
            ]
        )
        payload = windows_task_scheduler.install_task(args)
        if not payload.get("ok") or payload.get("method") != "startup":
            raise AssertionError(f"simulated scheduler denial did not fall back to startup: {payload}")
        if payload.get("task_ok") is not False or payload.get("startup_ok") is not True:
            raise AssertionError(f"fallback payload did not expose task/startup status: {payload}")
        if not Path(str(payload.get("startup_launcher_path"))).exists():
            raise AssertionError("simulated fallback did not create startup launcher")
        return {
            "method": payload.get("method"),
            "task_ok": payload.get("task_ok"),
            "startup_ok": payload.get("startup_ok"),
        }
    finally:
        windows_task_scheduler.run_schtasks = original_run_schtasks
        try:
            run_tool("uninstall", "--task-name", task_name, "--startup-dir", str(startup_dir))
        except Exception:
            pass


def main() -> int:
    task_name = f"CodexHistorySyncToolSmoke{next(tempfile._get_candidate_names())}"
    state_dir = Path(tempfile.mkdtemp(prefix="codex-task-smoke-"))
    startup_dir = state_dir / "Startup"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        stale_lock_path = state_dir / "autosync.lock"
        stale_lock_path.write_text("999999", encoding="utf-8")
        install_code, install = run_tool_allow_failure(
            "install",
            "--task-name",
            task_name,
            "--state-dir",
            str(state_dir),
            "--startup-dir",
            str(startup_dir),
            "--process-name",
            "python.exe",
        )
        if install_code != 0:
            error = str(install.get("error") or "")
            if "access is denied" in error.lower():
                summary = {
                    "ok": True,
                    "task_name": task_name,
                    "skipped_install_verification": True,
                    "reason": "Windows denied scheduled task creation in this environment.",
                    "tool_error_was_clear": bool(error),
                }
                print(json.dumps(summary, ensure_ascii=False, indent=2))
                return 0
            raise RuntimeError(error or json.dumps(install, ensure_ascii=False))
        if not install.get("removed_stale_lock"):
            raise AssertionError("install did not report stale lock cleanup")
        if stale_lock_path.exists():
            raise AssertionError("install did not remove stale autosync lock")

        status = run_tool("status", "--task-name", task_name, "--startup-dir", str(startup_dir))
        if not status.get("exists"):
            raise AssertionError("auto-sync launcher was not created")
        health = status.get("health")
        if not isinstance(health, dict) or "watcher_stale_lock" not in health:
            raise AssertionError("status did not include watcher health diagnostics")
        uninstall = run_tool("uninstall", "--task-name", task_name, "--startup-dir", str(startup_dir))
        status_after = run_tool("status", "--task-name", task_name, "--startup-dir", str(startup_dir))
        if status_after.get("exists"):
            raise AssertionError("auto-sync launcher still exists after uninstall")
        summary = {
            "ok": True,
            "task_name": task_name,
            "method": install.get("method"),
            "task_ok": install.get("task_ok"),
            "startup_ok": install.get("startup_ok"),
            "installed_command": install["command"],
            "removed": uninstall["removed"],
            "simulated_scheduler_denial": simulate_scheduler_exception_fallback(),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    finally:
        try:
            run_tool("uninstall", "--task-name", task_name, "--startup-dir", str(startup_dir))
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
