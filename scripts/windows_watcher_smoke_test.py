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
    if codex_home.exists():
        shutil.rmtree(codex_home)
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


def set_provider(codex_home: Path, thread_id: str, provider: str, model: str) -> None:
    with sqlite3.connect(codex_home / "state_5.sqlite") as conn:
        conn.execute(
            "UPDATE threads SET model_provider = ?, model = ?, updated_at = updated_at + 10 WHERE id = ?",
            (provider, model, thread_id),
        )
        conn.commit()


def wait_for_log(log_path: Path, needle: str, timeout_seconds: float = 8.0) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if log_path.exists():
            text = log_path.read_text(encoding="utf-8")
            if needle in text:
                return text
        time.sleep(0.1)
    text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    raise AssertionError(f"log did not contain {needle!r}:\n{text}")


def wait_for_log_count(log_path: Path, needle: str, count: int, timeout_seconds: float = 8.0) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if log_path.exists():
            text = log_path.read_text(encoding="utf-8")
            if text.count(needle) >= count:
                return text
        time.sleep(0.1)
    text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    raise AssertionError(f"log did not contain {count} occurrences of {needle!r}:\n{text}")


def main() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="codex-watcher-smoke-"))
    codex_home = temp_root / ".codex"
    log_path = temp_root / "autosync.log"
    lock_path = temp_root / "watcher.lock"
    settings_dir = temp_root / "settings"
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
                "--settings-state-dir",
                str(settings_dir),
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

        detect_only_home = temp_root / ".codex-detect-only"
        create_fixture(detect_only_home)
        lock_path.unlink(missing_ok=True)
        log_path.unlink(missing_ok=True)
        settings_dir.mkdir(parents=True, exist_ok=True)
        (settings_dir / "autosync-settings.json").write_text(
            json.dumps(
                {
                    "auto_detect": True,
                    "auto_fix_chats": True,
                    "auto_fix_projects": False,
                    "dual_home": False,
                    "detect_only": True,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        detect_only = subprocess.run(
            [
                sys.executable,
                str(WATCHER),
                "--backend",
                str(BACKEND),
                "--codex-home",
                str(detect_only_home),
                "--log",
                str(log_path),
                "--lock",
                str(lock_path),
                "--settings-state-dir",
                str(settings_dir),
                "--process-name",
                "python.exe",
                "--once",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if detect_only.returncode != 0:
            raise RuntimeError(f"detect-only watcher failed: {detect_only.stderr or detect_only.stdout}")
        if provider_for(detect_only_home, "thread-old") != ("old-provider", "old-model"):
            raise AssertionError("detect-only mode unexpectedly changed provider/model")
        if "Detect-only mode" not in log_path.read_text(encoding="utf-8"):
            raise AssertionError("detect-only mode was not logged")

        stale_lock_home = temp_root / ".codex-stale-lock"
        create_fixture(stale_lock_home)
        lock_path.write_text("999999", encoding="utf-8")
        log_path.unlink(missing_ok=True)
        (settings_dir / "autosync-settings.json").write_text(
            json.dumps(
                {
                    "auto_detect": True,
                    "auto_fix_chats": True,
                    "auto_fix_projects": False,
                    "dual_home": False,
                    "detect_only": False,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        stale_lock = subprocess.run(
            [
                sys.executable,
                str(WATCHER),
                "--backend",
                str(BACKEND),
                "--codex-home",
                str(stale_lock_home),
                "--log",
                str(log_path),
                "--lock",
                str(lock_path),
                "--settings-state-dir",
                str(settings_dir),
                "--process-name",
                "python.exe",
                "--once",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if stale_lock.returncode != 0:
            raise RuntimeError(f"watcher did not recover from stale lock: {stale_lock.stderr or stale_lock.stdout}")
        if lock_path.exists():
            raise AssertionError("watcher did not remove lock after recovering stale lock")
        if provider_for(stale_lock_home, "thread-old") != ("openai", "gpt-5"):
            raise AssertionError("stale-lock watcher run did not sync provider/model")

        fingerprint_home = temp_root / ".codex-fingerprint"
        create_fixture(fingerprint_home)
        lock_path.unlink(missing_ok=True)
        log_path.unlink(missing_ok=True)
        (settings_dir / "autosync-settings.json").write_text(
            json.dumps(
                {
                    "auto_detect": True,
                    "auto_fix_chats": True,
                    "auto_fix_projects": False,
                    "dual_home": False,
                    "detect_only": False,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        watcher = subprocess.Popen(
            [
                sys.executable,
                str(WATCHER),
                "--backend",
                str(BACKEND),
                "--codex-home",
                str(fingerprint_home),
                "--log",
                str(log_path),
                "--lock",
                str(lock_path),
                "--settings-state-dir",
                str(settings_dir),
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
                "45",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        wait_for_log(log_path, "Auto sync completed")
        if provider_for(fingerprint_home, "thread-old") != ("openai", "gpt-5"):
            raise AssertionError("initial watcher sync did not update provider/model")

        set_provider(fingerprint_home, "thread-old", "old-provider", "old-model")
        (fingerprint_home / "config.toml").write_text(
            'model_provider = "openai"\nmodel = "gpt-5"\n# touched once\n',
            encoding="utf-8",
        )
        wait_for_log(log_path, "Codex files changed: queued smart auto repair check")
        wait_for_log(log_path, "Smart auto repair check")
        wait_for_log_count(log_path, "Auto sync completed", 2, timeout_seconds=8.0)
        if provider_for(fingerprint_home, "thread-old") != ("openai", "gpt-5"):
            raise AssertionError("fingerprint watcher sync did not repair provider/model")

        set_provider(fingerprint_home, "thread-old", "old-provider", "old-model")
        (fingerprint_home / "config.toml").write_text(
            'model_provider = "openai"\nmodel = "gpt-5"\n# touched twice\n',
            encoding="utf-8",
        )
        wait_for_log(log_path, "Smart auto repair delayed: cooldown active")
        try:
            stdout, stderr = watcher.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            watcher.kill()
            stdout, stderr = watcher.communicate(timeout=5)
            raise
        if watcher.returncode != 0:
            raise RuntimeError(f"fingerprint watcher failed: {stderr or stdout}")
        fingerprint_log = log_path.read_text(encoding="utf-8")
        if fingerprint_log.count("Auto sync completed") < 2:
            raise AssertionError(f"fingerprint watcher did not complete at least two syncs:\n{fingerprint_log}")

        summary = {
            "ok": True,
            "codex_home": str(codex_home),
            "log_path": str(log_path),
            "final_thread_old": provider_for(codex_home, "thread-old"),
            "detect_only_preserved": provider_for(detect_only_home, "thread-old") == ("old-provider", "old-model"),
            "fingerprint_syncs": fingerprint_log.count("Auto sync completed"),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
