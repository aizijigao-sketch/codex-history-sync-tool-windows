from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
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

        summary = {
            "ok": True,
            "codex_home": str(codex_home),
            "log_path": str(log_path),
            "final_thread_old": provider_for(codex_home, "thread-old"),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

