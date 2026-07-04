# Codex History Sync Tool for Windows

[中文](README.md) | **English (current)**

Current source version: `0.3.8-autosync-provider-guard`

Latest Windows installer release: `v0.3.8-autosync-provider-guard`

Download:
[`Codex-History-Sync-Tool-0.3.8-autosync-provider-guard-Setup.exe`](https://github.com/aizijigao-sketch/codex-history-sync-tool-windows/releases/download/v0.3.8-autosync-provider-guard/Codex-History-Sync-Tool-0.3.8-autosync-provider-guard-Setup.exe)

Release page:
[`v0.3.8-autosync-provider-guard`](https://github.com/aizijigao-sketch/codex-history-sync-tool-windows/releases/tag/v0.3.8-autosync-provider-guard)

Codex History Sync Tool for Windows is a local repair utility for Codex Desktop
history visibility issues. It helps preserve local conversation history when
switching between official ChatGPT/OAuth login and third-party or custom API
provider configurations.

The tool works only on local Codex Desktop data. It does not upload chat
history, credentials, tokens, API keys, or provider databases.


## Relationship With Codex Windows Launcher

This project is the history repair companion for
[`codex-windows-launcher`](https://github.com/aizijigao-sketch/codex-windows-launcher).

Recommended source layout on the maintainer machine:

```text
F:\AI-Workspace\20_Projects\codex-windows-launcher
F:\AI-Workspace\20_Projects\codex-history-sync-windows-work
```

Responsibility split:

- Codex Windows Launcher starts/stops Codex Desktop and CCSwitch, switches local
  launcher profiles, and chooses the expected provider for each menu mode.
- Codex History Sync Tool repairs local history visibility, provider/model
  metadata, `session_index.jsonl`, archived-session index state, and sidebar
  project roots.
- Launcher menu `1` expects provider `openai`; launcher menu `2` expects
  provider `custom`.
- The launcher can call this backend before starting Codex:

```powershell
py -3 .\sync_backend.py --json --expected-provider custom sync
```

If this tool is not installed or not discoverable, the launcher can still switch
profiles and start Codex, but it cannot repair local history visibility.

## Required Software And Configuration

For normal users:

- Windows 10/11.
- Codex Desktop.
- The latest installer from this repository's GitHub Releases.
- CCSwitch only when using third-party/custom provider routing.

For source usage or development:

- Python 3.
- PowerShell.
- PyInstaller for packaged builds.
- Inno Setup 6 for Windows installer builds.

Configuration ownership:

- Configure official ChatGPT/OpenAI login inside Codex Desktop and your browser.
- Configure third-party provider, model mapping, Base URL, and API key inside
  CCSwitch or your provider tool.
- Use Codex Windows Launcher for mode switching and launch order.
- Use this tool for local history visibility repair and project-list repair.

Do not configure or copy:

- Do not copy `auth.json`, `.codex`, `.cc-switch`, OAuth tokens, API keys,
  refresh tokens, or provider databases between computers.
- Do not publish real `state_5.sqlite`, `session_index.jsonl`, `sessions`,
  backup directories, screenshots, logs, or private investigation notes.

## What It Does

- Shows the current Codex provider, model, thread counts, and project state.
- Backs up the local Codex database and sidebar metadata before write actions.
- Synchronizes local thread provider/model metadata to the currently active
  Codex configuration.
- Runs repeated repair rounds in one sync command until provider, metadata,
  visibility, and sidebar index state are clean or a busy active session file
  must be retried later.
- Runs a provider/model visibility sync after backup restore so restored chats
  are adapted to the currently active Codex configuration immediately.
- Rebuilds the local session index used by the Codex sidebar.
- Repairs local visibility flags that can make existing chats look hidden after
  provider or login switching.
- Repairs project roots shown in the Codex sidebar.
- Filters transient Codex work directories such as dated temporary workspaces
  so they are not promoted into permanent sidebar projects.
- Provides a Windows desktop launcher and optional low-frequency Windows
  auto-sync watcher.
- Lets launchers pass `--expected-provider` so status and sync can require a
  specific configured provider instead of silently trusting whichever provider
  is active at that moment.
- Persists Windows auto-sync settings for detect-only mode, chat repair,
  project repair, and dual-home repair.
- Reports auto-sync health issues such as stale watcher locks or old logs.
- Skips temporarily busy active session files during background repair instead
  of failing the whole watcher run.
- Provides an Inno Setup installer build flow with a selectable install path.

## Typical Use Cases

- Codex Desktop history exists locally but the sidebar looks empty after login
  or provider switching.
- You switched between official ChatGPT login and a third-party/custom provider.
- Sidebar project roots are missing or stale.
- A repair is needed after copying or restoring Codex local state.
- Restored backups need to be adapted to the provider/model currently selected
  in Codex Desktop.

## What It Does Not Do

- It does not sync cloud chat records between OpenAI accounts.
- It does not run a high-frequency shared-chat polling service.
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

Sync local history only when the active configuration can target an expected
provider:

```powershell
py -3 .\sync_backend.py --json --expected-provider custom sync
```

Repair project roots:

```powershell
py -3 .\sync_backend.py --json project-repair
```

Launcher-compatible one-click safe repair:

```powershell
py -3 .\sync_backend.py --json --one-click-safe-sync --mode auto --close-codex --backup --fix-projects --no-credentials --merge-global-state
```

Windows auto-sync task controls:

```powershell
py -3 .\scripts\windows_task_scheduler.py install --json
py -3 .\scripts\windows_task_scheduler.py status --json
py -3 .\scripts\windows_task_scheduler.py uninstall --json
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
