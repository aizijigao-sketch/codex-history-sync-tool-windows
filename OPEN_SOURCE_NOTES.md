# Open Source Notes

This repository is intended to be publishable as a public Windows-focused
Codex Desktop local history repair tool.

## Upstream Projects

- Original project: `GODGOD126/codex-history-sync-tool`
  - URL: https://github.com/GODGOD126/codex-history-sync-tool
  - Scope: backend sync logic, backup/restore workflow, and early GUI concepts.
- macOS adaptation: `ruigod1/codex-history-sync-tool-mac`
  - URL: https://github.com/ruigod1/codex-history-sync-tool-mac
  - Scope: macOS organization and app packaging concepts.

## Windows Edition Changes

- Added Windows GUI launcher and packaged app entry point.
- Added Windows Task Scheduler based auto-sync watcher support.
- Added Windows PyInstaller and Inno Setup build scripts.
- Added launcher-compatible one-click safe repair flow.
- Added dual-home repair support for official and third-party Codex homes.
- Disabled credential migration in safe repair paths.
- Added provider guard logic so an unavailable `custom` provider is not written
  into official ChatGPT/OAuth configuration.
- Added project root repair logic that excludes transient dated Codex work
  directories from permanent sidebar project lists.
- Added smart visibility repair for archived sessions, hidden user-event flags,
  archived database rows, Windows long-path cwd prefixes, and session-index
  field preservation.
- Upgraded the Windows watcher to a diagnostic-first smart auto-repair model
  with file-change fingerprints, debounce, and cooldown instead of high-frequency
  shared-chat polling.
- Updated the Windows installer to always show the install directory page and
  stop silently reusing the previous install path.
- Added Windows smoke tests for backend, watcher, scheduler, packaged app, and
  installer flows.

## Public Repository Boundary

Public source control may contain:

- Generic source code.
- Synthetic tests and fixtures.
- Installer/build scripts.
- Public documentation.
- Generic icons and license material.

Public source control must not contain:

- Real Codex data directories.
- Real SQLite state databases.
- Real `auth.json`, `config.toml`, tokens, cookies, OAuth data, API keys, or
  third-party key manager databases.
- Real backup directories or backup manifests from a user's machine.
- Local machine names, usernames, private absolute paths, screenshots with
  personal content, or private investigation notes.
- Personal knowledge-vault records.

## Publishing Checklist

Before publishing or pushing a public repository:

1. Export only the allow-listed public files.
2. Exclude build output, installers, app bundles, generated specs, caches, and
   private progress logs unless intentionally preparing a release artifact.
3. Run a repository-wide sensitive text scan for usernames, absolute paths,
   hostnames, tokens, keys, and local Codex data names.
4. Run backend smoke tests with synthetic temporary Codex homes.
5. Publish compiled installers through GitHub Releases, not regular source
   commits.
