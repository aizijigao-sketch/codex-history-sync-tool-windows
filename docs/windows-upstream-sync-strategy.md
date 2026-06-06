# Windows Upstream Sync Strategy

## Goal

Keep the Windows complete-experience edition easy to update when the upstream
macOS-focused project changes.

## Principle

Do not fork by rewriting the core sync logic. Keep upstream-owned code as close
to upstream as possible, and put Windows-specific product work in separate files.

## Ownership Boundaries

Upstream-owned or mostly upstream-owned:

- `sync_backend.py`
- core backup, restore, status, and sync behavior
- database and session metadata handling
- license and open-source notices

Windows-owned:

- Windows launcher and GUI shell
- Windows watcher
- Task Scheduler integration
- PyInstaller configuration
- Inno Setup installer
- Windows release scripts
- Windows-specific documentation

Shared, edit carefully:

- `README.md`
- `AGENTS.md`
- release notes

## Recommended Git Model

Use a local Windows branch:

```text
main                  tracks upstream
windows-complete      contains Windows product work
```

When upstream releases a new version:

1. Fetch upstream.
2. Review upstream changes, especially `sync_backend.py`.
3. Merge or rebase upstream `main` into `windows-complete`.
4. Resolve conflicts only inside touched files.
5. Run the Windows smoke test against a temporary Codex home.
6. Build the Windows package.
7. Run installer and uninstall verification.

## Practical Rules

- Keep Windows-specific code out of `sync_backend.py` unless there is no cleaner
  option.
- If `sync_backend.py` needs a compatibility hook, make it small, documented,
  and useful for both macOS and Windows.
- Prefer wrapper modules over invasive edits.
- Keep generated build output out of source control.
- Keep test fixtures synthetic and free of real Codex user data.
- Record the upstream commit or tag used for each Windows release.

## Release Naming

Use upstream version plus a Windows suffix:

```text
v1.6.1-windows.1
v1.6.1-windows.2
v1.6.2-windows.1
```

This makes it clear which upstream version the Windows release is based on.

## Fast Update Checklist

- Upstream tag checked.
- `sync_backend.py` diff reviewed.
- Windows-owned files untouched by upstream merge unless needed.
- Temporary Codex home smoke test passed.
- Packaged EXE starts without Python installed.
- Auto-sync task can be installed and removed.
- Installer uninstall does not delete backups.

