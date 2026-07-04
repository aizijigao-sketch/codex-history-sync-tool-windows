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
EXPECTED_TOOL_VERSION = "0.3.7-restore-sync-repair"

sys.path.insert(0, str(ROOT))
import sync_backend  # noqa: E402


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
                    "Archived hidden by visibility flags",
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
                    "thread-hidden-active-file",
                    "Active file hidden by visibility flags",
                    "old-provider",
                    "old-model",
                    r"\\?\C:\work\hidden-active",
                    5,
                    1,
                    23456,
                    0,
                    "hidden active hello",
                ),
                (
                    "thread-user-archived",
                    "User archived",
                    "old-provider",
                    "old-model",
                    "C:/work/user-archived",
                    6,
                    1,
                    67890,
                    1,
                    "archived hello",
                ),
                (
                    "thread-archive-drift",
                    "Archived file but active index",
                    "old-provider",
                    "old-model",
                    "C:/work/archive-drift",
                    7,
                    0,
                    None,
                    1,
                    "archive drift hello",
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
    write_session_meta(codex_home, "thread-hidden-active-file", "old-provider", "old-model")
    write_session_meta(codex_home, "thread-archive-drift", "old-provider", "old-model", folder="archived_sessions")
    write_session_meta(codex_home, "thread-archived-index-only", "old-provider", "old-model", folder="archived_sessions")
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
    with (codex_home / "session_index.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "id": "thread-archive-drift",
                    "thread_name": "Archived file but active index",
                    "updated_at": "2026-06-06T00:00:01Z",
                },
                separators=(",", ":"),
            )
            + "\n"
        )
        handle.write(
            json.dumps(
                {
                    "id": "thread-archived-index-only",
                    "thread_name": "Archived index only",
                    "updated_at": "2026-06-06T00:00:02Z",
                },
                separators=(",", ":"),
            )
            + "\n"
        )


def read_session_index_ids(codex_home: Path) -> set[str]:
    index_path = codex_home / "session_index.jsonl"
    return {
        json.loads(line)["id"]
        for line in index_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


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


def create_preserve_auth_custom_fixture(codex_home: Path) -> None:
    create_fixture(codex_home)
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                'model_provider = "custom"',
                'model = "gpt-5"',
                "",
                "[model_providers.custom]",
                'name = "CCSwitch Local Route"',
                'base_url = "http://127.0.0.1:15721/v1"',
                "requires_openai_auth = true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (codex_home / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt"}), encoding="utf-8")


def create_unresolved_provider_fixture(codex_home: Path) -> None:
    create_fixture(codex_home)
    (codex_home / "config.toml").write_text('model = "gpt-5"\n', encoding="utf-8")
    with sqlite3.connect(codex_home / "state_5.sqlite") as conn:
        conn.execute("UPDATE threads SET model_provider = 'custom', model = 'gpt-5'")
        conn.commit()
    auth_path = codex_home / "auth.json"
    if auth_path.exists():
        auth_path.unlink()


def assert_release_metadata() -> None:
    if sync_backend.TOOL_VERSION != EXPECTED_TOOL_VERSION:
        raise AssertionError(
            f"TOOL_VERSION should be {EXPECTED_TOOL_VERSION}, got {sync_backend.TOOL_VERSION}"
        )

    readme = (ROOT / "README.md").read_text(encoding="utf-8-sig")
    if f"Current version: `{EXPECTED_TOOL_VERSION}`" not in readme:
        raise AssertionError("README does not document the current tool version")
    if readme.count("?") > 10 or "????" in readme:
        raise AssertionError("README appears to contain replacement-question-mark mojibake")

    installer = (ROOT / "installer" / "CodexHistorySyncTool.iss").read_text(encoding="utf-8-sig")
    if f'#define MyAppVersion "{EXPECTED_TOOL_VERSION}"' not in installer:
        raise AssertionError("Installer version does not match TOOL_VERSION")


