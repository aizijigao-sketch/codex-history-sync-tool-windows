# Smart Visibility Repair Design

## Goal

Improve Codex History Sync Tool so it repairs the visibility conditions that make local Codex Desktop chats disappear after provider/model switching, while avoiding high-frequency polling and avoiding cross-home chat copying.

## Product Decision

The feature is named "smart visibility repair" rather than "share mode" because it does not share cloud chats, sync between accounts, or copy conversations across machines. It keeps local Codex history visible by repairing metadata and indexes when the current local Codex configuration changes.

The tool must not copy credentials, `auth.json`, OAuth tokens, API keys, refresh tokens, third-party key databases, or chat files between `.codex` and `.codex-official`.

## Backend Scope

The backend will extend existing sync behavior to cover additional visibility fields:

- Scan both `sessions/**/*.jsonl` and `archived_sessions/**/*.jsonl`.
- Backup and restore first-line `session_meta` for both active and archived session files.
- Detect and repair Windows `\\?\` cwd prefixes in `threads.cwd`.
- Detect and repair `threads.has_user_event = 0` when `first_user_message` is present.
- Detect and repair archived rows only when they also match a local visibility anomaly, then set `archived = 0` and clear `archived_at`.
- Preserve unknown fields in `session_index.jsonl` entries when rebuilding the index.
- Avoid preserving index-only entries that correspond to known archived database rows after repair.

## Smart Watcher Scope

The Windows watcher remains low-frequency and diagnostic-first:

- It keeps the existing Codex process-open trigger.
- It additionally checks modification fingerprints for `config.toml`, `state_5.sqlite`, `state_5.sqlite-wal`, `state_5.sqlite-shm`, `session_index.jsonl`, and top-level metadata under `sessions` and `archived_sessions`.
- It debounces changed fingerprints before running backend status.
- It only runs `sync` when backend status reports pending work.
- It enforces per-home cooldown before another write repair.
- If cooldown delays a changed-file check, it keeps the pending check queued and retries after cooldown.
- It records reasons and skip decisions in the watcher log.

This is not a `ReadDirectoryChangesW` implementation in the first pass. A polling fingerprint is simpler, has no external dependency, and avoids PyInstaller packaging risk. The poll remains light because it compares file stats and only calls backend status after a meaningful fingerprint change.

## User Interface And Documentation

Documentation should call the feature "smart auto repair" or "auto keep history visible." Avoid "shared chat" wording. UI text should distinguish:

- Manual repair.
- Smart auto repair.
- Advanced continuous guard mode, which is intentionally not implemented in this release.

## Safety

Every write repair keeps the existing backup-first behavior. The watcher must avoid repeated backups by checking pending work and cooldown before calling `sync`. SQLite lock failures should be logged and retried later by the next watcher cycle, not converted into aggressive tight-loop retries.

## Validation

Validation requires:

- Backend smoke fixtures for `archived_sessions`, `has_user_event`, `archived`, `archived_at`, Windows `\\?\` prefixes, and index field preservation.
- Watcher smoke fixtures for fingerprint-triggered sync, debounce/cooldown behavior, and no-sync when there is no pending work.
- Existing packaged and installer smoke tests after implementation.
