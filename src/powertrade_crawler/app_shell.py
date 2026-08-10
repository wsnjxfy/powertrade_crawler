from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tkinter import StringVar, Tk, messagebox, ttk
from typing import Any

from powertrade_crawler.ui_theme import FONT_FAMILY


@dataclass(frozen=True)
class PageSpec:
    key: str
    title: str
    subtitle: str
    navigation_label: str
    compact_label: str
    group: str


PAGE_SPECS = (
    PageSpec("overview", "数据总览", "跨来源概况与快捷任务", "⌂  数据总览", "⌂", "工作台"),
    PageSpec("gridstatus", "GridStatus", "北美目录与数据下载", "GS  GridStatus", "GS", "数据来源"),
    PageSpec("elecheck", "Elecheck 易能电易查", "现货、代理购电、增量机制与 Agent", "EL  Elecheck", "EL", "数据来源"),
    PageSpec("entsoe", "ENTSO-E 欧洲", "欧洲市场数据采集与浏览", "EU  ENTSO-E", "EU", "数据来源"),
    PageSpec("elexon", "Elexon 英国", "英国市场数据采集与浏览", "GB  Elexon", "GB", "数据来源"),
    PageSpec("agent", "多数据源 Agent", "跨来源查询、比较与受控采集", "AI  多源 Agent", "AI", "智能与自动化"),
    PageSpec("schedule", "定时任务 / 数据维护", "任务、运行记录与数据库维护", "TM  任务 / 维护", "TM", "智能与自动化"),
    PageSpec(
        "setup",
        "API 配置向导",
        "新用户账号、数据源凭据与免费模型网关配置",
        "KEY  API 配置",
        "KEY",
        "系统设置",
    ),
)

PAGE_SPEC_BY_KEY = {spec.key: spec for spec in PAGE_SPECS}


def resolve_quick_navigation(query: str) -> str | None:
    normalized = query.strip().lower().replace(" ", "")
    if not normalized:
        return None
    aliases = (
        ("overview", ("总览", "首页", "概况", "home", "overview")),
        ("gridstatus", ("gridstatus", "北美", "ercot", "pjm", "caiso")),
        ("elecheck", ("elecheck", "易能", "现货", "代理购电", "增量机制", "国内")),
        ("entsoe", ("entso-e", "entsoe", "欧洲", "德国", "法国")),
        ("elexon", ("elexon", "英国", "bmu", "结算价格")),
        ("setup", ("api", "key", "密钥", "凭据", "配置", "设置", "新用户")),
        ("agent", ("agent", "智能", "问答", "比较", "对话")),
        ("schedule", ("定时", "任务", "维护", "调度", "schedule", "数据库")),
    )
    for page_key, keywords in aliases:
        if any(keyword in normalized for keyword in keywords):
            return page_key
    return None