def main() -> int:
    assert_release_metadata()
    temp_root = Path(tempfile.mkdtemp(prefix="codex-history-sync-smoke-"))
    codex_home = temp_root / ".codex"
    try:
        create_fixture(codex_home)

        status_before = run_backend(codex_home, "status")
        if status_before["current_provider"] != "openai":
            raise AssertionError("Current provider detection failed")
        if int(status_before["movable_threads"]) <= 0:
            raise AssertionError("Fixture should have one movable thread")
        if int(status_before["archived_index_mismatch_threads"]) != 2:
            raise AssertionError("Fixture should report two archived index mismatches")

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
        if int(sync_result["sync_rounds"]) < 1:
            raise AssertionError("Sync should report at least one repair round")
        if not sync_result.get("rounds"):
            raise AssertionError("Sync should include per-round repair diagnostics")
        if int(sync_result["updated_rows"]) < 1:
            raise AssertionError("Expected at least one database row to be updated")
        if provider_for(codex_home, "thread-old") != ("openai", "gpt-5"):
            raise AssertionError("Sync did not update provider/model")
        if session_provider_for(codex_home, "thread-old") != ("openai", "gpt-5"):
            raise AssertionError("Sync did not update session_meta provider/model")
        if session_provider_for(codex_home, "thread-hidden") != ("openai", "gpt-5"):
            raise AssertionError("Sync did not update archived session_meta provider/model")
        hidden_cwd, hidden_user_event, hidden_archived, hidden_archived_at = thread_visibility_for(
            codex_home, "thread-hidden"
        )
        if hidden_archived != 1 or hidden_archived_at is None:
            raise AssertionError("Sync should keep files in archived_sessions archived")
        hidden_active_cwd, hidden_active_user_event, hidden_active_archived, hidden_active_archived_at = (
            thread_visibility_for(codex_home, "thread-hidden-active-file")
        )
        if (
            hidden_active_cwd != "C:\\work\\hidden-active"
            or hidden_active_user_event != 1
            or hidden_active_archived != 0
            or hidden_active_archived_at is not None
        ):
            raise AssertionError("Sync did not repair hidden active-file thread visibility flags")
        user_archived = thread_visibility_for(codex_home, "thread-user-archived")
        if user_archived != ("C:/work/user-archived", 1, 1, 67890):
            raise AssertionError("Sync should not unarchive a normal user-archived thread")
        drift = thread_visibility_for(codex_home, "thread-archive-drift")
        if drift[2] != 1 or drift[3] is None:
            raise AssertionError("Sync should mark archived session files as archived in the database")
        index_rows = [
            json.loads(line)
            for line in (codex_home / "session_index.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        current_index = next((row for row in index_rows if row.get("id") == "thread-current"), None)
        if current_index is None or current_index.get("extra_field") != "keep-me":
            raise AssertionError("Rebuilt session_index.jsonl did not preserve unknown fields")
        if "thread-archive-drift" in read_session_index_ids(codex_home):
            raise AssertionError("Rebuilt session_index.jsonl should exclude archived session files")
        if "thread-archived-index-only" in read_session_index_ids(codex_home):
            raise AssertionError("Rebuilt session_index.jsonl should exclude archived index-only files")
        status_after_sync = run_backend(codex_home, "status")
        if int(status_after_sync["archived_index_mismatch_threads"]) != 0:
            raise AssertionError("Sync should leave no archived index mismatches after one run")
        if int(status_after_sync["movable_threads"]) != 0:
            raise AssertionError("Sync should leave no pending thread/index work after one command")
        if int(status_after_sync["movable_session_meta_entries"]) != 0:
            raise AssertionError("Sync should leave no pending session metadata work after one command")

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
        sync_after_restore = restore_result.get("sync_after_restore") or {}
        if sync_after_restore.get("ok") is False:
            raise AssertionError(f"Restore follow-up sync failed: {sync_after_restore.get('error')}")
        if int(sync_after_restore.get("sync_rounds") or 0) < 1:
            raise AssertionError("Restore should run a follow-up sync round")
        if provider_for(codex_home, "thread-old") != ("openai", "gpt-5"):
            raise AssertionError("Restore did not adapt restored provider/model to the current provider")

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

        unresolved_home = temp_root / ".codex-unresolved"
        create_unresolved_provider_fixture(unresolved_home)
        unresolved_status = run_backend(unresolved_home, "status")
        if unresolved_status["current_provider"] != "" or not unresolved_status.get("provider_resolution_error"):
            raise AssertionError("Unresolved provider status should succeed with a provider_resolution_error")
        returncode, unresolved_sync = run_backend_raw(unresolved_home, "sync")
        if returncode == 0 or unresolved_sync.get("ok"):
            raise AssertionError("Unresolved provider sync should fail instead of writing empty provider")
        if "当前无法判断 Codex 正在使用哪个 provider" not in str(unresolved_sync.get("error")):
            raise AssertionError("Unresolved provider sync error should be user-facing Chinese")

        preserve_home = temp_root / ".codex-preserve-auth"
        create_preserve_auth_custom_fixture(preserve_home)
        expected_status = run_backend(preserve_home, "--expected-provider", "custom", "status")
        if expected_status["current_provider"] != "custom":
            raise AssertionError("Expected-provider status should prefer configured custom provider over ChatGPT auth")
        expected_sync = run_backend(preserve_home, "--expected-provider", "custom", "sync")
        if expected_sync["current_provider"] != "custom":
            raise AssertionError("Expected-provider sync should target custom provider")
        expected_after = run_backend(preserve_home, "--expected-provider", "custom", "status")
        if int(expected_after["movable_threads"]) != 0:
            raise AssertionError("Expected-provider sync should leave no movable threads")

        final_status = run_backend(codex_home, "status")
        if not final_status.get("login_mode") or "project_diagnostics" not in final_status:
            raise AssertionError("Status did not include login mode or project diagnostics")
        summary = {
            "ok": True,
            "codex_home": str(codex_home),
            "status_before_movable_threads": status_before["movable_threads"],
            "sync_updated_rows": sync_result["updated_rows"],
            "sync_rounds": sync_result["sync_rounds"],
            "restore_from": restore_result["restored_from"],
            "final_movable_threads": final_status["movable_threads"],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
