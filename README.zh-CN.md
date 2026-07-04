# Codex History Sync Tool for Windows

[English](README.md) | [中文](README.zh-CN.md)

当前源码版本：`0.3.8-autosync-provider-guard`

最新 Windows 安装包：`v0.3.8-autosync-provider-guard`

下载：
[`Codex-History-Sync-Tool-0.3.8-autosync-provider-guard-Setup.exe`](https://github.com/aizijigao-sketch/codex-history-sync-tool-windows/releases/download/v0.3.8-autosync-provider-guard/Codex-History-Sync-Tool-0.3.8-autosync-provider-guard-Setup.exe)

Release 页面：
[`v0.3.8-autosync-provider-guard`](https://github.com/aizijigao-sketch/codex-history-sync-tool-windows/releases/tag/v0.3.8-autosync-provider-guard)

Codex History Sync Tool for Windows 用于修复 Windows 版 Codex Desktop 的本地聊天可见性、provider/model metadata、侧栏索引、归档索引和项目列表。它适合在官方 ChatGPT/OAuth 登录态、第三方 provider、CCSwitch/custom 路由之间切换后使用。

本工具只处理本机 Codex Desktop 数据。它不上传聊天记录，不迁移 `auth.json`、token、API key、OAuth refresh token 或第三方 key 管理器数据库。

## 与 Codex Windows 启动器的关系

本项目是 [`codex-windows-launcher`](https://github.com/aizijigao-sketch/codex-windows-launcher) 的本地历史修复配套工具。

推荐源码放置方式：

```text
F:\AI-Workspace\20_Projects\codex-windows-launcher
F:\AI-Workspace\20_Projects\codex-history-sync-windows-work
```

职责划分：

- Codex Windows 启动器负责启动/关闭 Codex Desktop 和 CCSwitch、切换本地 profile、选择每个菜单期望的 provider。
- Codex History Sync Tool 负责修复本地聊天可见性、provider/model metadata、`session_index.jsonl`、归档索引和侧栏项目列表。
- 启动器菜单 `1` 期望 provider 为 `openai`；菜单 `2` 期望 provider 为 `custom`。
- 启动器可以在启动 Codex 前调用本工具后端：

```powershell
py -3 .\sync_backend.py --json --expected-provider custom sync
```

如果本工具未安装或启动器找不到它，启动器仍可切换 profile 并启动 Codex，但不能修复本地聊天可见性。

## 需要的软件和配置

普通用户需要：

- Windows 10/11。
- Codex Desktop。
- 本仓库 GitHub Releases 里的最新安装包。
- CCSwitch：只有使用第三方/custom provider 路由时需要。

源码使用或开发需要：

- Python 3。
- PowerShell。
- PyInstaller：构建便携程序时需要。
- Inno Setup 6：构建 Windows 安装包时需要。

配置归属：

- 官方 ChatGPT/OpenAI 登录在 Codex Desktop 和浏览器里完成。
- 第三方 provider、模型映射、Base URL 和 API key 在 CCSwitch 或你的 provider 工具里配置。
- 模式切换和启动顺序交给 Codex Windows 启动器。
- 本工具只负责本地历史可见性修复和项目列表修复。

不要配置或复制：

- 不要跨电脑复制 `auth.json`、`.codex`、`.cc-switch`、OAuth token、API key、refresh token 或 provider 数据库。
- 不要发布真实 `state_5.sqlite`、`session_index.jsonl`、`sessions`、备份目录、截图、日志或私有排查记录。

## 功能

- 显示当前 Codex provider、模型、线程数量和项目状态。
- 写入操作前备份本地 Codex 数据库和侧栏 metadata。
- 将本地 thread provider/model metadata 同步到当前 Codex 配置。
- 一次 `sync` 中多轮修复 provider、metadata、可见性和索引状态。
- 恢复备份后自动做 provider/model 可见性同步。
- 重建 Codex 侧栏使用的本地 `session_index.jsonl`。
- 修复导致已有聊天看起来隐藏的本地可见性标记。
- 修复 Codex 侧栏项目根目录。
- 支持 Windows 桌面 GUI 和低频 auto-sync watcher。
- 支持 `--expected-provider`，让启动器能要求同步目标必须匹配期望 provider。

## Windows 使用

从源码启动 GUI：

```powershell
py -3 .\launch_ui_windows.py
```

后端状态检查：

```powershell
py -3 .\sync_backend.py --json status
```

同步本地历史到当前 provider：

```powershell
py -3 .\sync_backend.py --json sync
```

要求 provider 匹配后再同步：

```powershell
py -3 .\sync_backend.py --json --expected-provider custom sync
```

修复项目列表：

```powershell
py -3 .\sync_backend.py --json project-repair
```

Windows auto-sync 任务：

```powershell
py -3 .\scripts\windows_task_scheduler.py install --json
py -3 .\scripts\windows_task_scheduler.py status --json
py -3 .\scripts\windows_task_scheduler.py uninstall --json
```

## 构建

构建 PyInstaller 程序：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_windows_pyinstaller.ps1
```

构建 Inno Setup 安装包：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_windows_installer.ps1
```

生成产物在 `dist`、`build` 和 `release` 下，这些目录不应提交到源码仓库。

## 测试

```powershell
py -3 .\scripts\windows_backend_smoke_test.py
py -3 .\scripts\windows_packaged_app_smoke_test.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_installer_smoke_test.ps1
```

## 隐私边界

不要把本地 Codex 数据或用户私有记录发布到公开仓库，尤其是：

- `.codex`
- `.codex-official`
- `state_5.sqlite`
- `session_index.jsonl`
- `sessions`
- `auth.json`
- `config.toml`
- `history_sync_backups`
- 本地截图、日志或私有排查记录

## 上游

本项目派生自 MIT 许可的 Codex history sync 相关工作：

- [`GODGOD126/codex-history-sync-tool`](https://github.com/GODGOD126/codex-history-sync-tool)
- [`ruigod1/codex-history-sync-tool-mac`](https://github.com/ruigod1/codex-history-sync-tool-mac)

详见 [OPEN_SOURCE_NOTES.md](OPEN_SOURCE_NOTES.md)。