class PowertradeAppShell:
    def __init__(self, root: Tk, db_path: Path) -> None:
        self.root = root
        self.db_path = db_path
        self.pages: dict[str, ttk.Frame] = {}
        self.controllers: dict[str, Any] = {}
        self.nav_buttons: dict[str, ttk.Button] = {}
        self.group_labels: list[ttk.Label] = []
        self.current_page = "overview"
        self.sidebar_collapsed = False
        self._manual_sidebar_state: bool | None = None
        self._auto_sidebar_compact = False
        self._last_compact_state: bool | None = None
        self.page_title_var = StringVar(value=PAGE_SPEC_BY_KEY["overview"].title)
        self.page_subtitle_var = StringVar(value=PAGE_SPEC_BY_KEY["overview"].subtitle)
        self.quick_nav_var = StringVar(value="搜索功能或页面…")
        self.source_status_var = StringVar(value="5 类数据源已接入")
        self.activity_var = StringVar(value="就绪")
        self._build_shell()
        self._bind_shortcuts()
        self.root.after(120, self.refresh_source_status)

    def _build_shell(self) -> None:
        outer = ttk.Frame(self.root, style="AppShell.TFrame")
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        self.sidebar = ttk.Frame(outer, style="Sidebar.TFrame", width=224)
        self.sidebar.grid(row=0, column=0, sticky="nsw")
        self.sidebar.grid_propagate(False)
        self.sidebar.columnconfigure(0, weight=1)

        brand = ttk.Frame(self.sidebar, style="Sidebar.TFrame", padding=(14, 15, 8, 13))
        brand.grid(row=0, column=0, sticky="ew")
        brand.columnconfigure(0, weight=1)
        self.brand_label = ttk.Label(
            brand,
            text="Powertrade",
            style="SidebarBrand.TLabel",
        )
        self.brand_label.grid(row=0, column=0, sticky="w")
        self.brand_subtitle = ttk.Label(
            brand,
            text="电力市场工作台",
            style="SidebarSubtitle.TLabel",
        )
        self.brand_subtitle.grid(row=1, column=0, sticky="w", pady=(3, 0))
        self.collapse_button = ttk.Button(
            brand,
            text="‹",
            width=2,
            style="SidebarLink.TButton",
            command=self.toggle_sidebar,
        )
        self.collapse_button.grid(row=0, column=1, rowspan=2, sticky="e")

        row_index = 1
        current_group: str | None = None
        for spec in PAGE_SPECS:
            if spec.group != current_group:
                current_group = spec.group
                group = ttk.Label(
                    self.sidebar,
                    text=current_group,
                    style="SidebarGroup.TLabel",
                )
                group.grid(row=row_index, column=0, sticky="w", padx=22, pady=(8, 3))
                self.group_labels.append(group)
                row_index += 1
            button = ttk.Button(
                self.sidebar,
                text=spec.navigation_label,
                style="Nav.TButton",
                command=lambda key=spec.key: self.show_page(key),
            )
            button.grid(row=row_index, column=0, sticky="ew", padx=12, pady=1)
            self.nav_buttons[spec.key] = button
            row_index += 1

        self.sidebar.rowconfigure(row_index, weight=1)
        self.sidebar_status = ttk.Frame(
            self.sidebar,
            style="Sidebar.TFrame",
            padding=(20, 12, 12, 16),
        )
        self.sidebar_status.grid(row=row_index + 1, column=0, sticky="sew")
        self.gateway_label = ttk.Label(
            self.sidebar_status,
            text="● 免费模型网关",
            style="SidebarSubtitle.TLabel",
            font=(FONT_FAMILY, 8, "bold"),
        )
        self.gateway_label.pack(anchor="w")
        self.gateway_detail = ttk.Label(
            self.sidebar_status,
            text="smart-auto · 免费",
            style="SidebarSubtitle.TLabel",
        )
        self.gateway_detail.pack(anchor="w", pady=(4, 0))

        main = ttk.Frame(outer, style="AppShell.TFrame")
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)

        header = ttk.Frame(main, style="Header.TFrame", height=64, padding=(18, 9))
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.columnconfigure(0, weight=1, minsize=220)
        title_box = ttk.Frame(header, style="Header.TFrame")
        title_box.grid(row=0, column=0, rowspan=2, sticky="w")
        ttk.Label(title_box, textvariable=self.page_title_var, style="HeaderTitle.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            title_box,
            textvariable=self.page_subtitle_var,
            style="HeaderSubtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        self.quick_nav_entry = ttk.Entry(
            header,
            textvariable=self.quick_nav_var,
            width=16,
        )
        self.quick_nav_entry.grid(row=0, column=1, rowspan=2, padx=(18, 10), sticky="e")
        self.quick_nav_entry.bind("<FocusIn>", self._clear_quick_placeholder)
        self.quick_nav_entry.bind("<FocusOut>", self._restore_quick_placeholder)
        self.quick_nav_entry.bind("<Return>", self._quick_navigate)

        status_pill = ttk.Frame(header, style="SuccessPill.TFrame", padding=(10, 6))
        status_pill.grid(row=0, column=2, rowspan=2, padx=(0, 8), sticky="e")
        ttk.Label(
            status_pill,
            textvariable=self.source_status_var,
            style="SuccessPill.TLabel",
        ).pack()
        self.refresh_status_button = ttk.Button(
            header,
            text="刷新状态",
            command=self.refresh_source_status,
        )
        self.refresh_status_button.grid(
            row=0,
            column=3,
            rowspan=2,
            padx=(0, 7),
        )
        ttk.Button(header, text="帮助", command=self.show_help).grid(row=0, column=4, rowspan=2)

        self.page_container = ttk.Frame(main, style="AppSurface.TFrame")
        self.page_container.grid(row=1, column=0, sticky="nsew")
        self.page_container.columnconfigure(0, weight=1)
        self.page_container.rowconfigure(0, weight=1)
        for spec in PAGE_SPECS:
            frame = ttk.Frame(self.page_container, style="AppSurface.TFrame")
            frame.grid(row=0, column=0, sticky="nsew")
            self.pages[spec.key] = frame

        status = ttk.Frame(main, style="Status.TFrame", padding=(12, 4))
        status.grid(row=2, column=0, sticky="ew")
        ttk.Label(
            status,
            textvariable=self.activity_var,
            style="Status.TLabel",
        ).pack(side="left")
        ttk.Label(
            status,
            text=f"SQLite · {self.db_path.name}",
            style="Status.TLabel",
        ).pack(side="right")

        self.root.bind("<Configure>", self._on_root_configure, add="+")
        self.show_page("overview")

    def page(self, key: str) -> ttk.Frame:
        return self.pages[key]

    def register_controller(self, key: str, controller: Any) -> None:
        self.controllers[key] = controller

    def show_page(self, key: str) -> None:
        if key not in self.pages:
            raise KeyError(f"Unknown UI page: {key}")
        self.current_page = key
        spec = PAGE_SPEC_BY_KEY[key]
        self.pages[key].tkraise()
        self.page_title_var.set(spec.title)
        self.page_subtitle_var.set(spec.subtitle)
        self.activity_var.set(f"已打开：{spec.title}")
        for page_key, button in self.nav_buttons.items():
            selected = page_key == key
            if self.sidebar_collapsed:
                style = "Compact.NavSelected.TButton" if selected else "Compact.Nav.TButton"
            else:
                style = "NavSelected.TButton" if selected else "Nav.TButton"
            button.configure(style=style)

    def open_agent_with_prompt(self, prompt: str) -> None:
        self.show_page("agent")
        controller = self.controllers.get("agent")
        if controller is not None and hasattr(controller, "prefill_prompt"):
            controller.prefill_prompt(prompt)
            self.activity_var.set("问题已填入多数据源 Agent，确认后即可发送。")

    def toggle_sidebar(self) -> None:
        if self._auto_sidebar_compact:
            return
        self._manual_sidebar_state = not self.sidebar_collapsed
        self._set_sidebar_collapsed(self._manual_sidebar_state)

    def _on_root_configure(self, event) -> None:
        if event.widget is not self.root:
            return
        self._auto_sidebar_compact = event.width < 1180
        self.collapse_button.configure(
            state="disabled" if self._auto_sidebar_compact else "normal"
        )
        should_collapse = self._auto_sidebar_compact
        if not self._auto_sidebar_compact and self._manual_sidebar_state is not None:
            should_collapse = self._manual_sidebar_state
        if should_collapse != self._last_compact_state:
            self._last_compact_state = should_collapse
            self._set_sidebar_collapsed(should_collapse)

    def _set_sidebar_collapsed(self, collapsed: bool) -> None:
        self.sidebar_collapsed = collapsed
        self.sidebar.configure(width=72 if collapsed else 224)
        self.brand_label.configure(text="P" if collapsed else "Powertrade")
        self.brand_subtitle.configure(text="" if collapsed else "电力市场工作台")
        self.collapse_button.configure(text="›" if collapsed else "‹")
        for group in self.group_labels:
            if collapsed:
                group.grid_remove()
            else:
                group.grid()
        for spec in PAGE_SPECS:
            selected = spec.key == self.current_page
            self.nav_buttons[spec.key].configure(
                text=spec.compact_label if collapsed else spec.navigation_label,
                style=(
                    "Compact.NavSelected.TButton"
                    if collapsed and selected
                    else "Compact.Nav.TButton"
                    if collapsed
                    else "NavSelected.TButton"
                    if selected
                    else "Nav.TButton"
                ),
            )
        if collapsed:
            self.gateway_label.configure(text="●")
            self.gateway_detail.configure(text="")
            self.source_status_var.set("5 类已接入")
            self.refresh_status_button.configure(text="刷新")
        else:
            self.gateway_label.configure(text="● 免费模型网关")
            self.gateway_detail.configure(text="smart-auto · 免费")
            self.source_status_var.set("5 类数据源已接入")
            self.refresh_status_button.configure(text="刷新状态")

    def _clear_quick_placeholder(self, _event=None) -> None:
        if self.quick_nav_var.get() == "搜索功能或页面…":
            self.quick_nav_var.set("")

    def _restore_quick_placeholder(self, _event=None) -> None:
        if not self.quick_nav_var.get().strip():
            self.quick_nav_var.set("搜索功能或页面…")

    def _quick_navigate(self, _event=None) -> str:
        query = self.quick_nav_var.get()
        target = resolve_quick_navigation(query)
        if target is None:
            messagebox.showinfo(
                "未找到页面",
                "可搜索：总览、各数据源、Agent、定时任务、数据维护或 API 配置。",
                parent=self.root,
            )
        else:
            self.show_page(target)
            self.quick_nav_var.set("")
            self.quick_nav_entry.focus_set()
        return "break"

    def refresh_source_status(self) -> None:
        self.source_status_var.set("5 类已接入" if self.sidebar_collapsed else "5 类数据源已接入")
        overview = self.controllers.get("overview")
        if overview is not None and hasattr(overview, "refresh"):
            overview.refresh()

    def show_help(self) -> None:
        messagebox.showinfo(
            "Powertrade Crawler 使用帮助",
            (
                "左侧导航可进入全部数据源、Agent 和定时任务。\n\n"
                "Ctrl+K：聚焦快速跳转\n"
                "Ctrl+1～8：切换主要页面\n"
                "API 配置向导：查看按功能必需、可选与无需密钥的项目\n"
                "数据采集、导出和维护入口保留在各业务页面中。\n"
                "所有会写入业务数据的 Agent 操作仍需审批。"
            ),
            parent=self.root,
        )

    def _bind_shortcuts(self) -> None:
        self.root.bind_all("<Control-k>", self._focus_quick_navigation, add="+")
        self.root.bind_all("<Control-K>", self._focus_quick_navigation, add="+")
        for index, spec in enumerate(PAGE_SPECS, start=1):
            self.root.bind_all(
                f"<Control-Key-{index}>",
                lambda _event, key=spec.key: self.show_page(key),
                add="+",
            )

    def _focus_quick_navigation(self, _event=None) -> str:
        self.quick_nav_entry.focus_set()
        self._clear_quick_placeholder()
        return "break"
