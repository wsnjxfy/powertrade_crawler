from __future__ import annotations

from tkinter import Text, Tk, Widget, ttk


COLORS = {
    "navy": "#102A43",
    "navy_hover": "#183B59",
    "primary": "#2563EB",
    "primary_hover": "#1D4ED8",
    "cyan": "#0891B2",
    "shell": "#F3F6FA",
    "surface": "#FFFFFF",
    "surface_alt": "#F7F9FC",
    "border": "#DCE4EC",
    "border_strong": "#C8D3E0",
    "text": "#132238",
    "text_soft": "#425168",
    "muted": "#718096",
    "nav_text": "#D6E4F1",
    "nav_muted": "#8FB3D1",
    "success": "#168554",
    "success_soft": "#E7F7EF",
    "warning": "#A96500",
    "warning_soft": "#FFF4DF",
    "danger": "#B64242",
    "danger_hover": "#983535",
    "danger_soft": "#FBEAEA",
    "disabled": "#A5B2C1",
}

FONT_FAMILY = "Microsoft YaHei UI"


PRIMARY_BUTTON_TEXTS = {
    "搜索",
    "更新数据集目录",
    "下载 CSV",
    "执行爬取",
    "发送",
    "发送请求",
    "刷新分析",
    "创建任务",
    "立即运行",
    "开始下载",
    "保存并继续",
    "批准",
    "批准并执行",
    "新建会话",
    "新建采集任务",
    "提问",
    "刷新总览",
    "开始采集",
    "采集全部",
    "爬取全部数据",
    "创建每日自动更新",
    "安全保存",
    "保存",
    "保存并测试",
    "启动并检测",
    "连接检查",
}

DANGER_BUTTON_TEXTS = {
    "清除数据",
    "清空数据",
    "删除",
    "删除任务",
    "放弃采集",
    "拒绝",
    "拒绝操作",
    "清除",
}

WARNING_BUTTON_TEXTS = {
    "停止",
    "结束并保存",
    "卸载 Windows 任务",
}


