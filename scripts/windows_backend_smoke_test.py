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


def run_backend(codex_home: Path, *args: str) -> dict:
    completed = subprocess.run(
        [sys.executable, str(BACKEND), "--codex-home", str(codex_home), "--json", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    output = (completed.stdout or completed.stderr).strip()
    if not output:
        raise RuntimeError(f"Backend returned no output for: {' '.join(args)}")
    payload = json.loads(output)
    if completed.returncode != 0 or not payload.get("ok"):
        raise RuntimeError(payload.get("error") or output)
    return payload


def run_backend_raw(codex_home: Path, *args: str) -> tuple[int, dict]:
    completed = subprocess.run(
        [sys.executable, str(BACKEND), "--codex-home", str(codex_home), "--json", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    output = (completed.stdout or completed.stderr).strip()
    if not output:
        raise RuntimeError(f"Backend returned no output for: {' '.join(args)}")
    return completed.returncode, json.loads(output)


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
              archived INTEGER DEFAULT 0,
              archived_at INTEGER,
              has_user_event INTEGER DEFAULT 1,
              first_user_message TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO threads
              (id, title, model_provider, model, cwd, updated_at, archived, archived_at, has_user_event, first_user_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("thread-current", "Current provider", "openai", "gpt-5", "C:/work/current", 1, 0, None, 1, "hello"),
                ("thread-old", "Old provider", "old-provider", "old-model", "C:/work/old", 2, 0, None, 1, "old hello"),
                (
                    "thread-transient",
                    "Transient Codex workspace",
                    "old-provider",
                    "old-model",
                    str(Path.home() / "Documents" / "Codex" / "2026-06-06" / "new-chat"),
                    3,
                    0,
                    None,
                    1,
                    "transient hello",
                ),
                (
                    "thread-hidden",
                    "Hidden by visibility flags",
                    "old-provider",
                    "old-model",
                    r"\\?\C:\work\hidden",
                    4,
                    1,
                    12345,
                    0,
                    "hidden hello",
                ),
                (
                    "thread-user-archived",
                    "User archived",
                    "old-provider",
                    "old-model",
                    "C:/work/user-archived",
                    5,
                    1,
                    67890,
                    1,
                    "archived hello",
                ),
            ],
        )
        conn.commit()
    (codex_home / ".codex-global-state.json").write_text(
        json.dumps(
            {
                "electron-saved-workspace-roots": ["C:/work/current"],
                "project-order": ["C:/work/current"],
                "project-writable-roots": ["C:/work/current"],
                "local-projects": [{"path": "C:/work/current"}, {"path": "C:/work/legacy-local"}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    write_session_meta(codex_home, "thread-old", "old-provider", "old-model")
    write_session_meta(codex_home, "thread-hidden", "old-provider", "old-model", folder="archived_sessions")
    (codex_home / "session_index.jsonl").write_text(
        json.dumps(
            {
                "id": "thread-current",
                "thread_name": "Current provider",
                "updated_at": "2026-06-06T00:00:00Z",
                "extra_field": "keep-me",
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def write_session_meta(codex_home: Path, thread_id: str, provider: str, model: str, folder: str = "sessions") -> None:
    session_dir = codex_home / folder
    session_dir.mkdir(parents=True, exist_ok=True)
    session_path = session_dir / f"rollout-test-{thread_id}.jsonl"
    payload = {
        "type": "session_meta",
        "payload": {
            "id": thread_id,
            "model_provider": provider,
            "model": model,
        },
    }
    session_path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")


def provider_for(codex_home: Path, thread_id: str) -> tuple[str, str]:
    with sqlite3.connect(codex_home / "state_5.sqlite") as conn:
        row = conn.execute(
            "SELECT model_provider, model FROM threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"Missing fixture row: {thread_id}")
    return str(row[0]), str(row[1])


def session_provider_for(codex_home: Path, thread_id: str) -> tuple[str, str]:
    session_path = codex_home / "sessions" / f"rollout-test-{thread_id}.jsonl"
    if not session_path.exists():
        session_path = codex_home / "archived_sessions" / f"rollout-test-{thread_id}.jsonl"
    item = json.loads(session_path.read_text(encoding="utf-8").splitlines()[0])
    payload = item["payload"]
    return str(payload["model_provider"]), str(payload["model"])


def thread_visibility_for(codex_home: Path, thread_id: str) -> tuple[str, int, int, object | None]:
    with sqlite3.connect(codex_home / "state_5.sqlite") as conn:
        row = conn.execute(
            "SELECT cwd, has_user_event, archived, archived_at FROM threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"Missing fixture row: {thread_id}")
    return str(row[0]), int(row[1]), int(row[2]), row[3]


def read_global_state(codex_home: Path) -> dict:
    return json.loads((codex_home / ".codex-global-state.json").read_text(encoding="utf-8"))


def create_invalid_custom_fixture(codex_home: Path, chatgpt_auth: bool) -> None:
    create_fixture(codex_home)
    (codex_home / "config.toml").write_text(
        'model_provider = "custom"\nmodel = "gpt-5"\n',
        encoding="utf-8",
    )
    if chatgpt_auth:
        (codex_home / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt"}), encoding="utf-8")
    with sqlite3.connect(codex_home / "state_5.sqlite") as conn:
        conn.execute("UPDATE threads SET model_provider = 'custom', model = 'gpt-5'")
        conn.commit()
    write_session_meta(codex_home, "thread-old", "custom", "gpt-5")


def main() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="codex-history-sync-smoke-"))
    codex_home = temp_root / ".codex"
    try:
        create_fixture(codex_home)

        status_before = run_backend(codex_home, "status")
        if status_before["current_provider"] != "openai":
            raise AssertionError("Current provider detection failed")
        if int(status_before["movable_threads"]) <= 0:
            raise AssertionError("Fixture should have one movable thread")

        manual_backup = run_backend(codex_home, "backup")
        backup_path = Path(manual_backup["backup_path"])
        if not backup_path.exists() or not (backup_path / "manifest.json").exists():
            raise AssertionError("Manual backup directory or manifest was not created")
        manifest = json.loads((backup_path / "manifest.json").read_text(encoding="utf-8"))
        if manifest["targetProvider"] != "openai" or manifest["projectRootCount"] < 2:
            raise AssertionError("Backup manifest did not capture provider/project metadata")
        details = run_backend(codex_home, "backup-details", "--backup", str(backup_path))
        if not details.get("contains_database") or not details.get("contains_global_state"):
            raise AssertionError("Backup details did not report expected backup contents")
        update = run_backend(codex_home, "backup-update", "--backup", str(backup_path), "--notes", "smoke note")
        if update["manifest"].get("notes") != "smoke note":
            raise AssertionError("Backup notes were not updated")

        sync_result = run_backend(codex_home, "sync")
        if int(sync_result["updated_rows"]) < 1:
            raise AssertionError("Expected at least one database row to be updated")
        if provider_for(codex_home, "thread-old") != ("openai", "gpt-5"):
            raise AssertionError("Sync did not update provider/model")
        if session_provider_for(codex_home, "thread-old") != ("openai", "gpt-5"):
            raise AssertionError("Sync did not update session_meta provider/model")
        if session_provider_for(codex_home, "thread-hidden") != ("openai", "gpt-5"):
            raise AssertionError("Sync did not update archived session_meta provider/model")
        hidden_cwd, hidden_has_user_event, hidden_archived, hidden_archived_at = thread_visibility_for(codex_home, "thread-hidden")
        if hidden_cwd != "C:\\work\\hidden" or hidden_has_user_event != 1 or hidden_archived != 0 or hidden_archived_at is not None:
            raise AssertionError("Sync did not repair hidden visibility flags")
        archived_cwd, archived_has_user_event, archived_archived, archived_archived_at = thread_visibility_for(
            codex_home,
            "thread-user-archived",
        )
        if (
            archived_cwd != "C:/work/user-archived"
            or archived_has_user_event != 1
            or archived_archived != 1
            or archived_archived_at != 67890
        ):
            raise AssertionError("Sync should not unarchive a normal user-archived thread")
        index_entries = [
            json.loads(line)
            for line in (codex_home / "session_index.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        current_entry = next(item for item in index_entries if item["id"] == "thread-current")
        if current_entry.get("extra_field") != "keep-me":
            raise AssertionError("Session index rebuild did not preserve unknown fields")

        repair_result = run_backend(codex_home, "project-repair")
        state = read_global_state(codex_home)
        if "C:/work/old" not in state.get("electron-saved-workspace-roots", []):
            raise AssertionError("Project repair did not add thread cwd to saved workspace roots")
        if any("Documents\\Codex\\2026-06-06\\new-chat" in root for root in state.get("electron-saved-workspace-roots", [])):
            raise AssertionError("Project repair should not add transient Codex workspace roots")
        if any(item.get("path") == "C:/work/current" for item in state.get("local-projects", [])):
            raise AssertionError("Project repair did not remove duplicate local-projects entry")
        if not repair_result.get("safety_backup"):
            raise AssertionError("Project repair did not create a safety backup")

        delete_backup = run_backend(codex_home, "backup")
        delete_backup_path = Path(delete_backup["backup_path"])
        delete_result = run_backend(codex_home, "backup-delete", "--backup", str(delete_backup_path))
        if delete_backup_path.exists() or not delete_result.get("deleted"):
            raise AssertionError("Backup delete did not remove the selected backup")

        restore_result = run_backend(codex_home, "restore", "--backup", str(backup_path))
        if Path(restore_result["restored_from"]) != backup_path:
            raise AssertionError("Restore did not use the selected backup")
        if provider_for(codex_home, "thread-old") != ("old-provider", "old-model"):
            raise AssertionError("Restore did not restore original provider/model")

        chatgpt_home = temp_root / ".codex-invalid-chatgpt"
        create_invalid_custom_fixture(chatgpt_home, chatgpt_auth=True)
        chatgpt_status = run_backend(chatgpt_home, "status")
        if chatgpt_status["current_provider"] != "openai":
            raise AssertionError("ChatGPT invalid custom provider should fall back to openai")
        run_backend(chatgpt_home, "sync")
        if provider_for(chatgpt_home, "thread-old") != ("openai", "gpt-5"):
            raise AssertionError("Invalid custom provider was not repaired to openai in database")
        if session_provider_for(chatgpt_home, "thread-old") != ("openai", "gpt-5"):
            raise AssertionError("Invalid custom provider was not repaired to openai in session_meta")

        api_home = temp_root / ".codex-invalid-api"
        create_invalid_custom_fixture(api_home, chatgpt_auth=False)
        returncode, error_payload = run_backend_raw(api_home, "sync")
        if returncode == 0 or error_payload.get("ok"):
            raise AssertionError("Invalid custom provider without ChatGPT auth should fail instead of syncing")
        if "model_provider = \"custom\"" not in str(error_payload.get("error")):
            raise AssertionError("Invalid custom provider error message was not explanatory")
        if provider_for(api_home, "thread-old") != ("custom", "gpt-5"):
            raise AssertionError("Failed invalid custom sync should not rewrite database rows")

        final_status = run_backend(codex_home, "status")
        if not final_status.get("login_mode") or "project_diagnostics" not in final_status:
            raise AssertionError("Status did not include login mode or project diagnostics")
        summary = {
            "ok": True,
            "codex_home": str(codex_home),
            "status_before_movable_threads": status_before["movable_threads"],
            "sync_updated_rows": sync_result["updated_rows"],
            "restore_from": restore_result["restored_from"],
            "final_movable_threads": final_status["movable_threads"],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
