# Public Release Checklist

Use this checklist before publishing this project to a public GitHub repository.

## Export Scope

Include:

- `sync_backend.py`
- `launch_ui_windows.py`
- `windows_app.py`
- `scripts/windows_*.py`
- `scripts/windows_*.ps1`
- `scripts/build_windows_*.ps1`
- `installer/CodexHistorySyncTool.iss`
- `installer/languages/ChineseSimplified.isl`
- `docs/windows-upstream-sync-strategy.md`
- `README.md`
- `OPEN_SOURCE_NOTES.md`
- `LICENSE`
- `.gitignore`

Exclude:

- `.git`
- `.codex`
- `.codex-official`
- `history_sync_backups`
- `dist`
- `build`
- `release`
- `.venv-build`
- `*.spec`
- `Codex History Sync Tool.app`
- `AGENTS.md`
- `PROJECT_HISTORY.md`
- `task_plan.md`
- `progress.md`
- `findings.md`
- personal screenshots, logs, and private notes

## Sensitive Scan

Run a scan similar to:

```powershell
rg -n "C:\\Users|F:\\|E:\\|DESKTOP-|auth\\.json|token|API key|refresh|history_sync_backups|state_5\\.sqlite|session_index\\.jsonl|private-vault-name" -S .
```

Any hit must be reviewed before publishing. Generic documentation references to
file names such as `auth.json` are acceptable only when they describe files that
must not be committed.

## Release Artifact Rule

Source commits should not contain generated installers. Put installer files in
GitHub Releases after the public source tree is clean.