def configure_app_theme(root: Tk) -> ttk.Style:
    """Configure the shared desktop theme without introducing external UI dependencies."""
    colors = COLORS
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    root.configure(background=colors["shell"])
    root.option_add("*Font", (FONT_FAMILY, 9))
    root.option_add("*Text.background", colors["surface"])
    root.option_add("*Text.foreground", colors["text"])
    root.option_add("*Text.insertBackground", colors["primary"])
    root.option_add("*Text.selectBackground", "#DCE7FF")
    root.option_add("*Text.selectForeground", colors["text"])
    root.option_add("*Listbox.background", colors["surface"])
    root.option_add("*Listbox.foreground", colors["text"])
    root.option_add("*Listbox.selectBackground", colors["primary"])
    root.option_add("*Listbox.selectForeground", "#FFFFFF")

    style.configure("TFrame", background=colors["surface"])
    style.configure("AppShell.TFrame", background=colors["shell"])
    style.configure("AppSurface.TFrame", background=colors["shell"])
    style.configure("Toolbar.TFrame", background=colors["surface_alt"], padding=(10, 8))
    style.configure(
        "Card.TFrame",
        background=colors["surface"],
        bordercolor=colors["border"],
        borderwidth=1,
        relief="solid",
    )
    style.configure("CardBody.TFrame", background=colors["surface"])

    style.configure(
        "TLabel",
        background=colors["surface"],
        foreground=colors["text"],
        font=(FONT_FAMILY, 9),
    )
    style.configure(
        "PageTitle.TLabel",
        background=colors["shell"],
        foreground=colors["text"],
        font=(FONT_FAMILY, 16, "bold"),
    )
    style.configure(
        "PageSubtitle.TLabel",
        background=colors["shell"],
        foreground=colors["muted"],
        font=(FONT_FAMILY, 9),
    )
    style.configure(
        "SectionTitle.TLabel",
        background=colors["surface"],
        foreground=colors["text"],
        font=(FONT_FAMILY, 10, "bold"),
    )
    style.configure(
        "Muted.TLabel",
        background=colors["surface"],
        foreground=colors["muted"],
        font=(FONT_FAMILY, 9),
    )
    style.configure(
        "CardMuted.TLabel",
        background=colors["surface"],
        foreground=colors["muted"],
        font=(FONT_FAMILY, 8),
    )
    style.configure(
        "KpiValue.TLabel",
        background=colors["surface"],
        foreground=colors["text"],
        font=(FONT_FAMILY, 18, "bold"),
    )
    style.configure(
        "Success.TLabel",
        background=colors["surface"],
        foreground=colors["success"],
    )
    style.configure(
        "Warning.TLabel",
        background=colors["surface"],
        foreground=colors["warning"],
    )
    style.configure(
        "Danger.TLabel",
        background=colors["surface"],
        foreground=colors["danger"],
    )
    style.configure(
        "Info.TLabel",
        background=colors["surface"],
        foreground=colors["primary"],
    )

    style.configure(
        "TButton",
        background=colors["surface"],
        foreground=colors["text_soft"],
        bordercolor=colors["border_strong"],
        lightcolor=colors["surface"],
        darkcolor=colors["border_strong"],
        padding=(11, 7),
        relief="flat",
        font=(FONT_FAMILY, 9),
    )
    style.map(
        "TButton",
        background=[("pressed", "#E5EBF2"), ("active", "#EFF3F7")],
        foreground=[("disabled", colors["disabled"])],
        bordercolor=[("focus", colors["primary"]), ("active", colors["border_strong"])],
    )
    style.configure("Compact.TButton", padding=(8, 3), font=(FONT_FAMILY, 8))
    style.configure(
        "TMenubutton",
        background=colors["surface"],
        foreground=colors["text_soft"],
        bordercolor=colors["border_strong"],
        arrowcolor=colors["muted"],
        padding=(11, 7),
        relief="flat",
    )
    style.map("TMenubutton", background=[("active", "#EFF3F7")])
    style.configure(
        "Primary.TButton",
        background=colors["primary"],
        foreground="#FFFFFF",
        bordercolor=colors["primary"],
        lightcolor=colors["primary"],
        darkcolor=colors["primary"],
        padding=(12, 7),
        font=(FONT_FAMILY, 9, "bold"),
    )
    style.map(
        "Primary.TButton",
        background=[("pressed", "#1E40AF"), ("active", colors["primary_hover"])],
        foreground=[("disabled", "#D5DEEA"), ("!disabled", "#FFFFFF")],
    )
    style.configure(
        "Danger.TButton",
        background=colors["surface"],
        foreground=colors["danger"],
        bordercolor="#E4BABA",
        padding=(11, 7),
        font=(FONT_FAMILY, 9, "bold"),
    )
    style.map(
        "Danger.TButton",
        background=[("pressed", "#F4D5D5"), ("active", colors["danger_soft"])],
        foreground=[("disabled", colors["disabled"]), ("!disabled", colors["danger"])],
    )
    style.configure("Compact.Danger.TButton", padding=(8, 3), font=(FONT_FAMILY, 8, "bold"))
    style.configure(
        "Warning.TButton",
        background=colors["warning_soft"],
        foreground=colors["warning"],
        bordercolor="#E8C98F",
        padding=(11, 7),
        font=(FONT_FAMILY, 9, "bold"),
    )
    style.map("Warning.TButton", background=[("active", "#FFE9BE")])
    style.configure(
        "Link.TButton",
        background=colors["surface"],
        foreground=colors["primary"],
        borderwidth=0,
        padding=(4, 3),
        relief="flat",
    )
    style.map("Link.TButton", foreground=[("active", colors["primary_hover"])])

    style.configure(
        "TEntry",
        fieldbackground=colors["surface"],
        foreground=colors["text"],
        bordercolor=colors["border_strong"],
        insertcolor=colors["primary"],
        padding=(8, 6),
    )
    style.map(
        "TEntry",
        bordercolor=[("focus", colors["primary"]), ("!focus", colors["border_strong"])],
    )
    style.configure("Compact.TEntry", padding=(7, 3))
    style.configure(
        "TCombobox",
        fieldbackground=colors["surface"],
        background=colors["surface"],
        foreground=colors["text"],
        arrowcolor=colors["muted"],
        bordercolor=colors["border_strong"],
        padding=(7, 5),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", colors["surface"])],
        foreground=[("readonly", colors["text"])],
        bordercolor=[("focus", colors["primary"])],
    )
    style.configure("Compact.TCombobox", padding=(6, 2))
    style.configure(
        "TCheckbutton",
        background=colors["surface"],
        foreground=colors["text_soft"],
        padding=(2, 3),
    )
    style.configure("Compact.TCheckbutton", padding=(2, 1))
    style.map("TCheckbutton", background=[("active", colors["surface"])])

    style.configure(
        "Treeview",
        background=colors["surface"],
        fieldbackground=colors["surface"],
        foreground=colors["text_soft"],
        bordercolor=colors["border"],
        borderwidth=0,
        rowheight=31,
        font=(FONT_FAMILY, 9),
    )
    style.configure(
        "Treeview.Heading",
        background=colors["surface_alt"],
        foreground=colors["muted"],
        bordercolor=colors["border"],
        borderwidth=1,
        relief="flat",
        padding=(8, 8),
        font=(FONT_FAMILY, 9, "bold"),
    )
    style.map(
        "Treeview",
        background=[("selected", "#E8EEFF")],
        foreground=[("selected", "#1E3A8A")],
    )
    style.map("Treeview.Heading", background=[("active", "#EEF2F7")])

    style.configure("TNotebook", background=colors["shell"], borderwidth=0, tabmargins=0)
    style.configure(
        "TNotebook.Tab",
        background=colors["surface_alt"],
        foreground=colors["muted"],
        borderwidth=0,
        padding=(14, 8),
        font=(FONT_FAMILY, 9),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", colors["surface"]), ("active", "#EEF3F8")],
        foreground=[("selected", colors["primary"]), ("active", colors["text"])],
        expand=[("selected", (0, 0, 0, 2))],
    )
    style.configure(
        "TLabelframe",
        background=colors["surface"],
        bordercolor=colors["border"],
        borderwidth=1,
        relief="solid",
        padding=8,
    )
    style.configure(
        "TLabelframe.Label",
        background=colors["surface"],
        foreground=colors["text"],
        font=(FONT_FAMILY, 9, "bold"),
    )
    style.configure("TPanedwindow", background=colors["shell"], sashwidth=7)
    style.configure(
        "Horizontal.TProgressbar",
        background=colors["primary"],
        troughcolor="#E5EBF2",
        bordercolor="#E5EBF2",
        lightcolor=colors["primary"],
        darkcolor=colors["primary"],
    )
    style.configure(
        "Vertical.TScrollbar",
        background="#C8D3E0",
        troughcolor=colors["surface_alt"],
        bordercolor=colors["surface_alt"],
        arrowcolor=colors["muted"],
    )
    style.configure(
        "Horizontal.TScrollbar",
        background="#C8D3E0",
        troughcolor=colors["surface_alt"],
        bordercolor=colors["surface_alt"],
        arrowcolor=colors["muted"],
    )

    style.configure("Sidebar.TFrame", background=colors["navy"])
    style.configure(
        "SidebarBrand.TLabel",
        background=colors["navy"],
        foreground="#FFFFFF",
        font=(FONT_FAMILY, 11, "bold"),
    )
    style.configure(
        "SidebarSubtitle.TLabel",
        background=colors["navy"],
        foreground=colors["nav_muted"],
        font=(FONT_FAMILY, 7),
    )
    style.configure(
        "SidebarGroup.TLabel",
        background=colors["navy"],
        foreground=colors["nav_muted"],
        font=(FONT_FAMILY, 8),
    )
    style.configure(
        "Nav.TButton",
        background=colors["navy"],
        foreground=colors["nav_text"],
        bordercolor=colors["navy"],
        lightcolor=colors["navy"],
        darkcolor=colors["navy"],
        relief="flat",
        anchor="w",
        padding=(12, 6),
        font=(FONT_FAMILY, 9),
    )
    style.map(
        "Nav.TButton",
        background=[("pressed", colors["navy_hover"]), ("active", colors["navy_hover"])],
        foreground=[("!disabled", colors["nav_text"])],
    )
    style.configure(
        "NavSelected.TButton",
        background=colors["primary"],
        foreground="#FFFFFF",
        bordercolor=colors["primary"],
        lightcolor=colors["primary"],
        darkcolor=colors["primary"],
        relief="flat",
        anchor="w",
        padding=(12, 6),
        font=(FONT_FAMILY, 9, "bold"),
    )
    style.map(
        "NavSelected.TButton",
        background=[("pressed", colors["primary_hover"]), ("active", colors["primary_hover"])],
        foreground=[("!disabled", "#FFFFFF")],
    )
    style.configure("Compact.Nav.TButton", anchor="center", padding=(4, 6))
    style.configure("Compact.NavSelected.TButton", anchor="center", padding=(4, 6))
    style.configure(
        "SidebarLink.TButton",
        background=colors["navy"],
        foreground=colors["nav_muted"],
        borderwidth=0,
        padding=4,
    )
    style.map(
        "SidebarLink.TButton",
        background=[("active", colors["navy_hover"])],
        foreground=[("active", "#FFFFFF")],
    )

    style.configure("Header.TFrame", background=colors["surface"])
    style.configure(
        "HeaderTitle.TLabel",
        background=colors["surface"],
        foreground=colors["text"],
        font=(FONT_FAMILY, 12, "bold"),
    )
    style.configure(
        "HeaderSubtitle.TLabel",
        background=colors["surface"],
        foreground=colors["muted"],
        font=(FONT_FAMILY, 8),
    )
    style.configure("Status.TFrame", background=colors["surface"])
    style.configure(
        "Status.TLabel",
        background=colors["surface"],
        foreground=colors["muted"],
        font=(FONT_FAMILY, 8),
    )
    style.configure("SuccessPill.TFrame", background=colors["success_soft"])
    style.configure(
        "SuccessPill.TLabel",
        background=colors["success_soft"],
        foreground=colors["success"],
        font=(FONT_FAMILY, 8, "bold"),
    )
    style.configure(
        "SourceLink.TButton",
        background=colors["surface"],
        foreground=colors["text"],
        borderwidth=0,
        anchor="w",
        padding=(2, 3),
        font=(FONT_FAMILY, 9, "bold"),
    )
    style.map(
        "SourceLink.TButton",
        foreground=[("active", colors["primary"])],
        background=[("active", colors["surface_alt"])],
    )
    return style


def apply_semantic_widget_styles(widget: Widget) -> None:
    """Apply intent styles to existing ttk buttons and default Text widgets."""
    for child in widget.winfo_children():
        if isinstance(child, ttk.Button):
            current_style = str(child.cget("style") or "TButton")
            if current_style == "TButton":
                text = str(child.cget("text") or "").strip()
                if text in PRIMARY_BUTTON_TEXTS:
                    child.configure(style="Primary.TButton")
                elif text in DANGER_BUTTON_TEXTS:
                    child.configure(style="Danger.TButton")
                elif text in WARNING_BUTTON_TEXTS:
                    child.configure(style="Warning.TButton")
        elif isinstance(child, Text):
            try:
                if str(child.cget("relief")) not in {"flat", "solid"}:
                    child.configure(relief="flat", borderwidth=0, highlightthickness=1)
            except Exception:
                pass
        apply_semantic_widget_styles(child)
