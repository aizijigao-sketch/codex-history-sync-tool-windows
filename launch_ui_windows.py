from __future__ import annotations

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

import sync_backend
from scripts import windows_task_scheduler


class WindowsSyncApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Codex 历史同步工具")
        self.geometry("1120x760")
        self.minsize(1040, 700)
        self.latest_status: dict | None = None
        self.backup_rows: list[dict] = []

        self.provider_var = tk.StringVar(value="未刷新")
        self.login_var = tk.StringVar(value="未刷新")
        self.model_var = tk.StringVar(value="未刷新")
        self.history_var = tk.StringVar(value="未刷新")
        self.project_var = tk.StringVar(value="未刷新")
        self.safety_var = tk.StringVar(value="恢复和修复前请先完全退出 Codex Desktop")
        self.path_var = tk.StringVar(value="数据库: 未刷新")
        self.autosync_headline_var = tk.StringVar(value="检查中")
        self.autosync_detail_var = tk.StringVar(value="正在读取后台同步状态")
        self.autosync_status_var = tk.StringVar(value="自动同步状态：正在检查...")
        self.autosync_help_var = tk.StringVar(
            value="开启后不需要每次打开本工具。Windows 登录后会在后台运行；本工具关闭后仍会继续按后台规则同步。"
        )
        self.autosync_method_var = tk.StringVar(value="启用方式：未读取")
        self.autosync_next_var = tk.StringVar(value="生效时机：未读取")

        self._build_ui()
        self.refresh_state_async()

    def _build_ui(self) -> None:
        self.configure(background="#f4f6f8")
        style = ttk.Style(self)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"), background="#f4f6f8")
        style.configure("Hint.TLabel", font=("Microsoft YaHei UI", 10), background="#f4f6f8", foreground="#8a4b22")
        style.configure("Card.TLabelframe", padding=10)
        style.configure("Card.TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("StatusValue.TLabel", font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("StatusSubtle.TLabel", font=("Microsoft YaHei UI", 9), foreground="#5f6b7a")
        style.configure("Primary.TButton", padding=(12, 6))
        style.configure("Guide.TLabelframe", padding=10)

        root = ttk.Frame(self, padding=18)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)

        ttk.Label(root, text="Codex 历史同步工具", style="Title.TLabel").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(
            root,
            text="给第一次使用的人看的历史同步、备份恢复、项目列表修复工具。默认不迁移登录凭据、Token、API key 或 CC Switch 数据库。",
            style="Hint.TLabel",
        ).grid(row=1, column=0, sticky=tk.W, pady=(6, 14))

        self._build_status_cards(root).grid(row=2, column=0, sticky=tk.EW, pady=(0, 12))

        notebook = ttk.Notebook(root)
        notebook.grid(row=3, column=0, sticky=tk.NSEW)
        notebook.add(self._build_guide_tab(notebook), text="新手向导")
        notebook.add(self._build_actions_tab(notebook), text="同步与修复")
        notebook.add(self._build_backups_tab(notebook), text="备份管理")
        notebook.add(self._build_diagnostics_tab(notebook), text="诊断日志")

    def _build_status_cards(self, parent: ttk.Frame) -> ttk.Frame:
        frame = ttk.Frame(parent)
        for index in range(4):
            frame.columnconfigure(index, weight=1, uniform="cards")

        cards = [
            ("当前账号/通道", self.provider_var),
            ("登录方式", self.login_var),
            ("当前模型", self.model_var),
            ("历史与项目", self.history_var),
        ]
        for index, (title, variable) in enumerate(cards):
            card = ttk.LabelFrame(frame, text=title, style="Card.TLabelframe")
            card.grid(row=0, column=index, sticky=tk.EW, padx=(0 if index == 0 else 8, 0))
            ttk.Label(card, textvariable=variable, wraplength=230, justify=tk.LEFT).pack(anchor=tk.W)

        project_card = ttk.LabelFrame(frame, text="项目状态", style="Card.TLabelframe")
        project_card.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(8, 0), padx=(0, 8))
        ttk.Label(project_card, textvariable=self.project_var, wraplength=520, justify=tk.LEFT).pack(anchor=tk.W)

        autosync_card = ttk.LabelFrame(frame, text="后台自动同步", style="Card.TLabelframe")
        autosync_card.grid(row=1, column=2, sticky=tk.EW, pady=(8, 0), padx=(0, 8))
        ttk.Label(autosync_card, textvariable=self.autosync_headline_var, style="StatusValue.TLabel").pack(anchor=tk.W)
        ttk.Label(
            autosync_card,
            textvariable=self.autosync_detail_var,
            style="StatusSubtle.TLabel",
            wraplength=250,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(4, 0))

        safety_card = ttk.LabelFrame(frame, text="安全提醒", style="Card.TLabelframe")
        safety_card.grid(row=1, column=3, sticky=tk.EW, pady=(8, 0))
        ttk.Label(safety_card, textvariable=self.safety_var, wraplength=250, justify=tk.LEFT).pack(anchor=tk.W)
        return frame

    def _build_guide_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=14)
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)

        guides = [
            (
                "第一次使用：先保命，再整理",
                [
                    "1. 先点“刷新状态”，确认工具读到了当前账号/通道、历史数量和项目数量。",
                    "2. 点“手动备份当前状态”。这个备份只是保存历史和项目登记，不会保存登录密码或 token。",
                    "3. 如果你只是切换过 Official、API key、CC Switch 后历史少了，再点“同步历史到当前账号/通道”。",
                    "4. 同步后重新打开 Codex Desktop，看历史是否恢复。",
                ],
            ),
            (
                "换电脑恢复：先登录，再恢复",
                [
                    "1. 旧电脑先点“手动备份当前状态”，再打开备份目录，把最新备份文件夹带到新电脑。",
                    "2. 新电脑先正常安装并登录 Codex Desktop，登录方式可以是 OpenAI 官方、ChatGPT OAuth、API key、自定义通道或 CC Switch。",
                    "3. 新电脑打开本工具，先点“刷新状态”，再在备份管理里选中旧电脑备份并恢复。",
                    "4. 默认不会迁移 auth.json、OAuth token、API key、refresh token 或 CC Switch 数据库。",
                ],
            ),
            (
                "项目少了、重复、项目里暂无对话",
                [
                    "1. 先完全退出 Codex Desktop，避免它把旧状态写回来。",
                    "2. 点“项目诊断”，看是否有重复项目、项目数量异常或项目没有匹配到历史。",
                    "3. 点“只修项目列表”。它只修项目登记，不会改你的登录凭据。",
                    "4. 重新打开 Codex Desktop，再看项目列表和项目里的历史。",
                ],
            ),
            (
                "什么时候点哪个按钮",
                [
                    "刷新状态：不确定当前情况时先点它。",
                    "手动备份当前状态：做任何恢复、同步、修复前都建议先点。",
                    "同步历史到当前账号/通道：历史还在，但切换登录方式后看不到时使用。",
                    "恢复选中备份：换电脑、误操作后回到某个备份点时使用。",
                    "只修项目列表：项目少了、重复、项目里暂无对话时使用。",
                    "自动同步：确认手动同步稳定后，再考虑开启。",
                ],
            ),
        ]

        for index, (title, lines) in enumerate(guides):
            box = ttk.LabelFrame(tab, text=title, style="Guide.TLabelframe")
            box.grid(row=index // 2, column=index % 2, sticky=tk.NSEW, padx=(0, 12), pady=(0, 12))
            text = "\n".join(lines)
            ttk.Label(box, text=text, wraplength=480, justify=tk.LEFT).pack(anchor=tk.W)

        quick = ttk.Frame(tab)
        quick.grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))
        ttk.Button(quick, text="刷新状态", command=self.refresh_state_async, style="Primary.TButton").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(quick, text="一键安全备份并修复", command=self.one_click_safe_sync_async, style="Primary.TButton").pack(
            side=tk.LEFT,
            padx=(0, 8),
        )
        ttk.Button(quick, text="手动备份当前状态", command=self.backup_async, style="Primary.TButton").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(quick, text="打开备份目录", command=self.open_backups).pack(side=tk.LEFT)
        return tab

    def _build_actions_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=14)
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)

        one_click_box = ttk.LabelFrame(tab, text="新手一键修复", padding=12)
        one_click_box.grid(row=0, column=0, columnspan=2, sticky=tk.EW, pady=(0, 12))
        ttk.Label(
            one_click_box,
            text="自动处理 .codex-official 和 .codex：先关闭 Codex Desktop，再安全备份，最后修复历史可见性和项目列表。不会复制 auth.json、Token、API key 或 CC Switch 数据库。",
            wraplength=980,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 10))
        ttk.Button(
            one_click_box,
            text="一键安全备份并修复",
            command=self.one_click_safe_sync_async,
            style="Primary.TButton",
        ).pack(anchor=tk.W)

        sync_box = ttk.LabelFrame(tab, text="历史同步", padding=12)
        sync_box.grid(row=1, column=0, sticky=tk.NSEW, padx=(0, 10))
        ttk.Label(
            sync_box,
            text="适合切换 OpenAI 官方、API key、自定义通道、CC Switch 后，旧历史还在但当前账号/通道看不到的情况。",
            wraplength=460,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 10))
        ttk.Button(sync_box, text="同步历史到当前账号/通道", command=self.sync_async, style="Primary.TButton").pack(anchor=tk.W)

        project_box = ttk.LabelFrame(tab, text="项目列表修复", padding=12)
        project_box.grid(row=1, column=1, sticky=tk.NSEW)
        ttk.Label(
            project_box,
            text="适合项目变少、重复、项目里显示暂无对话。请先完全退出 Codex Desktop，再修复。",
            wraplength=460,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 10))
        project_buttons = ttk.Frame(project_box)
        project_buttons.pack(anchor=tk.W)
        ttk.Button(project_buttons, text="项目诊断", command=self.project_diagnose_async).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(project_buttons, text="只修项目列表", command=self.project_repair_async, style="Primary.TButton").pack(side=tk.LEFT)

        autosync_box = ttk.LabelFrame(tab, text="自动同步设置", padding=12)
        autosync_box.grid(row=2, column=0, columnspan=2, sticky=tk.EW, pady=(12, 0))
        for index in range(3):
            autosync_box.columnconfigure(index, weight=1, uniform="autosync")

        explain = ttk.Frame(autosync_box)
        explain.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 12))
        ttk.Label(explain, text="用途", style="StatusValue.TLabel").pack(anchor=tk.W)
        ttk.Label(
            explain,
            text="适合经常切换账号/通道，又希望打开 Codex Desktop 后自动整理历史的情况。它不会迁移登录凭据、Token、API key 或 CC Switch 数据库。",
            wraplength=330,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(6, 0))

        state = ttk.Frame(autosync_box)
        state.grid(row=0, column=1, sticky=tk.NSEW, padx=(0, 12))
        ttk.Label(state, text="当前状态", style="StatusValue.TLabel").pack(anchor=tk.W)
        ttk.Label(state, textvariable=self.autosync_status_var, wraplength=330, justify=tk.LEFT).pack(anchor=tk.W, pady=(6, 0))
        ttk.Label(state, textvariable=self.autosync_method_var, wraplength=330, justify=tk.LEFT).pack(anchor=tk.W, pady=(4, 0))
        ttk.Label(state, textvariable=self.autosync_next_var, wraplength=330, justify=tk.LEFT).pack(anchor=tk.W, pady=(4, 0))

        controls = ttk.Frame(autosync_box)
        controls.grid(row=0, column=2, sticky=tk.NSEW)
        ttk.Label(controls, text="操作", style="StatusValue.TLabel").pack(anchor=tk.W)
        ttk.Label(
            controls,
            textvariable=self.autosync_help_var,
            wraplength=330,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(6, 10))
        buttons = ttk.Frame(controls)
        buttons.pack(anchor=tk.W, fill=tk.X)
        ttk.Button(buttons, text="刷新自动同步状态", command=self.refresh_autosync_status_async).pack(anchor=tk.W, fill=tk.X, pady=(0, 6))
        ttk.Button(buttons, text="开启自动同步", command=self.enable_autosync, style="Primary.TButton").pack(
            anchor=tk.W,
            fill=tk.X,
            pady=(0, 6),
        )
        ttk.Button(buttons, text="关闭自动同步", command=self.disable_autosync).pack(anchor=tk.W, fill=tk.X)
        return tab

    def _build_backups_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=14)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        header = ttk.Frame(tab)
        header.grid(row=0, column=0, sticky=tk.EW, pady=(0, 10))
        ttk.Button(header, text="手动备份当前状态", command=self.backup_async, style="Primary.TButton").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(header, text="打开备份目录", command=self.open_backups).pack(side=tk.LEFT)

        self.backups = ttk.Treeview(
            tab,
            columns=("time", "name", "sessions", "projects", "provider", "notes"),
            show="headings",
            height=12,
        )
        self.backups.heading("time", text="时间")
        self.backups.heading("name", text="备份名称")
        self.backups.heading("sessions", text="历史")
        self.backups.heading("projects", text="项目")
        self.backups.heading("provider", text="通道")
        self.backups.heading("notes", text="备注")
        self.backups.column("time", width=150, stretch=False)
        self.backups.column("name", width=360)
        self.backups.column("sessions", width=60, stretch=False, anchor=tk.CENTER)
        self.backups.column("projects", width=60, stretch=False, anchor=tk.CENTER)
        self.backups.column("provider", width=120, stretch=False)
        self.backups.column("notes", width=260)
        self.backups.grid(row=1, column=0, sticky=tk.NSEW)

        backup_buttons = ttk.Frame(tab)
        backup_buttons.grid(row=2, column=0, sticky=tk.W, pady=(10, 0))
        ttk.Button(backup_buttons, text="恢复选中备份", command=self.restore_selected_async, style="Primary.TButton").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(backup_buttons, text="恢复最新备份", command=self.restore_latest_async).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(backup_buttons, text="查看详情", command=self.backup_details_async).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(backup_buttons, text="重命名", command=self.rename_backup_async).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(backup_buttons, text="写备注", command=self.edit_backup_notes_async).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(backup_buttons, text="删除", command=self.delete_backup_async).pack(side=tk.LEFT)
        return tab

    def _build_diagnostics_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=14)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        ttk.Label(tab, textvariable=self.path_var).grid(row=0, column=0, sticky=tk.W, pady=(0, 8))
        self.log = tk.Text(tab, height=14, wrap=tk.WORD, borderwidth=1, relief=tk.SOLID)
        self.log.grid(row=1, column=0, sticky=tk.NSEW)
        return tab

    def append_log(self, message: str) -> None:
        self.log.insert(tk.END, f"{message}\n")
        self.log.see(tk.END)

    def run_background(self, work, on_success=None, title: str = "操作失败") -> None:
        def runner() -> None:
            try:
                result = work()
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror(title, str(exc)))
                self.after(0, lambda: self.append_log(f"{title}: {exc}"))
                return
            if on_success:
                self.after(0, lambda: on_success(result))

        threading.Thread(target=runner, daemon=True).start()

    def backend(self, command: str, *args: str) -> dict:
        paths = sync_backend.resolve_paths(None)
        if command == "status":
            payload = sync_backend.get_status(paths)
        elif command == "sync":
            payload = sync_backend.sync_to_current_provider(paths)
        elif command == "backup":
            sync_backend.ensure_environment(paths)
            payload = {"backup_path": str(sync_backend.make_backup(paths, "manual"))}
        elif command == "restore":
            backup = args[0] if args else None
            payload = sync_backend.restore_backup(paths, backup)
        elif command == "backup-details":
            backup = args[0] if args else None
            payload = sync_backend.backup_details(paths, backup)
        elif command == "backup-update":
            backup, display_name, notes = args
            payload = sync_backend.update_backup_manifest(
                paths,
                backup,
                display_name if display_name else None,
                notes if notes else None,
            )
        elif command == "backup-delete":
            backup = args[0]
            payload = sync_backend.delete_backup(paths, backup)
        elif command == "project-diagnose":
            sync_backend.ensure_environment(paths)
            payload = {"action": "project-diagnose", **sync_backend.diagnose_projects(paths)}
        elif command == "project-repair":
            backup_path = sync_backend.make_backup(paths, "pre-project-repair")
            payload = sync_backend.repair_projects(paths)
            payload["safety_backup"] = str(backup_path)
        elif command == "one-click-safe-sync":
            payload = sync_backend.one_click_safe_sync(
                mode="auto",
                close_codex=True,
                backup=True,
                fix_projects=True,
                no_credentials=True,
                merge_global_state=True,
            )
        else:
            raise RuntimeError(f"不支持的命令: {command}")
        payload["ok"] = True
        return payload

    def refresh_state_async(self) -> None:
        self.run_background(lambda: self.backend("status"), self.render_state, "刷新失败")
        self.refresh_autosync_status_async(show_popup=False)

    def render_state(self, status: dict) -> None:
        self.latest_status = status
        self.provider_var.set(str(status.get("current_provider") or "未知"))
        login_mode = status.get("login_mode") or {}
        self.login_var.set(self.login_mode_label(str(login_mode.get("mode") or "unknown")))
        self.model_var.set(str(status.get("current_model") or "未读取到"))
        self.history_var.set(f"历史 {status.get('total_threads', 0)} 条，可整理 {status.get('movable_threads', 0)} 条")

        diagnostics = status.get("project_diagnostics") or {}
        duplicate_count = len(diagnostics.get("duplicate_local_project_paths") or [])
        self.project_var.set(
            f"项目 {diagnostics.get('project_root_count', 0)} 个，重复 {duplicate_count} 个，"
            f"最近 50 条项目历史 {diagnostics.get('recent_50_project_thread_count', 0)} 条"
        )
        self.path_var.set(f"数据库: {status.get('db_path')}")

        if hasattr(self, "backups"):
            for item in self.backups.get_children():
                self.backups.delete(item)
            self.backup_rows = list(status.get("backups", []))
            for index, backup in enumerate(self.backup_rows):
                title = backup.get("display_name") or backup.get("name")
                self.backups.insert(
                    "",
                    tk.END,
                    iid=str(index),
                    values=(
                        backup.get("modified_at") or "",
                        self.readable_backup_title(str(title)),
                        backup.get("session_count") or "?",
                        backup.get("project_root_count") or "?",
                        backup.get("target_provider") or "未知",
                        backup.get("notes") or "",
                    ),
                )

        self.append_log(f"状态已刷新。当前账号/通道={status.get('current_provider')}，可整理历史={status.get('movable_threads')}")

    def readable_backup_title(self, title: str) -> str:
        return (
            title.replace("provider-", "通道-")
            .replace("-sessions", "-历史")
            .replace("-projects", "-项目")
            .replace("unknown", "未知")
        )

    def login_mode_label(self, mode: str) -> str:
        labels = {
            "chatgpt-oauth": "OpenAI 官方 / ChatGPT 登录",
            "openai-compatible-api": "OpenAI 兼容 API",
            "custom-provider": "自定义通道",
            "cc-switch-local-route": "CC Switch 本地路由",
            "unknown": "未识别，仅按历史数据处理",
        }
        return labels.get(mode, mode)

    def sync_async(self) -> None:
        if not self.latest_status:
            self.refresh_state_async()
            return
        movable = int(self.latest_status.get("movable_threads") or 0)
        if movable <= 0:
            messagebox.showinfo("无需同步", "当前没有需要同步的历史。")
            return
        if not messagebox.askokcancel("确认同步", f"将整理 {movable} 条历史到当前账号/通道，并先自动备份。"):
            return
        self.run_background(lambda: self.backend("sync"), self.after_sync, "同步失败")

    def after_sync(self, result: dict) -> None:
        self.append_log(f"同步完成。已更新 {result.get('updated_rows')} 条历史。备份: {result.get('backup_path')}")
        messagebox.showinfo("同步完成", "同步完成。若历史列表没有立刻刷新，请重开一次 Codex Desktop。")
        self.refresh_state_async()

    def backup_async(self) -> None:
        self.run_background(lambda: self.backend("backup"), self.after_backup, "备份失败")

    def after_backup(self, result: dict) -> None:
        self.append_log(f"手动备份完成: {result.get('backup_path')}")
        self.refresh_state_async()

    def one_click_safe_sync_async(self) -> None:
        if not messagebox.askokcancel(
            "一键安全备份并修复",
            "将自动处理 .codex-official 和 .codex：先尝试关闭 Codex Desktop，再安全备份并修复历史可见性和项目列表。\n\n不会复制 auth.json、Token、API key 或 CC Switch 数据库。",
        ):
            return
        self.run_background(lambda: self.backend("one-click-safe-sync"), self.after_one_click_safe_sync, "一键修复失败")

    def after_one_click_safe_sync(self, result: dict) -> None:
        summary = str(result.get("summary") or "一键安全备份并修复已完成。")
        self.append_log(summary)
        messagebox.showinfo("一键修复完成", summary)
        self.refresh_state_async()

    def selected_backup_path(self) -> str | None:
        selection = self.backups.selection()
        if not selection:
            messagebox.showinfo("请选择备份", "请先在备份列表里点选一个备份。")
            return None
        index = int(selection[0])
        if index < 0 or index >= len(self.backup_rows):
            messagebox.showinfo("请选择备份", "当前选择的备份无效，请刷新后再选。")
            return None
        return str(self.backup_rows[index].get("path") or "")

    def restore_selected_async(self) -> None:
        backup_path = self.selected_backup_path()
        if not backup_path:
            return
        if not messagebox.askokcancel("确认恢复", "将恢复你选中的备份，并在恢复前再做一次安全备份。请先完全退出 Codex Desktop。"):
            return
        self.run_background(lambda: self.backend("restore", backup_path), self.after_restore, "恢复失败")

    def restore_latest_async(self) -> None:
        if not messagebox.askokcancel("确认恢复", "将恢复最新备份，并在恢复前再做一次安全备份。请先完全退出 Codex Desktop。"):
            return
        self.run_background(lambda: self.backend("restore"), self.after_restore, "恢复失败")

    def after_restore(self, result: dict) -> None:
        self.append_log(f"恢复完成。来源备份: {result.get('restored_from')}")
        messagebox.showinfo("恢复完成", "恢复完成。请重新打开 Codex Desktop 再看历史和项目列表。")
        self.refresh_state_async()

    def backup_details_async(self) -> None:
        backup_path = self.selected_backup_path()
        if backup_path:
            self.run_background(lambda: self.backend("backup-details", backup_path), self.show_backup_details, "读取备份失败")

    def show_backup_details(self, result: dict) -> None:
        manifest = result.get("manifest") or {}
        lines = [
            f"显示名: {manifest.get('displayName') or Path(str(result.get('backup_path'))).name}",
            f"创建时间: {manifest.get('createdAt') or '未知'}",
            f"电脑: {manifest.get('hostname') or '未知'}",
            f"登录方式: {self.login_mode_label(str(manifest.get('loginModeDetected') or 'unknown'))}",
            f"账号/通道: {manifest.get('targetProvider') or '未知'}",
            f"历史数量: {manifest.get('sessionCount') or 0}",
            f"项目数量: {manifest.get('projectRootCount') or 0}",
            f"备注: {manifest.get('notes') or '无'}",
            "",
            "安全策略: 不默认迁移登录凭据、Token、API key、CC Switch 数据库。",
        ]
        messagebox.showinfo("备份详情", "\n".join(lines))

    def edit_backup_notes_async(self) -> None:
        backup_path = self.selected_backup_path()
        if not backup_path:
            return
        notes = simpledialog.askstring("编辑备注", "给这个备份写一句备注，方便以后识别：")
        if notes is None:
            return
        self.run_background(lambda: self.backend("backup-update", backup_path, "", notes), self.after_backup_update, "更新备注失败")

    def rename_backup_async(self) -> None:
        backup_path = self.selected_backup_path()
        if not backup_path:
            return
        display_name = simpledialog.askstring("重命名备份", "给这个备份改一个显示名：")
        if display_name is None:
            return
        self.run_background(lambda: self.backend("backup-update", backup_path, display_name, ""), self.after_backup_update, "重命名失败")

    def after_backup_update(self, result: dict) -> None:
        self.append_log(f"备份信息已更新: {result.get('backup_path')}")
        self.refresh_state_async()

    def delete_backup_async(self) -> None:
        backup_path = self.selected_backup_path()
        if not backup_path:
            return
        if not messagebox.askokcancel("删除备份", "只删除你选中的这个备份，不会删除 Codex 历史。确定删除吗？"):
            return
        self.run_background(lambda: self.backend("backup-delete", backup_path), self.after_backup_delete, "删除备份失败")

    def after_backup_delete(self, result: dict) -> None:
        self.append_log(f"备份已删除: {result.get('deleted')}")
        self.refresh_state_async()

    def project_diagnose_async(self) -> None:
        self.run_background(lambda: self.backend("project-diagnose"), self.show_project_diagnosis, "项目诊断失败")

    def show_project_diagnosis(self, result: dict) -> None:
        duplicate_count = len(result.get("duplicate_local_project_paths") or [])
        empty_projects = [item.get("path") for item in result.get("projects", []) if item.get("message")]
        lines = [
            f"识别到项目: {result.get('project_root_count', 0)} 个",
            f"重复项目: {duplicate_count} 个",
            f"最近 50 条里项目历史: {result.get('recent_50_project_thread_count', 0)} 条",
            f"存在但暂未匹配到历史的项目: {len(empty_projects)} 个",
        ]
        if empty_projects:
            lines.append("")
            lines.extend(str(path) for path in empty_projects[:8])
        messagebox.showinfo("项目诊断", "\n".join(lines))

    def project_repair_async(self) -> None:
        if not messagebox.askokcancel("只修项目列表", "将只修复 Codex Desktop 的项目列表登记，不迁移登录凭据。请先完全退出 Codex Desktop。"):
            return
        self.run_background(lambda: self.backend("project-repair"), self.after_project_repair, "项目修复失败")

    def after_project_repair(self, result: dict) -> None:
        self.append_log(
            f"项目列表已修复。新增项目={result.get('added_saved_workspace_roots')}，"
            f"移除重复={result.get('removed_local_project_duplicates')}，安全备份={result.get('safety_backup')}"
        )
        messagebox.showinfo("项目修复完成", "项目列表已修复。请重新打开 Codex Desktop 查看项目和历史。")
        self.refresh_state_async()

    def open_backups(self) -> None:
        if not self.latest_status:
            self.refresh_state_async()
            return
        folder = Path(str(self.latest_status.get("backup_dir")))
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))
        self.append_log(f"已打开备份目录: {folder}")

    def task_payload(self, command: str) -> dict:
        args = windows_task_scheduler.parse_args([command, "--json"])
        if command == "install":
            return windows_task_scheduler.install_task(args)
        if command == "uninstall":
            return windows_task_scheduler.uninstall_task(args)
        if command == "status":
            return windows_task_scheduler.status_task(args)
        raise RuntimeError(f"不支持的自动同步命令: {command}")

    def autosync_method_label(self, method: object) -> str:
        labels = {
            "task_scheduler": "任务计划",
            "startup": "启动文件夹备用方案",
            "none": "未开启",
        }
        return labels.get(str(method or "none"), str(method or "未知"))

    def autosync_status_message(self, result: dict) -> str:
        if not result.get("exists"):
            return "未开启"
        method = result.get("method") or "未知"
        message = f"已开启，使用 {self.autosync_method_label(method)}"
        if method == "startup":
            message += "，说明当前 Windows 限制任务计划"
        return message

    def autosync_help_message(self, result: dict | None = None) -> str:
        base = "开启一次即可长期生效；关闭本工具窗口不影响后台监听。"
        if result and result.get("exists"):
            return base + " 如果换了登录方式或通道，先看顶部状态，再决定是否手动同步。"
        return base + " 只想临时整理一次历史时，不必开启自动同步。"

    def autosync_summary_values(self, result: dict) -> tuple[str, str, str, str]:
        if not result.get("exists"):
            return (
                "未开启",
                "需要时点“开启自动同步”一次",
                "启用方式：未开启",
                "生效时机：开启后，Windows 登录时自动启动后台监听",
            )

        method = result.get("method") or "未知"
        method_label = self.autosync_method_label(method)
        if method == "startup":
            detail = "已启用备用方案"
            method_text = "启用方式：启动文件夹备用方案"
        elif method == "task_scheduler":
            detail = "已启用标准方案"
            method_text = "启用方式：Windows 任务计划"
        else:
            detail = "已启用"
            method_text = f"启用方式：{method_label}"

        launcher = result.get("startup_launcher_path")
        if method == "startup" and launcher:
            method_text += f"\n启动脚本：{launcher}"

        return (
            "已开启",
            detail,
            method_text,
            "生效时机：Windows 登录后启动后台监听；打开 Codex Desktop 时检查并同步需要整理的历史",
        )

    def refresh_autosync_status_async(self, show_popup: bool = True) -> None:
        def done(result: dict) -> None:
            self.render_autosync_status(result)
            if show_popup:
                messagebox.showinfo("自动同步状态", self.autosync_status_message(result))

        self.run_background(lambda: self.task_payload("status"), done, "查询自动同步状态失败")

    def render_autosync_status(self, result: dict) -> None:
        message = self.autosync_status_message(result)
        headline, detail, method_text, next_text = self.autosync_summary_values(result)
        self.autosync_headline_var.set(headline)
        self.autosync_detail_var.set(detail)
        self.autosync_status_var.set(message)
        self.autosync_method_var.set(method_text)
        self.autosync_next_var.set(next_text)
        self.autosync_help_var.set(self.autosync_help_message(result))
        self.append_log(f"自动同步状态：{message}")

    def show_autosync_status(self) -> None:
        try:
            result = self.task_payload("status")
            self.render_autosync_status(result)
            message = self.autosync_status_message(result)
            self.append_log(message)
            messagebox.showinfo("自动同步状态", message)
        except Exception as exc:
            messagebox.showerror("查询失败", str(exc))

    def enable_autosync(self) -> None:
        if not messagebox.askokcancel(
            "开启自动同步",
            "开启后只需要设置一次。以后 Windows 登录后会自动启动后台同步；不需要每次打开本工具，也不需要每次点开启。",
        ):
            return
        self.run_background(lambda: self.task_payload("install"), self.after_enable_autosync, "开启自动同步失败")

    def after_enable_autosync(self, result: dict) -> None:
        method_label = self.autosync_method_label(result.get("method"))
        self.render_autosync_status({"exists": True, **result})
        self.append_log(f"自动同步已开启。方式: {method_label}。日志: {result.get('log_path')}")
        messagebox.showinfo("完成", f"自动同步已开启。\n方式：{method_label}\n\n以后不需要每次打开本工具或重复开启。")

    def disable_autosync(self) -> None:
        if not messagebox.askokcancel("关闭自动同步", "将移除 Windows 自动同步任务，不会删除你的备份。"):
            return
        self.run_background(lambda: self.task_payload("uninstall"), self.after_disable_autosync, "关闭自动同步失败")

    def after_disable_autosync(self, result: dict) -> None:
        self.render_autosync_status({"exists": False, "method": "none"})
        self.append_log("自动同步已关闭。" if result.get("removed") else "自动同步本来就不存在。")
        messagebox.showinfo("完成", "自动同步已关闭。")


def main() -> int:
    app = WindowsSyncApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
