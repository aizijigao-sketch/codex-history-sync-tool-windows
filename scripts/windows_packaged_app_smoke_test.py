from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGED_EXE = ROOT / "dist" / "CodexHistorySyncTool" / "CodexHistorySyncTool.exe"
PACKAGED_CLI_EXE = ROOT / "dist" / "CodexHistorySyncTool" / "CodexHistorySyncToolCli.exe"


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


def run_exe(*args: str) -> dict:
    completed = subprocess.run(
        [str(PACKAGED_CLI_EXE), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    text = (completed.stdout or completed.stderr).strip()
    if not text:
        raise RuntimeError("packaged app returned no output")
    payload = json.loads(text)
    if completed.returncode != 0 or not payload.get("ok"):
        raise RuntimeError(payload.get("error") or text)
    return payload


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
    if not PACKAGED_EXE.exists():
        raise RuntimeError(f"Packaged executable does not exist: {PACKAGED_EXE}")
    if not PACKAGED_CLI_EXE.exists():
        raise RuntimeError(f"Packaged CLI executable does not exist: {PACKAGED_CLI_EXE}")

    temp_root = Path(tempfile.mkdtemp(prefix="codex-packaged-smoke-"))
    codex_home = temp_root / ".codex"
    log_path = temp_root / "autosync.log"
    lock_path = temp_root / "autosync.lock"
    try:
        create_fixture(codex_home)
        status = run_exe("--run-backend", "--codex-home", str(codex_home), "--json", "status")
        if int(status["movable_threads"]) <= 0:
            raise AssertionError("Packaged backend did not detect movable thread")

        sync = run_exe("--run-backend", "--codex-home", str(codex_home), "--json", "sync")
        if int(sync["updated_rows"]) != 1:
            raise AssertionError("Packaged backend did not sync exactly one row")

        create_fixture(codex_home)
        watcher_completed = subprocess.run(
            [
                str(PACKAGED_CLI_EXE),
                "--run-watcher",
                "--backend",
                "__bundled__",
                "--codex-home",
                str(codex_home),
                "--log",
                str(log_path),
                "--lock",
                str(lock_path),
                "--process-name",
                "CodexHistorySyncToolCli.exe",
                "--once",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if watcher_completed.returncode != 0:
            raise RuntimeError(watcher_completed.stderr or watcher_completed.stdout)
        if provider_for(codex_home, "thread-old") != ("openai", "gpt-5"):
            raise AssertionError("Packaged watcher did not update provider/model")

        summary = {
            "ok": True,
            "exe": str(PACKAGED_EXE),
            "backend_updated_rows": sync["updated_rows"],
            "watcher_log_created": log_path.exists(),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
