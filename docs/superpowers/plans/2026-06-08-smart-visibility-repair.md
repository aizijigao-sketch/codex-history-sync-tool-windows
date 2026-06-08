# Smart Visibility Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add visibility-field repair and low-frequency smart auto repair without implementing high-frequency shared-chat polling.

**Architecture:** Extend `sync_backend.py` in place using existing backup, retry, and sync patterns. Upgrade `scripts/windows_auto_sync_watcher.py` from process-open-only triggering to process-open plus fingerprint-triggered diagnostic sync with debounce and cooldown. Keep the public API conservative: `sync` remains the write repair command and the watcher decides when to call it.

**Tech Stack:** Python standard library, SQLite, PowerShell smoke scripts, existing PyInstaller/Inno Setup packaging.

---

### Task 1: Backend Visibility Fixtures

**Files:**
- Modify: `scripts/windows_backend_smoke_test.py`

- [x] **Step 1: Extend the fixture database**

Add `has_user_event`, `first_user_message`, and `archived_at` columns to the synthetic `threads` table and create a hidden row with `cwd` prefixed by `\\?\`, `has_user_event = 0`, `archived = 1`, and a non-empty `first_user_message`.

- [x] **Step 2: Add archived session metadata**

Create a helper call that writes `archived_sessions/rollout-test-thread-archived.jsonl` with stale `model_provider` and `model`.

- [x] **Step 3: Assert repair behavior**

After `sync`, assert that the hidden row has normalized cwd, `has_user_event = 1`, `archived = 0`, and `archived_at IS NULL`. Assert that archived session metadata is rewritten.

### Task 2: Backend Visibility Repair

**Files:**
- Modify: `sync_backend.py`

- [x] **Step 1: Add `archived_sessions_dir` to `Paths`**

Extend the dataclass and `resolve_paths()` so session scanning can include both active and archived session directories.

- [x] **Step 2: Update `iter_session_paths()`**

Return rollout files from `paths.sessions_dir` and `paths.archived_sessions_dir`.

- [x] **Step 3: Add visibility diagnostics**

Add a helper that counts `cwd` values beginning with `\\?\`, `has_user_event = 0` rows with non-empty `first_user_message`, and `archived != 0` rows.

- [x] **Step 4: Repair visibility fields in `update_provider_assignments()`**

When the relevant columns exist, normalize `cwd`, set `has_user_event = 1`, set `archived = 0`, and clear `archived_at`. Include counts in `visibility_updates`.

- [x] **Step 5: Include visibility pending in `get_status()`**

Add visibility counts and IDs to status. Include visibility IDs in `movable_threads` so watcher and UI see pending work.

### Task 3: Session Index Field Preservation

**Files:**
- Modify: `sync_backend.py`
- Modify: `scripts/windows_backend_smoke_test.py`

- [x] **Step 1: Preserve unknown index fields**

Change `read_session_index()` and `rebuild_session_index()` so existing JSON objects keep unknown keys. Patch only `id`, `thread_name`, and `updated_at`.

- [x] **Step 2: Avoid archived leftovers**

When a database row is known and still archived before repair, do not preserve an index-only copy that would make it linger incorrectly.

- [x] **Step 3: Add smoke assertion**

Add an index entry with an extra field and assert the field survives sync.

### Task 4: Smart Watcher Fingerprints

**Files:**
- Modify: `scripts/windows_auto_sync_watcher.py`
- Modify: `scripts/windows_watcher_smoke_test.py`

- [x] **Step 1: Add fingerprint helpers**

Fingerprint `config.toml`, `state_5.sqlite`, `state_5.sqlite-wal`, `state_5.sqlite-shm`, `session_index.jsonl`, and rollout files under active/archived session directories using path, mtime, and size.

- [x] **Step 2: Add debounce and cooldown arguments**

Add CLI options `--debounce`, `--cooldown`, and `--no-fingerprint`. Keep defaults conservative: debounce 2 seconds, cooldown 60 seconds, fingerprint enabled.

- [x] **Step 3: Trigger diagnostic sync on changed fingerprint**

When Codex is running and fingerprint changes, wait for debounce, run `status`, and call `sync` only when pending work exists and cooldown allows a write.

- [x] **Step 4: Test no duplicate writes**

Add watcher smoke coverage proving two rapid changes produce only one sync call.

### Task 5: Documentation And Release Validation

**Files:**
- Modify: `README.md`
- Modify: `OPEN_SOURCE_NOTES.md`

- [x] **Step 1: Document smart auto repair**

Explain that automatic repair keeps local history visible and does not share chats or credentials.

- [x] **Step 2: Run validation**

Run:

```powershell
py -3 -m py_compile sync_backend.py scripts\windows_auto_sync_watcher.py scripts\windows_backend_smoke_test.py scripts\windows_watcher_smoke_test.py
py -3 .\scripts\windows_backend_smoke_test.py
py -3 .\scripts\windows_watcher_smoke_test.py
```

- [x] **Step 3: Build and installer smoke**

Run the existing packaged app and installer smoke flow before publishing a new Release.
