from __future__ import annotations

import os
import threading
import webbrowser
from tkinter import BooleanVar, Canvas, StringVar, ttk
from tkinter import messagebox

from powertrade_crawler.credential_setup import (
    LLM_PLATFORM_GUIDES,
    POWER_CREDENTIAL_GUIDES,
    RouterSnapshot,
    collection_setup_progress,
    inspect_router,
    llm_platform_display_status,
    power_credential_status,
    providers_for_platform,
)
from powertrade_crawler.credentials import save_credential
from powertrade_crawler.llm_router import default_client_env_path
from powertrade_crawler.ui_theme import COLORS
from powertrade_crawler.ui_dispatch import UiLifecycle


LOCAL_ROUTER_URL = "http://127.0.0.1:8317/"


class CredentialSetupApp:
    """New-user credential guide without ever displaying stored secret values."""

    def __init__(self, root: ttk.Frame) -> None:
        self.root = root
        self.ui = UiLifecycle(root)
        self.power_guide_by_key = {guide.key: guide for guide in POWER_CREDENTIAL_GUIDES}
        self.llm_guide_by_key = {guide.key: guide for guide in LLM_PLATFORM_GUIDES}
        self.selected_power_key = "gridstatus"
        self.selected_llm_key = "gemini"
        self.router_snapshot = RouterSnapshot(
            configured=False,
            reachable=False,
            config_path=default_client_env_path(),
            message="正在检测本地免费模型网关…",
        )
        self._router_busy = False
        self._compact_layout: bool | None = None

        collection_required = sum(
            guide.requirement == "collection_required" for guide in POWER_CREDENTIAL_GUIDES
        )
        no_key_sources = sum(
            guide.requirement == "not_required" for guide in POWER_CREDENTIAL_GUIDES
        )
        self.collection_progress_var = StringVar(value=f"0 / {collection_required}")
        self.no_key_var = StringVar(value=f"{no_key_sources} 个")
        self.router_summary_var = StringVar(value="检测中")
        self.provider_count_var = StringVar(value="—")
        self.power_title_var = StringVar()
        self.power_requirement_var = StringVar()
        self.power_capability_var = StringVar()
        self.power_credential_label_var = StringVar()
        self.power_steps_var = StringVar()
        self.power_note_var = StringVar()
        self.power_secret_var = StringVar()
        self.show_power_secret_var = BooleanVar(value=False)
        self._router_status_detail = f"Agent 功能必需 · {self.router_snapshot.message}"
        self._router_status_compact = "Agent 必需 · 正在检测网关…"
        self.router_status_var = StringVar(value=self._router_status_detail)
        self.router_path_var = StringVar(value=str(self.router_snapshot.config_path))
        self.llm_title_var = StringVar()
        self.llm_status_var = StringVar()
        self.llm_key_label_var = StringVar()
        self.llm_steps_var = StringVar()
        self.llm_note_var = StringVar()
        self.llm_channels_var = StringVar()

        self._build()
        self.refresh_power_status()
        self._select_power_guide(self.selected_power_key)
        self._select_llm_guide(self.selected_llm_key)
        self.root.after(80, self.refresh_router_status)
        self.root.bind("<Configure>", self._on_page_configure, add="+")

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        self.intro = ttk.Frame(self.root, style="Card.TFrame", padding=(18, 10))
        self.intro.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 10))
        self.intro.columnconfigure(0, weight=1)
        ttk.Label(
            self.intro,
            text="基础浏览无需任何 API Key",
            style="SectionTitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self.intro_detail_label = ttk.Label(
            self.intro,
            text=(
                "按功能配置：在线采集和 Agent 才需要凭据。选择功能后按步骤申请和保存；"
                "已保存的密钥不会回显。"
            ),
            style="Muted.TLabel",
            wraplength=660,
            justify="left",
        )
        self.intro_detail_label.grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Button(
            self.intro,
            text="刷新全部状态",
            command=self.refresh_all,
        ).grid(row=0, column=1, rowspan=2, padx=(16, 0), sticky="e")

        self.metrics = ttk.Frame(self.root, style="AppSurface.TFrame")
        self.metrics.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 10))
        for column in range(4):
            self.metrics.columnconfigure(column, weight=1, uniform="credential_metrics")
        self._metric_card(
            self.metrics,
            0,
            "采集凭据",
            self.collection_progress_var,
            "3 个在线来源",
        )
        self._metric_card(self.metrics, 1, "无需密钥", self.no_key_var, "2 个公开来源")
        self._metric_card(self.metrics, 2, "Agent 网关", self.router_summary_var, "仅 Agent 需要")
        self._metric_card(self.metrics, 3, "可用免费渠道", self.provider_count_var, "任选一个即可")

        notebook = ttk.Notebook(self.root)
        notebook.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 16))
        power_tab = ttk.Frame(notebook, style="AppSurface.TFrame", padding=(0, 10, 0, 0))
        llm_tab = ttk.Frame(notebook, style="AppSurface.TFrame", padding=(0, 10, 0, 0))
        notebook.add(power_tab, text="电力网站凭据")
        notebook.add(llm_tab, text="LLM / Agent 配置")
        self._build_power_tab(power_tab)
        self._build_llm_tab(llm_tab)

    def _metric_card(
        self,
        parent: ttk.Frame,
        column: int,
        label: str,
        value_var: StringVar,
        detail: str,
    ) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=(14, 9))
        card.grid(
            row=0,
            column=column,
            sticky="nsew",
            padx=(0 if column == 0 else 5, 0 if column == 3 else 5),
        )
        ttk.Label(card, text=label, style="CardMuted.TLabel").pack(anchor="w")
        body = ttk.Frame(card)
        body.pack(fill="x", pady=(1, 0))
        ttk.Label(body, textvariable=value_var, style="KpiValue.TLabel").pack(side="left")
        ttk.Label(body, text=detail, style="CardMuted.TLabel").pack(
            side="right", padx=(8, 0)
        )

    def _on_page_configure(self, event) -> None:
        if event.widget is not self.root:
            return
        compact = event.height < 700
        if compact == self._compact_layout:
            return
        self._compact_layout = compact
        if compact:
            self.metrics.grid_remove()
            self.intro.configure(padding=(14, 7))
            self.intro.grid_configure(pady=(9, 8))
            self.intro_detail_label.configure(
                text="本地浏览无需 Key；在线采集和 Agent 再按功能配置，已保存密钥不会回显。",
                wraplength=650,
            )
            self.router_path_label.grid_remove()
            self.router_status_label.configure(wraplength=275)
            self.router_manage_button.configure(text="管理", style="Compact.TButton", width=4)
            self.router_directory_button.configure(
                text="配置目录", style="Compact.TButton", width=6
            )
            self.router_start_button.configure(text="检测", style="Compact.TButton", width=4)
            self.llm_table_title.configure(text="上游免费平台（任选其一）")
        else:
            self.metrics.grid()
            self.intro.configure(padding=(18, 10))
            self.intro.grid_configure(pady=(14, 10))
            self.intro_detail_label.configure(
                text=(
                    "按功能配置：在线采集和 Agent 才需要凭据。选择功能后按步骤申请和保存；"
                    "已保存的密钥不会回显。"
                ),
                wraplength=660,
            )
            self.router_path_label.grid()
            self.router_status_label.configure(wraplength=500)
            self.router_manage_button.configure(text="打开网关管理页", style="TButton", width=0)
            self.router_directory_button.configure(text="打开配置目录", style="TButton", width=0)
            self.router_start_button.configure(
                text="启动并检测", style="Primary.TButton", width=0
            )
            self.llm_table_title.configure(
                text="上游平台（每个平台都可选，只需至少一个免费渠道可用）"
            )
        self._render_router_status()

    def _render_router_status(self) -> None:
        self.router_status_var.set(
            self._router_status_compact
            if self._compact_layout
            else self._router_status_detail
        )

    def _build_power_tab(self, tab: ttk.Frame) -> None:
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        panes = ttk.Panedwindow(tab, orient="horizontal")
        panes.grid(row=0, column=0, sticky="nsew")
        panes.bind(
            "<Configure>",
            lambda event: self._keep_pane_balance(panes, event.width),
            add="+",
        )

        list_card = ttk.Frame(panes, style="Card.TFrame", padding=(14, 12))
        detail_container = ttk.Frame(panes, style="Card.TFrame")
        panes.add(list_card, weight=6)
        panes.add(detail_container, weight=5)
        detail_card, self.power_detail_canvas = self._scrollable_card(detail_container)
        list_card.columnconfigure(0, weight=1)
        list_card.rowconfigure(2, weight=1)

        ttk.Label(list_card, text="配置清单", style="SectionTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.power_list_hint_label = ttk.Label(
            list_card,
            text="选择数据源，右侧会显示申请、保存和使用步骤。",
            style="Muted.TLabel",
            wraplength=430,
            justify="left",
        )
        self.power_list_hint_label.grid(row=1, column=0, sticky="w", pady=(3, 9))
        tree_frame = ttk.Frame(list_card)
        tree_frame.grid(row=2, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self.power_tree = ttk.Treeview(
            tree_frame,
            columns=("requirement", "status", "source"),
            show="headings",
            selectmode="browse",
        )
        for key, title, width, anchor in (
            ("requirement", "配置要求", 105, "center"),
            ("status", "当前状态", 84, "center"),
            ("source", "数据来源", 190, "w"),
        ):
            self.power_tree.heading(key, text=title)
            self.power_tree.column(key, width=width, minwidth=70, anchor=anchor)
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.power_tree.yview)
        self.power_tree.configure(yscrollcommand=scrollbar.set)
        self.power_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.power_tree.bind("<<TreeviewSelect>>", self._on_power_selection)

        detail_card.columnconfigure(0, weight=1)
        detail_card.rowconfigure(8, weight=1)
        ttk.Label(detail_card, textvariable=self.power_title_var, style="SectionTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            detail_card,
            textvariable=self.power_requirement_var,
            style="Warning.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(
            detail_card,
            textvariable=self.power_capability_var,
            style="Muted.TLabel",
            wraplength=430,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(4, 9))

        link_bar = ttk.Frame(detail_card)
        link_bar.grid(row=3, column=0, sticky="w", pady=(0, 10))
        self.power_account_button = ttk.Button(
            link_bar,
            text="打开账号 / 产品页",
            style="Link.TButton",
        )
        self.power_account_button.pack(side="left")
        self.power_key_button = ttk.Button(
            link_bar,
            text="打开密钥 / 官方说明",
            style="Link.TButton",
        )
        self.power_key_button.pack(side="left", padx=(10, 0))

        ttk.Separator(detail_card).grid(row=4, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(detail_card, text="配置步骤", style="SectionTitle.TLabel").grid(
            row=5, column=0, sticky="w"
        )
        ttk.Label(
            detail_card,
            textvariable=self.power_steps_var,
            wraplength=430,
            justify="left",
        ).grid(row=6, column=0, sticky="nw", pady=(5, 8))
        ttk.Label(
            detail_card,
            textvariable=self.power_note_var,
            style="Muted.TLabel",
            wraplength=430,
            justify="left",
        ).grid(row=7, column=0, sticky="nw", pady=(0, 8))

        self.power_input_frame = ttk.Frame(detail_card)
        self.power_input_frame.grid(row=8, column=0, sticky="sew")
        self.power_input_frame.columnconfigure(0, weight=1)
        ttk.Label(
            self.power_input_frame,
            textvariable=self.power_credential_label_var,
            style="SectionTitle.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        self.power_secret_entry = ttk.Entry(
            self.power_input_frame,
            textvariable=self.power_secret_var,
            show="*",
        )
        self.power_secret_entry.grid(row=1, column=0, sticky="ew", pady=(6, 6))
        self.power_save_button = ttk.Button(
            self.power_input_frame,
            text="安全保存",
            style="Primary.TButton",
            command=self.save_selected_power_credential,
        )
        self.power_save_button.grid(row=1, column=1, padx=(8, 0), pady=(6, 6))
        ttk.Checkbutton(
            self.power_input_frame,
            text="临时显示本次输入",
            variable=self.show_power_secret_var,
            command=self._toggle_power_secret,
        ).grid(row=2, column=0, sticky="w")
        ttk.Label(
            self.power_input_frame,
            text="保存到项目 .auth/credentials.json（已被 Git 忽略），页面不会读取或显示原值。",
            style="CardMuted.TLabel",
            wraplength=420,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(3, 0))

        self.power_no_input_label = ttk.Label(
            detail_card,
            text="✓ 此数据源无需填写密钥，可直接使用在线采集。",
            style="Success.TLabel",
        )
        self._bind_detail_mousewheel(detail_card, self.power_detail_canvas)

    def _build_llm_tab(self, tab: ttk.Frame) -> None:
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        panes = ttk.Panedwindow(tab, orient="horizontal")
        panes.grid(row=0, column=0, sticky="nsew")
        panes.bind(
            "<Configure>",
            lambda event: self._keep_pane_balance(panes, event.width),
            add="+",
        )
        list_card = ttk.Frame(panes, style="Card.TFrame", padding=(14, 12))
        detail_container = ttk.Frame(panes, style="Card.TFrame")
        panes.add(list_card, weight=6)
        panes.add(detail_container, weight=5)
        detail_card, self.llm_detail_canvas = self._scrollable_card(detail_container)

        list_card.columnconfigure(0, weight=1)
        list_card.rowconfigure(5, weight=1)
        ttk.Label(list_card, text="本机免费模型网关", style="SectionTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.router_status_label = ttk.Label(
            list_card,
            textvariable=self.router_status_var,
            style="Muted.TLabel",
            wraplength=500,
            justify="left",
        )
        self.router_status_label.grid(row=1, column=0, sticky="w", pady=(4, 2))
        self.router_path_label = ttk.Label(
            list_card,
            textvariable=self.router_path_var,
            style="CardMuted.TLabel",
            wraplength=570,
            justify="left",
        )
        self.router_path_label.grid(row=2, column=0, sticky="w")
        self.router_buttons = ttk.Frame(list_card)
        self.router_buttons.grid(row=3, column=0, sticky="w", pady=(8, 12))
        self.router_manage_button = ttk.Button(
            self.router_buttons,
            text="打开网关管理页",
            command=lambda: self._open_url(LOCAL_ROUTER_URL),
        )
        self.router_manage_button.pack(side="left")
        self.router_directory_button = ttk.Button(
            self.router_buttons,
            text="打开配置目录",
            command=self._open_router_config_directory,
        )
        self.router_directory_button.pack(side="left", padx=(7, 0))
        self.router_start_button = ttk.Button(
            self.router_buttons,
            text="启动并检测",
            style="Primary.TButton",
            command=lambda: self._refresh_router_async(start_if_needed=True),
        )
        self.router_start_button.pack(side="left", padx=(7, 0))

        self.llm_table_title = ttk.Label(
            list_card,
            text="上游平台（每个平台都可选，只需至少一个免费渠道可用）",
            style="SectionTitle.TLabel",
        )
        self.llm_table_title.grid(row=4, column=0, sticky="w", pady=(0, 7))
        tree_frame = ttk.Frame(list_card)
        tree_frame.grid(row=5, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self.llm_tree = ttk.Treeview(
            tree_frame,
            columns=("status", "platform"),
            show="headings",
            selectmode="browse",
        )
        for key, title, width, anchor in (
            ("status", "网关状态", 105, "center"),
            ("platform", "LLM 平台", 175, "w"),
        ):
            self.llm_tree.heading(key, text=title)
            self.llm_tree.column(key, width=width, minwidth=85, anchor=anchor)
        llm_scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.llm_tree.yview)
        self.llm_tree.configure(yscrollcommand=llm_scrollbar.set)
        self.llm_tree.grid(row=0, column=0, sticky="nsew")
        llm_scrollbar.grid(row=0, column=1, sticky="ns")
        self.llm_tree.bind("<<TreeviewSelect>>", self._on_llm_selection)

        detail_card.columnconfigure(0, weight=1)
        detail_card.rowconfigure(9, weight=1)
        ttk.Label(detail_card, textvariable=self.llm_title_var, style="SectionTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(detail_card, text="单个平台：可选", style="Warning.TLabel").grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )
        ttk.Label(
            detail_card,
            textvariable=self.llm_status_var,
            style="Muted.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(3, 8))
        ttk.Label(detail_card, textvariable=self.llm_key_label_var).grid(
            row=3, column=0, sticky="w"
        )
        ttk.Button(
            detail_card,
            text="打开官方密钥页面",
            style="Link.TButton",
            command=self._open_selected_llm_key_url,
        ).grid(row=4, column=0, sticky="w", pady=(4, 10))
        ttk.Separator(detail_card).grid(row=5, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(detail_card, text="配置步骤", style="SectionTitle.TLabel").grid(
            row=6, column=0, sticky="w"
        )
        ttk.Label(
            detail_card,
            textvariable=self.llm_steps_var,
            wraplength=430,
            justify="left",
        ).grid(row=7, column=0, sticky="nw", pady=(5, 8))
        ttk.Label(
            detail_card,
            textvariable=self.llm_note_var,
            style="Muted.TLabel",
            wraplength=430,
            justify="left",
        ).grid(row=8, column=0, sticky="nw")
        ttk.Label(
            detail_card,
            textvariable=self.llm_channels_var,
            style="CardMuted.TLabel",
            wraplength=430,
            justify="left",
        ).grid(row=9, column=0, sticky="sw", pady=(10, 0))

        safety = ttk.Frame(detail_card, style="Card.TFrame", padding=(10, 8))
        safety.grid(row=10, column=0, sticky="ew", pady=(12, 0))
        ttk.Label(
            safety,
            text=(
                "安全边界：上游平台 Key 只在本机网关管理页配置，不会保存到本项目、"
                "SQLite、提示词、日志或 Git；smart-auto 不会自动调用付费模型。"
            ),
            style="Muted.TLabel",
            wraplength=420,
            justify="left",
        ).pack(anchor="w")
        self._bind_detail_mousewheel(detail_card, self.llm_detail_canvas)

    @staticmethod
    def _scrollable_card(container: ttk.Frame) -> tuple[ttk.Frame, Canvas]:
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        canvas = Canvas(
            container,
            background=COLORS["surface"],
            borderwidth=0,
            highlightthickness=0,
        )
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        content = ttk.Frame(canvas, style="CardBody.TFrame", padding=(18, 14))
        window_id = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(window_id, width=event.width),
        )
        return content, canvas

    @classmethod
    def _bind_detail_mousewheel(cls, widget, canvas: Canvas) -> None:
        def scroll(event) -> str:
            direction = -1 if event.delta > 0 else 1
            canvas.yview_scroll(direction * 3, "units")
            return "break"

        widget.bind("<MouseWheel>", scroll, add="+")
        for child in widget.winfo_children():
            cls._bind_detail_mousewheel(child, canvas)

    @staticmethod
    def _keep_pane_balance(panes: ttk.Panedwindow, width: int) -> None:
        if width < 20:
            return
        left_width = max(360, min(int(width * 0.44), width - 440))
        if left_width > 0:
            panes.sashpos(0, left_width)

    def refresh_all(self) -> None:
        self.refresh_power_status()
        self._refresh_router_async(start_if_needed=False)

    def refresh_power_status(self) -> None:
        selected = self.selected_power_key
        for iid in self.power_tree.get_children():
            self.power_tree.delete(iid)
        for guide in POWER_CREDENTIAL_GUIDES:
            _status, configured = power_credential_status(guide)
            requirement = (
                "采集必需"
                if guide.requirement == "collection_required"
                else guide.requirement_label
            )
            status_text = "— 无需" if configured is None else "✓ 已配置" if configured else "! 待配置"
            source = {
                "elecheck": "Elecheck 易能",
                "gzpec": "广州交易中心",
            }.get(guide.key, guide.source)
            self.power_tree.insert(
                "",
                "end",
                iid=guide.key,
                values=(requirement, status_text, source),
            )
        configured, required = collection_setup_progress()
        self.collection_progress_var.set(f"{configured} / {required}")
        if selected in self.power_guide_by_key:
            self.power_tree.selection_set(selected)
            self.power_tree.focus(selected)

    def _on_power_selection(self, _event=None) -> None:
        selection = self.power_tree.selection()
        if selection:
            self._select_power_guide(selection[0])

    def _select_power_guide(self, key: str) -> None:
        guide = self.power_guide_by_key[key]
        self.selected_power_key = key
        self.power_detail_canvas.yview_moveto(0)
        status, _configured = power_credential_status(guide)
        self.power_title_var.set(guide.source)
        self.power_requirement_var.set(f"{guide.requirement_label}  ·  {status}")
        self.power_capability_var.set(f"用途：{guide.capability}")
        self.power_credential_label_var.set(guide.credential_label)
        self.power_steps_var.set(self._numbered_steps(guide.steps))
        self.power_note_var.set(f"说明：{guide.note}")
        self.power_secret_var.set("")
        self.show_power_secret_var.set(False)
        self._toggle_power_secret()
        self._configure_link_button(self.power_account_button, guide.account_url)
        self._configure_link_button(self.power_key_button, guide.credential_url)
        if guide.credential_name is None:
            self.power_input_frame.grid_remove()
            self.power_no_input_label.grid(row=8, column=0, sticky="sw", pady=(8, 0))
        else:
            self.power_no_input_label.grid_remove()
            self.power_input_frame.grid()

    def _configure_link_button(self, button: ttk.Button, url: str | None) -> None:
        if url:
            button.configure(state="normal", command=lambda target=url: self._open_url(target))
        else:
            button.configure(state="disabled", command=lambda: None)

    def _toggle_power_secret(self) -> None:
        self.power_secret_entry.configure(show="" if self.show_power_secret_var.get() else "*")

    def save_selected_power_credential(self) -> None:
        guide = self.power_guide_by_key[self.selected_power_key]
        if guide.credential_name is None:
            return
        value = self.power_secret_var.get().strip()
        if not value:
            messagebox.showwarning(
                "尚未填写凭据",
                f"请粘贴完整的 {guide.credential_label}。",
                parent=self.root,
            )
            return
        try:
            path = save_credential(guide.credential_name, value)
        except (OSError, ValueError) as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.root)
            return
        self.power_secret_var.set("")
        self.show_power_secret_var.set(False)
        self._toggle_power_secret()
        self.refresh_power_status()
        self._select_power_guide(guide.key)
        messagebox.showinfo(
            "保存成功",
            f"{guide.credential_label} 已安全保存到：\n{path}\n\n页面不会回显凭据内容。",
            parent=self.root,
        )

    def refresh_router_status(self) -> None:
        self._refresh_router_async(start_if_needed=False)

    def _refresh_router_async(self, *, start_if_needed: bool) -> None:
        if self._router_busy:
            return
        self._router_busy = True
        self.router_start_button.configure(state="disabled")
        action = "正在启动并检测本地网关…" if start_if_needed else "正在检测本地网关…"
        self._router_status_detail = f"Agent 功能必需 · {action}"
        self._router_status_compact = "Agent 必需 · 正在启动并检测…" if start_if_needed else "Agent 必需 · 正在检测…"
        self._render_router_status()

        def worker() -> None:
            try:
                snapshot = inspect_router(start_if_needed=start_if_needed)
            except Exception as exc:
                self.ui.post(self._apply_router_error, str(exc))
            else:
                self.ui.post(self._apply_router_snapshot, snapshot)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_router_error(self, message: str) -> None:
        self._router_busy = False
        self.router_start_button.configure(state="normal")
        self._router_status_detail = f"Agent 功能必需 · 网关检测失败：{message}"
        self._router_status_compact = "Agent 必需 · 检测失败"
        self._render_router_status()

    def _apply_router_snapshot(self, snapshot: RouterSnapshot) -> None:
        self._router_busy = False
        self.router_start_button.configure(state="normal")
        self.router_snapshot = snapshot
        self._router_status_detail = f"Agent 功能必需 · {snapshot.message}"
        self._router_status_compact = (
            f"Agent 必需 · 已连接 · {snapshot.available_provider_count} 可用"
            if snapshot.reachable
            else "Agent 必需 · 已配置，网关未运行"
            if snapshot.configured
            else "Agent 必需 · 待安装或配置"
        )
        self._render_router_status()
        self.router_path_var.set(f"客户端配置：{snapshot.config_path}")
        self.router_summary_var.set(
            "已连接" if snapshot.reachable else "已配置 · 未运行" if snapshot.configured else "待安装 / 配置"
        )
        self.provider_count_var.set(
            f"{snapshot.available_provider_count} 个" if snapshot.reachable else "—"
        )
        selected = self.selected_llm_key
        for iid in self.llm_tree.get_children():
            self.llm_tree.delete(iid)
        for guide in LLM_PLATFORM_GUIDES:
            matches = providers_for_platform(guide, snapshot.providers)
            status = llm_platform_display_status(guide, snapshot)
            table_status = (
                "✓ 可用"
                if status == "已接入 · 可用"
                else "! 不可用"
                if matches
                else "? 待检测"
                if not snapshot.reachable
                else "— 可选"
            )
            self.llm_tree.insert(
                "",
                "end",
                iid=guide.key,
                values=(table_status, guide.platform),
            )
        self.llm_tree.selection_set(selected)
        self.llm_tree.focus(selected)
        self._select_llm_guide(selected)

    def _on_llm_selection(self, _event=None) -> None:
        selection = self.llm_tree.selection()
        if selection:
            self._select_llm_guide(selection[0])

    def _select_llm_guide(self, key: str) -> None:
        guide = self.llm_guide_by_key[key]
        self.selected_llm_key = key
        self.llm_detail_canvas.yview_moveto(0)
        matches = providers_for_platform(guide, self.router_snapshot.providers)
        self.llm_title_var.set(guide.platform)
        self.llm_status_var.set(llm_platform_display_status(guide, self.router_snapshot))
        self.llm_key_label_var.set(f"凭据类型：{guide.credential_label}")
        self.llm_steps_var.set(self._numbered_steps(guide.steps))
        note = guide.note or "免费额度、地区可用性和限流规则以平台当前页面为准。"
        self.llm_note_var.set(f"说明：{note}")
        if matches:
            channel_lines = [
                f"• {row.get('name') or row.get('id')}："
                f"{'可用' if row.get('available') is True else row.get('status') or '不可用'}"
                for row in matches
            ]
            self.llm_channels_var.set("本机网关中的渠道：\n" + "\n".join(channel_lines))
        elif not self.router_snapshot.reachable:
            self.llm_channels_var.set("网关尚未连接，暂时无法判断该平台是否已经接入。")
        else:
            self.llm_channels_var.set("本机网关尚未接入该平台；这不会影响其他可用渠道。")

    def _open_selected_llm_key_url(self) -> None:
        self._open_url(self.llm_guide_by_key[self.selected_llm_key].credential_url)

    def _open_router_config_directory(self) -> None:
        directory = self.router_snapshot.config_path.parent
        if not directory.exists():
            messagebox.showwarning(
                "配置目录不存在",
                (
                    f"尚未找到 {directory}。\n\n"
                    "请先由项目管理员安装本机 llm-router，再返回本页启动并检测。"
                ),
                parent=self.root,
            )
            return
        try:
            if os.name == "nt":
                os.startfile(directory)  # type: ignore[attr-defined]
            else:
                webbrowser.open(directory.resolve().as_uri())
        except OSError as exc:
            messagebox.showerror("无法打开目录", str(exc), parent=self.root)

    @staticmethod
    def _open_url(url: str) -> None:
        webbrowser.open_new_tab(url)

    @staticmethod
    def _numbered_steps(steps: tuple[str, ...]) -> str:
        return "\n".join(f"{index}. {step}" for index, step in enumerate(steps, start=1))
