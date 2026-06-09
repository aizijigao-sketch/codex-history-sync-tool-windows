from __future__ import annotations

import os
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

import sync_backend
from scripts import windows_autosync_settings, windows_task_scheduler


class WindowsSyncApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.version_label = f"v{sync_backend.TOOL_VERSION}"
        self.title(f"Codex 历史同步工具 {self.version_label}")
        self.geometry("980x680")
        self.minsize(760, 560)
        self.latest_status: dict | None = None
        self.backup_rows: list[dict] = []
        self.provider_restart_prompted = False

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
        self.recommendation_title_var = tk.StringVar(value="先刷新状态")
        self.recommendation_detail_var = tk.StringVar(value="工具会读取当前 Codex 历史、项目和后台同步状态，然后给出下一步建议。")
        self.backup_summary_var = tk.StringVar(value="备份：未刷新")
        self.auto_detect_var = tk.BooleanVar(value=True)
        self.auto_fix_chats_var = tk.BooleanVar(value=True)
        self.auto_fix_projects_var = tk.BooleanVar(value=False)
        self.dual_home_var = tk.BooleanVar(value=True)
        self.detect_only_var = tk.BooleanVar(value=False)

        self.load_autosync_settings()
        self._build_ui()
        self.refresh_state_async()

    def load_autosync_settings(self) -> None:
        settings = windows_autosync_settings.load_settings()
        self.auto_detect_var.set(settings["auto_detect"])
        self.auto_fix_chats_var.set(settings["auto_fix_chats"])
        self.auto_fix_projects_var.set(settings["auto_fix_projects"])
        self.dual_home_var.set(settings["dual_home"])
        self.detect_only_var.set(settings["detect_only"])

    def current_autosync_settings(self) -> dict[str, bool]:
        return {
            "auto_detect": bool(self.auto_detect_var.get()),
            "auto_fix_chats": bool(self.auto_fix_chats_var.get()),
            "auto_fix_projects": bool(self.auto_fix_projects_var.get()),
            "dual_home": bool(self.dual_home_var.get()),
            "detect_only": bool(self.detect_only_var.get()),
        }

    def _build_ui(self) -> None:
        self.configure(background="#eef2f5")
        style = ttk.Style(self)
        style.configure("TFrame", background="#eef2f5")
        style.configure("Panel.TFrame", background="#ffffff", relief=tk.FLAT)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"), background="#eef2f5", foreground="#17202a")
        style.configure("Subtitle.TLabel", font=("Microsoft YaHei UI", 10), background="#eef2f5", foreground="#667085")
        style.configure("Hint.TLabel", font=("Microsoft YaHei UI", 10), background="#ffffff", foreground="#667085")
        style.configure("Card.TLabelframe", padding=12, background="#ffffff")
        style.configure("Card.TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"), foreground="#344054")
        style.configure("StatusValue.TLabel", font=("Microsoft YaHei UI", 13, "bold"), background="#ffffff", foreground="#101828")
        style.configure("StatusSubtle.TLabel", font=("Microsoft YaHei UI", 9), background="#ffffff", foreground="#667085")
        style.configure("Section.TLabel", font=("Microsoft YaHei UI", 12, "bold"), background="#eef2f5", foreground="#101828")
        style.configure("PanelTitle.TLabel", font=("Microsoft YaHei UI", 12, "bold"), background="#ffffff", foreground="#101828")
        style.configure("PanelText.TLabel", font=("Microsoft YaHei UI", 10), background="#ffffff", foreground="#475467")
        style.configure("Primary.TButton", padding=(12, 6))
        style.configure("Quiet.TButton", padding=(10, 5))
        style.configure("TCheckbutton", background="#ffffff", foreground="#344054")
        style.configure("TNotebook", background="#eef2f5", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 8))

        root = ttk.Frame(self, padding=18)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        ttk.Label(root, text=f"Codex 历史同步工具 {self.version_label}", style="Title.TLabel").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(
            root,
            text="多登录方式、多 provider、多电脑迁移的历史同步与项目修复。默认不迁移登录凭据、Token、API key 或 CC Switch 数据库。",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky=tk.W, pady=(6, 14))

        notebook = ttk.Notebook(root)
        notebook.grid(row=2, column=0, sticky=tk.NSEW)
        notebook.add(self._build_dashboard_tab(notebook), text="首页")
        notebook.add(self._build_backups_tab(notebook), text="备份")
        notebook.add(self._build_settings_tab(notebook), text="自动同步")
        notebook.add(self._build_diagnostics_tab(notebook), text="日志")

    def _build_status_cards(self, parent: ttk.Frame) -> ttk.Frame:
        frame = ttk.Frame(parent)
        for index in range(3):
            frame.columnconfigure(index, weight=1, uniform="cards")

        cards = [
            ("账号/通道", self.provider_var),
            ("登录方式", self.login_var),
            ("历史", self.history_var),
        ]
        for index, (title, variable) in enumerate(cards):
            card = ttk.LabelFrame(frame, text=title, style="Card.TLabelframe")
            card.grid(row=0, column=index, sticky=tk.EW, padx=(0 if index == 0 else 10, 0), pady=(0, 10))
            ttk.Label(card, textvariable=variable, style="StatusValue.TLabel", wraplength=250, justify=tk.LEFT).pack(anchor=tk.W)

        project_card = ttk.LabelFrame(frame, text="项目状态", style="Card.TLabelframe")
        project_card.grid(row=1, column=0, sticky=tk.EW, padx=(0, 10))
        ttk.Label(project_card, textvariable=self.project_var, style="StatusValue.TLabel", wraplength=270, justify=tk.LEFT).pack(anchor=tk.W)

        autosync_card = ttk.LabelFrame(frame, text="后台自动同步", style="Card.TLabelframe")
        autosync_card.grid(row=1, column=1, sticky=tk.EW, padx=(0, 10))
        ttk.Label(autosync_card, textvariable=self.autosync_headline_var, style="StatusValue.TLabel").pack(anchor=tk.W)
        ttk.Label(
            autosync_card,
            textvariable=self.autosync_detail_var,
            style="StatusSubtle.TLabel",
            wraplength=270,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(4, 0))

        backup_card = ttk.LabelFrame(frame, text="备份", style="Card.TLabelframe")
        backup_card.grid(row=1, column=2, sticky=tk.EW)
        ttk.Label(backup_card, textvariable=self.backup_summary_var, style="StatusValue.TLabel", wraplength=270, justify=tk.LEFT).pack(anchor=tk.W)
        return frame

    def _scrollable_tab(self, parent: ttk.Notebook) -> tuple[ttk.Frame, ttk.Frame]:
        outer = ttk.Frame(parent)
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)
        canvas = tk.Canvas(outer, highlightthickness=0, background="#eef2f5")
        scrollbar = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        content = ttk.Frame(canvas, padding=14)
        window_id = canvas.create_window((0, 0), window=content, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky=tk.NSEW)
        scrollbar.grid(row=0, column=1, sticky=tk.NS)
        content.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window_id, width=event.width))
        return outer, content

    def _build_dashboard_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        tab, content = self._scrollable_tab(parent)
        content.columnconfigure(0, weight=2)
        content.columnconfigure(1, weight=1)

        self._build_status_cards(content).grid(row=0, column=0, columnspan=2, sticky=tk.EW, pady=(0, 14))

        recommend = ttk.LabelFrame(content, text="建议下一步", style="Card.TLabelframe")
        recommend.grid(row=1, column=0, sticky=tk.NSEW, padx=(0, 12), pady=(0, 12))
        ttk.Label(recommend, textvariable=self.recommendation_title_var, style="PanelTitle.TLabel").pack(anchor=tk.W)
        ttk.Label(
            recommend,
            textvariable=self.recommendation_detail_var,
            style="PanelText.TLabel",
            wraplength=560,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(6, 12))
        main_actions = ttk.Frame(recommend, style="Panel.TFrame")
        main_actions.pack(anchor=tk.W, fill=tk.X)
        main_actions.columnconfigure(0, weight=1)
        main_actions.columnconfigure(1, weight=1)
        ttk.Button(main_actions, text="一键安全备份并修复", command=self.one_click_safe_sync_async, style="Primary.TButton").grid(
            row=0,
            column=0,
            columnspan=2,
            sticky=tk.EW,
            pady=(0, 8),
        )
        ttk.Button(main_actions, text="只同步历史", command=self.sync_async).grid(row=1, column=0, sticky=tk.EW, padx=(0, 8))
        ttk.Button(main_actions, text="只修项目列表", command=self.project_repair_async).grid(row=1, column=1, sticky=tk.EW)

        quick = ttk.LabelFrame(content, text="常用操作", style="Card.TLabelframe")
        quick.grid(row=1, column=1, sticky=tk.NSEW, pady=(0, 12))
        for text, command, primary in [
            ("刷新状态", self.refresh_state_async, True),
            ("手动备份", self.backup_async, False),
            ("项目诊断", self.project_diagnose_async, False),
            ("打开备份目录", self.open_backups, False),
        ]:
            ttk.Button(
                quick,
                text=text,
                command=command,
                style="Primary.TButton" if primary else "Quiet.TButton",
            ).pack(anchor=tk.W, fill=tk.X, pady=(0, 8))

        safety = ttk.LabelFrame(content, text="安全边界", style="Card.TLabelframe")
        safety.grid(row=2, column=0, sticky=tk.EW, padx=(0, 12))
        ttk.Label(
            safety,
            text="本工具只整理历史、项目登记和 provider metadata；默认不复制 auth.json、OAuth token、API key、refresh token、CC Switch 数据库。",
            style="PanelText.TLabel",
            wraplength=560,
            justify=tk.LEFT,
        ).pack(anchor=tk.W)
        ttk.Button(safety, text="查看新手教程", command=self.show_beginner_help).pack(anchor=tk.W, pady=(10, 0))

        autosync = ttk.LabelFrame(content, text="自动同步状态", style="Card.TLabelframe")
        autosync.grid(row=2, column=1, sticky=tk.EW)
        ttk.Label(autosync, textvariable=self.autosync_status_var, style="PanelTitle.TLabel", wraplength=280).pack(anchor=tk.W)
        ttk.Label(autosync, textvariable=self.autosync_next_var, style="PanelText.TLabel", wraplength=280, justify=tk.LEFT).pack(anchor=tk.W, pady=(6, 10))
        ttk.Button(autosync, text="管理自动同步", command=lambda: self.notebook_select_by_text(parent, "自动同步")).pack(anchor=tk.W)
        return tab

    def notebook_select_by_text(self, notebook: ttk.Notebook, text: str) -> None:
        for tab_id in notebook.tabs():
            if notebook.tab(tab_id, "text") == text:
                notebook.select(tab_id)
                return

    def _build_settings_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        tab, content = self._scrollable_tab(parent)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)

        state = ttk.LabelFrame(content, text="后台运行状态", style="Card.TLabelframe")
        state.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 12), pady=(0, 12))
        ttk.Label(state, textvariable=self.autosync_status_var, style="PanelTitle.TLabel", wraplength=420).pack(anchor=tk.W)
        ttk.Label(state, textvariable=self.autosync_method_var, style="PanelText.TLabel", wraplength=420, justify=tk.LEFT).pack(anchor=tk.W, pady=(8, 0))
        ttk.Label(state, textvariable=self.autosync_next_var, style="PanelText.TLabel", wraplength=420, justify=tk.LEFT).pack(anchor=tk.W, pady=(4, 10))
        state_buttons = ttk.Frame(state, style="Panel.TFrame")
        state_buttons.pack(anchor=tk.W, fill=tk.X)
        for index in range(2):
            state_buttons.columnconfigure(index, weight=1)
        ttk.Button(state_buttons, text="刷新状态", command=self.refresh_autosync_status_async).grid(
            row=0,
            column=0,
            sticky=tk.EW,
            padx=(0, 8),
            pady=(0, 6),
        )
        ttk.Button(state_buttons, text="开启后台同步", command=self.enable_autosync, style="Primary.TButton").grid(
            row=0,
            column=1,
            sticky=tk.EW,
            pady=(0, 6),
        )
        ttk.Button(state_buttons, text="关闭后台同步", command=self.disable_autosync).grid(row=1, column=0, columnspan=2, sticky=tk.EW)

        rules = ttk.LabelFrame(content, text="自动处理范围", style="Card.TLabelframe")
        rules.grid(row=0, column=1, sticky=tk.NSEW, pady=(0, 12))
        checks = [
            ("后台检测 Codex 状态", self.auto_detect_var),
            ("自动修复聊天可见性", self.auto_fix_chats_var),
            ("自动修复项目列表", self.auto_fix_projects_var),
            ("同时关注 .codex 与 .codex-official", self.dual_home_var),
            ("仅提示，不自动修改", self.detect_only_var),
        ]
        for text, variable in checks:
            ttk.Checkbutton(rules, text=text, variable=variable).pack(anchor=tk.W, pady=(0, 6))
        ttk.Label(
            rules,
            text="设置会保存到本机后台策略文件。项目列表自动修复默认关闭；打开后 watcher 会在检测到异常时尝试修复，并先生成安全备份。",
            style="PanelText.TLabel",
            wraplength=420,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(8, 0))
        ttk.Button(rules, text="保存自动同步设置", command=self.save_autosync_settings, style="Primary.TButton").pack(
            anchor=tk.W,
            fill=tk.X,
            pady=(10, 0),
        )

        usage = ttk.LabelFrame(content, text="什么时候开启", style="Card.TLabelframe")
        usage.grid(row=1, column=0, sticky=tk.EW, padx=(0, 12))
        ttk.Label(
            usage,
            text="经常在 OpenAI 官方、API key、自定义 provider、CC Switch 之间切换时，可以开启。开启一次后，Windows 登录时会启动后台监听；关闭这个工具窗口后仍会继续运行。",
            style="PanelText.TLabel",
            wraplength=420,
            justify=tk.LEFT,
        ).pack(anchor=tk.W)
        ttk.Button(usage, text="查看自动同步说明", command=self.show_autosync_help).pack(anchor=tk.W, pady=(10, 0))

        safety = ttk.LabelFrame(content, text="不会自动迁移", style="Card.TLabelframe")
        safety.grid(row=1, column=1, sticky=tk.EW)
        ttk.Label(
            safety,
            text="不会默认复制 auth.json、OAuth token、API key、refresh token、CC Switch 数据库或账号路由数据。换电脑时应先在新电脑正常登录，再恢复历史和项目登记。",
            style="PanelText.TLabel",
            wraplength=420,
            justify=tk.LEFT,
        ).pack(anchor=tk.W)
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
        self.backups.column("time", width=140, stretch=False)
        self.backups.column("name", width=300)
        self.backups.column("sessions", width=58, stretch=False, anchor=tk.CENTER)
        self.backups.column("projects", width=58, stretch=False, anchor=tk.CENTER)
        self.backups.column("provider", width=120, stretch=False)
        self.backups.column("notes", width=220)
        y_scroll = ttk.Scrollbar(tab, orient=tk.VERTICAL, command=self.backups.yview)
        x_scroll = ttk.Scrollbar(tab, orient=tk.HORIZONTAL, command=self.backups.xview)
        self.backups.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.backups.grid(row=1, column=0, sticky=tk.NSEW)
        y_scroll.grid(row=1, column=1, sticky=tk.NS)
        x_scroll.grid(row=2, column=0, sticky=tk.EW)

        backup_buttons = ttk.Frame(tab)
        backup_buttons.grid(row=3, column=0, sticky=tk.W, pady=(10, 0))
        buttons = [
            ("恢复选中备份", self.restore_selected_async, "Primary.TButton"),
            ("恢复最新备份", self.restore_latest_async, "Quiet.TButton"),
            ("查看详情", self.backup_details_async, "Quiet.TButton"),
            ("重命名", self.rename_backup_async, "Quiet.TButton"),
            ("写备注", self.edit_backup_notes_async, "Quiet.TButton"),
            ("删除", self.delete_backup_async, "Quiet.TButton"),
        ]
        for index, (text, command, button_style) in enumerate(buttons):
            ttk.Button(backup_buttons, text=text, command=command, style=button_style).grid(
                row=index // 3,
                column=index % 3,
                sticky=tk.W,
                padx=(0, 8),
                pady=(0, 6),
            )
        return tab

    def show_beginner_help(self) -> None:
        lines = [
            "第一次用：",
            "1. 点“刷新状态”，确认读到历史和项目。",
            "2. 点“手动备份”，先留一个安全点。",
            "3. 切换登录方式后历史少了，点“只同步历史”。",
            "4. 项目少了、重复、项目里暂无对话，先完全退出 Codex Desktop，再点“只修项目列表”。",
            "",
            "换电脑：",
            "1. 新电脑先正常安装并登录 Codex Desktop。",
            "2. 再恢复旧电脑备份。",
            "3. 默认不迁移任何登录凭据或密钥。",
        ]
        messagebox.showinfo("新手教程", "\n".join(lines))

    def show_autosync_help(self) -> None:
        lines = [
            "自动同步说明：",
            "开启后不是每次打开本工具才同步，而是 Windows 登录后在后台运行。",
            "关闭本工具窗口，不会关闭后台任务。",
            "如果 Windows 限制任务计划，会使用启动文件夹备用方案。",
            "",
            "建议：",
            "先手动同步确认稳定，再开启后台同步。",
            "项目列表自动修复更谨慎，最好在 Codex Desktop 完全退出后执行。",
        ]
        messagebox.showinfo("自动同步说明", "\n".join(lines))

    def save_autosync_settings(self) -> None:
        settings = self.current_autosync_settings()
        if settings["detect_only"] and (settings["auto_fix_chats"] or settings["auto_fix_projects"]):
            messagebox.showinfo("设置已调整", "你开启了“仅提示，不自动修改”。后台会记录状态，但不会自动同步或修复。")
        path = windows_autosync_settings.save_settings(settings)
        self.append_log(f"自动同步设置已保存: {path}")
        messagebox.showinfo("已保存", f"自动同步设置已保存。\n\n{path}")

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
                self.after(0, lambda: self.handle_background_error(title, exc))
                return
            if on_success:
                self.after(0, lambda: on_success(result))

        threading.Thread(target=runner, daemon=True).start()

    def handle_background_error(self, title: str, exc: Exception) -> None:
        message = str(exc)
        self.append_log(f"{title}: {message}")
        if self.is_provider_unresolved_message(message):
            self.prompt_restart_thirdparty_flow(message)
            return
        messagebox.showerror(title, message)

    def is_provider_unresolved_message(self, message: str) -> bool:
        markers = [
            "Could not determine current model_provider",
            "当前无法判断 Codex 正在使用哪个 provider",
            "config.toml has no model_provider",
        ]
        return any(marker in message for marker in markers)

    def prompt_restart_thirdparty_flow(self, detail: str) -> None:
        if not messagebox.askokcancel(
            "需要重启启动器",
            "检测到 Codex 当前通道状态不完整，通常是 CC Switch 还在运行时又选择了启动器 2，"
            "导致 Codex 暂时无法判断 provider，所以历史列表会显示为空。\n\n"
            "是否现在自动关闭 Codex 和 CC Switch，并重新按“第三方模式/启动器 2”启动？",
        ):
            return
        self.run_background(self.restart_thirdparty_flow, self.after_restart_thirdparty_flow, "自动重启失败")

    def restart_thirdparty_flow(self) -> dict[str, object]:
        launcher = Path("F:/AI-Workspace/20_Projects/codex-windows-launcher/codex-launcher.ps1")
        if not launcher.exists():
            raise RuntimeError(f"没有找到 Codex Windows 启动器：{launcher}")

        attempts: list[dict[str, object]] = []
        for process_name in ("Codex.exe", "OpenAI.Codex.exe", "ccswitch.exe", "CCSwitch.exe", "cc-switch.exe"):
            completed = subprocess.run(
                ["taskkill", "/IM", process_name, "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            attempts.append(
                {
                    "process": process_name,
                    "returncode": completed.returncode,
                    "output": (completed.stdout or completed.stderr or "").strip(),
                }
            )

        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(launcher),
                "-Mode",
                "thirdparty",
            ],
            cwd=str(launcher.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return {"launcher": str(launcher), "attempts": attempts}

    def after_restart_thirdparty_flow(self, result: dict) -> None:
        self.append_log(f"已调用启动器第三方模式重启：{result.get('launcher')}")
        messagebox.showinfo(
            "已开始重启",
            "已关闭 Codex / CC Switch，并重新按启动器 2 启动。\n\n"
            "等 Codex 窗口重新打开后，再点一次“刷新状态”或等待后台自动同步。",
        )
        self.provider_restart_prompted = False

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
        provider_error = str(status.get("provider_resolution_error") or "")
        self.provider_var.set(str(status.get("current_provider") or "通道未识别"))
        login_mode = status.get("login_mode") or {}
        self.login_var.set(self.login_mode_label(str(login_mode.get("mode") or "unknown")))
        self.model_var.set(str(status.get("current_model") or "未读取到"))
        if provider_error:
            self.history_var.set(f"历史 {status.get('total_threads', 0)} 条，等待重启通道")
        else:
            self.history_var.set(f"历史 {status.get('total_threads', 0)} 条，可整理 {status.get('movable_threads', 0)} 条")

        diagnostics = status.get("project_diagnostics") or {}
        duplicate_count = len(diagnostics.get("duplicate_local_project_paths") or [])
        self.project_var.set(
            f"项目 {diagnostics.get('project_root_count', 0)} 个，重复 {duplicate_count} 个，"
            f"最近 50 条项目历史 {diagnostics.get('recent_50_project_thread_count', 0)} 条"
        )
        self.path_var.set(f"数据库: {status.get('db_path')}")
        backups = list(status.get("backups", []))
        if backups:
            newest = backups[0]
            modified = str(newest.get("modified_at") or "").replace("T", " ")[:16]
            sessions = newest.get("session_count") or "?"
            projects = newest.get("project_root_count") or "?"
            latest = modified or "已找到"
            self.backup_summary_var.set(f"{len(backups)} 个\n最新：{latest}\n历史 {sessions} / 项目 {projects}")
        else:
            self.backup_summary_var.set("0 个\n建议先手动备份")
        self.update_recommendation(status, duplicate_count)

        if hasattr(self, "backups"):
            for item in self.backups.get_children():
                self.backups.delete(item)
            self.backup_rows = backups
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
        if provider_error:
            self.append_log(provider_error)
            if not self.provider_restart_prompted:
                self.provider_restart_prompted = True
                self.prompt_restart_thirdparty_flow(provider_error)
        else:
            self.provider_restart_prompted = False

    def update_recommendation(self, status: dict, duplicate_count: int) -> None:
        if status.get("provider_resolution_error"):
            self.recommendation_title_var.set("建议自动重启启动器")
            self.recommendation_detail_var.set(
                "当前通道状态不完整，通常是 CC Switch 未关闭时选择了启动器 2。请确认自动重启，让工具关闭 Codex 和 CC Switch 后重新按第三方模式启动。"
            )
            return
        movable = int(status.get("movable_threads") or 0)
        backups = list(status.get("backups", []))
        diagnostics = status.get("project_diagnostics") or {}
        recent_project_threads = int(diagnostics.get("recent_50_project_thread_count") or 0)
        project_count = int(diagnostics.get("project_root_count") or 0)
        if not backups:
            self.recommendation_title_var.set("先做一个安全备份")
            self.recommendation_detail_var.set("当前还没有可用备份。先点“手动备份”，以后恢复、同步或换电脑都有回退点。")
        elif movable > 0 and (duplicate_count > 0 or project_count == 0 or recent_project_threads == 0):
            self.recommendation_title_var.set("建议一键安全备份并修复")
            self.recommendation_detail_var.set("检测到历史可整理，同时项目状态可能异常。一键流程会先备份，再整理历史和项目登记，不迁移任何登录凭据。")
        elif movable > 0:
            self.recommendation_title_var.set("建议同步历史到当前通道")
            self.recommendation_detail_var.set("历史数据还在，但当前账号/通道可能看不到。点“只同步历史”即可先整理聊天记录。")
        elif duplicate_count > 0 or (project_count > 0 and recent_project_threads == 0):
            self.recommendation_title_var.set("建议只修项目列表")
            self.recommendation_detail_var.set("项目列表可能重复或项目归属没有被 Codex 识别。请先完全退出 Codex Desktop，再执行项目修复。")
        else:
            self.recommendation_title_var.set("当前看起来正常")
            self.recommendation_detail_var.set("可以先不用操作。切换 provider、换电脑或项目列表异常时，再回来同步或修复。")

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
        provider_error = str(self.latest_status.get("provider_resolution_error") or "")
        if provider_error or not str(self.latest_status.get("current_provider") or "").strip():
            self.prompt_restart_thirdparty_flow(provider_error)
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
        health = result.get("health") if isinstance(result.get("health"), dict) else {}
        if health.get("watcher_stale_lock"):
            return "已开启，但后台锁文件异常，建议重新开启自动同步"
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
        settings = result.get("settings") if isinstance(result.get("settings"), dict) else self.current_autosync_settings()
        health = result.get("health") if isinstance(result.get("health"), dict) else {}
        mode_notes = []
        if settings.get("detect_only"):
            mode_notes.append("仅提示")
        if settings.get("auto_fix_chats"):
            mode_notes.append("修聊天")
        if settings.get("auto_fix_projects"):
            mode_notes.append("修项目")
        if settings.get("dual_home"):
            mode_notes.append("双目录")
        mode_text = "，".join(mode_notes) if mode_notes else "仅检测"
        if not result.get("exists"):
            return (
                "未开启",
                f"需要时点“开启自动同步”一次；当前策略：{mode_text}",
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

        health_notes = []
        if health.get("watcher_stale_lock"):
            pid = health.get("watcher_lock_pid")
            health_notes.append(f"发现旧锁文件，PID {pid or '未知'} 未运行；新版会自动清理，建议重新开启一次自动同步")
        if health.get("watcher_log_exists"):
            age = health.get("watcher_log_age_seconds")
            if isinstance(age, (int, float)) and age > 86400:
                days = int(age // 86400)
                health_notes.append(f"后台日志约 {days} 天未更新，可能没有实际运行")
        elif result.get("exists"):
            health_notes.append("尚未发现后台日志，可能需要重新开启后等待首次启动")
        if not health.get("task_launcher_exists", True):
            health_notes.append("启动命令文件缺失，建议重新开启自动同步")
        if health_notes:
            detail += "；" + "；".join(health_notes)

        return (
            "已开启",
            f"{detail}；当前策略：{mode_text}",
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
        settings = result.get("settings")
        if isinstance(settings, dict):
            normalized = windows_autosync_settings.normalize_settings(settings)
            self.auto_detect_var.set(normalized["auto_detect"])
            self.auto_fix_chats_var.set(normalized["auto_fix_chats"])
            self.auto_fix_projects_var.set(normalized["auto_fix_projects"])
            self.dual_home_var.set(normalized["dual_home"])
            self.detect_only_var.set(normalized["detect_only"])
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
        windows_autosync_settings.save_settings(self.current_autosync_settings())
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
