# Codex History Sync Tool for Windows

Codex History Sync Tool for Windows is a local repair utility for Codex Desktop
history visibility issues. It helps preserve local conversation history when
switching between official ChatGPT/OAuth login and third-party or custom API
provider configurations.

The tool works only on local Codex Desktop data. It does not upload chat
history, credentials, tokens, API keys, or provider databases.

## What It Does

- Shows the current Codex provider, model, thread counts, and project state.
- Backs up the local Codex database and sidebar metadata before write actions.
- Synchronizes local thread provider/model metadata to the currently active
  Codex configuration.
- Rebuilds the local session index used by the Codex sidebar.
- Repairs project roots shown in the Codex sidebar.
- Filters transient Codex work directories such as dated temporary workspaces
  so they are not promoted into permanent sidebar projects.
- Provides a Windows desktop launcher and optional Windows auto-sync watcher.
- Provides an Inno Setup installer build flow.

## Typical Use Cases

- Codex Desktop history exists locally but the sidebar looks empty after login
  or provider switching.
- You switched between official ChatGPT login and a third-party/custom provider.
- Sidebar project roots are missing or stale.
- A repair is needed after copying or restoring Codex local state.

## What It Does Not Do

- It does not sync cloud chat records between OpenAI accounts.
- It does not migrate credentials or tokens.
- It does not copy third-party key manager databases.
- It does not recover local history files that were already deleted.
- It is not a replacement for a full machine backup.

## Safety Model

Every write path is designed around local safety:

- Backups are created before sync, restore, and project repair operations.
- Backup manifests record metadata, but credential copying is disabled.
- `auth.json`, OAuth tokens, API keys, refresh tokens, and third-party key
  databases are never copied by the one-click repair flow.
- Project repair keeps durable project roots and drops transient dated Codex
  workspaces from sidebar project lists.

Even with those safeguards, review your Codex home before using any repair tool
against important local data.

## Windows Usage

From source:

```powershell
py -3 .\launch_ui_windows.py
```

Backend status check:

```powershell
py -3 .\sync_backend.py --json status
```

Sync local history to the active provider:

```powershell
py -3 .\sync_backend.py --json sync
```

Repair project roots:

```powershell
py -3 .\sync_backend.py --json project-repair
```

Launcher-compatible one-click safe repair:

```powershell
py -3 .\sync_backend.py --json --one-click-safe-sync --mode auto --close-codex --backup --fix-projects --no-credentials --merge-global-state
```

## Build

Build the portable executable with PyInstaller:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_windows_pyinstaller.ps1
```

Build the installer with Inno Setup:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_windows_installer.ps1
```

Generated artifacts are written under `dist`, `build`, and `release`; these
directories should not be committed.

## Tests

Run the backend smoke test:

```powershell
py -3 .\scripts\windows_backend_smoke_test.py
```

Run packaged and installer smoke tests after building:

```powershell
py -3 .\scripts\windows_packaged_app_smoke_test.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_installer_smoke_test.ps1
```

## Project Files

- `sync_backend.py`: backend sync, backup, restore, and repair logic.
- `launch_ui_windows.py`: Windows GUI launcher.
- `windows_app.py`: Windows app entry point for packaged builds.
- `scripts/windows_auto_sync_watcher.py`: optional Windows auto-sync watcher.
- `scripts/windows_task_scheduler.py`: Windows Task Scheduler integration.
- `installer/CodexHistorySyncTool.iss`: Inno Setup installer script.
- `docs/windows-upstream-sync-strategy.md`: maintenance strategy for tracking
  upstream changes.

## Privacy

Do not publish local Codex data or user-specific records. In particular, keep
these out of public repositories:

- `.codex`
- `.codex-official`
- `state_5.sqlite`
- `session_index.jsonl`
- `sessions`
- `auth.json`
- `config.toml`
- `history_sync_backups`
- local screenshots, logs, or private investigation notes

## Upstream

This project is derived from MIT-licensed Codex history sync work:

- [`GODGOD126/codex-history-sync-tool`](https://github.com/GODGOD126/codex-history-sync-tool)
- [`ruigod1/codex-history-sync-tool-mac`](https://github.com/ruigod1/codex-history-sync-tool-mac)

See [OPEN_SOURCE_NOTES.md](OPEN_SOURCE_NOTES.md) for lineage and publishing
notes.

## License

MIT. See [LICENSE](LICENSE).
