from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "sync_backend.py"
WATCHER = ROOT / "scripts" / "windows_auto_sync_watcher.py"


def create_fixture(codex_home: Path) -> None:
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / "config.toml").write_text(
        'model_provider = "openai"\nmodel = "gpt-5"\n',
        encoding="utf-8",
    )
    with sqlite3.connect(codex_home / "state_5.sqlite") as conn:
        conn.execute(
            """
            CREATE TABLE threads (
              id TEXT PRIMARY KEY,
              title TEXT,
              model_provider TEXT,
              model TEXT,
              cwd TEXT,
              updated_at INTEGER,
              archived INTEGER DEFAULT 0
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO threads
              (id, title, model_provider, model, cwd, updated_at, archived)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("thread-current", "Current provider", "openai", "gpt-5", "C:/work/current", 1, 0),
                ("thread-old", "Old provider", "old-provider", "old-model", "C:/work/old", 2, 0),
            ],
        )
        conn.commit()


def provider_for(codex_home: Path, thread_id: str) -> tuple[str, str]:
    with sqlite3.connect(codex_home / "state_5.sqlite") as conn:
        row = conn.execute(
            "SELECT model_provider, model FROM threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"Missing fixture row: {thread_id}")
    return str(row[0]), str(row[1])


def reset_old_provider(codex_home: Path) -> None:
    with sqlite3.connect(codex_home / "state_5.sqlite") as conn:
        conn.execute(
            "UPDATE threads SET model_provider = 'old-provider', model = 'old-model' WHERE id = 'thread-old'"
        )
        conn.commit()
    config_path = codex_home / "config.toml"
    config_path.write_text(config_path.read_text(encoding="utf-8") + "# touched by watcher smoke\n", encoding="utf-8")


def wait_for_log(path: Path, pattern: str, timeout_seconds: float = 10.0) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if pattern in text:
                return text
        time.sleep(0.1)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def main() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="codex-watcher-smoke-"))
    codex_home = temp_root / ".codex"
    log_path = temp_root / "autosync.log"
    lock_path = temp_root / "watcher.lock"
    try:
        create_fixture(codex_home)

        completed = subprocess.run(
            [
                sys.executable,
                str(WATCHER),
                "--backend",
                str(BACKEND),
                "--codex-home",
                str(codex_home),
                "--log",
                str(log_path),
                "--lock",
                str(lock_path),
                "--process-name",
                "python.exe",
                "--once",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"watcher failed: {completed.stderr or completed.stdout}")
        if not log_path.exists():
            raise AssertionError("watcher log was not created")
        log_text = log_path.read_text(encoding="utf-8")
        if "Auto sync completed" not in log_text:
            raise AssertionError(f"watcher did not sync:\n{log_text}")
        if provider_for(codex_home, "thread-old") != ("openai", "gpt-5"):
            raise AssertionError("watcher did not update provider/model")

        reset_old_provider(codex_home)
        smart_log_path = temp_root / "smart-autosync.log"
        smart_lock_path = temp_root / "smart-watcher.lock"
        process = subprocess.Popen(
            [
                sys.executable,
                str(WATCHER),
                "--backend",
                str(BACKEND),
                "--codex-home",
                str(codex_home),
                "--log",
                str(smart_log_path),
                "--lock",
                str(smart_lock_path),
                "--process-name",
                "python.exe",
                "--poll",
                "0.1",
                "--initial-delay",
                "0.1",
                "--debounce",
                "0.1",
                "--cooldown",
                "1.0",
                "--max-cycles",
                "80",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            first_log = wait_for_log(smart_log_path, "Auto sync completed", timeout_seconds=10)
            if "Auto sync completed" not in first_log:
                raise AssertionError(f"smart watcher did not perform initial sync:\n{first_log}")
            reset_old_provider(codex_home)
            stdout, stderr = process.communicate(timeout=30)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        if process.returncode != 0:
            raise RuntimeError(f"smart watcher failed: {stderr or stdout}")
        smart_log_text = smart_log_path.read_text(encoding="utf-8")
        if "Codex files changed: queued smart auto repair check" not in smart_log_text:
            raise AssertionError(f"smart watcher did not detect file changes:\n{smart_log_text}")
        if "Smart auto repair delayed: cooldown active" not in smart_log_text:
            raise AssertionError(f"smart watcher did not preserve a cooldown-delayed repair:\n{smart_log_text}")
        if smart_log_text.count("Auto sync completed") < 2:
            raise AssertionError(f"smart watcher did not run a second repair:\n{smart_log_text}")
        if provider_for(codex_home, "thread-old") != ("openai", "gpt-5"):
            raise AssertionError("smart watcher did not repair provider/model after file change")

        summary = {
            "ok": True,
            "codex_home": str(codex_home),
            "log_path": str(log_path),
            "smart_log_path": str(smart_log_path),
            "final_thread_old": provider_for(codex_home, "thread-old"),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
