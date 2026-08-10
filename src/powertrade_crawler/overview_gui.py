from __future__ import annotations

import threading
from collections import Counter
from datetime import date, timedelta
from tkinter import END, StringVar, messagebox, ttk
from typing import Any, Callable

from powertrade_crawler.market_agent.data_tools import connect_database
from powertrade_crawler.metrics import list_dashboard_series
from powertrade_crawler.scheduler import list_recent_job_runs, list_scheduled_jobs
from powertrade_crawler.ui_theme import COLORS, FONT_FAMILY
from powertrade_crawler.ui_dispatch import UiLifecycle


SOURCE_LABELS = {
    "elecheck": "Elecheck",
    "entsoe": "ENTSO-E",
    "elexon": "Elexon",
    "gridstatus": "GridStatus",
    "gzpec": "广州交易中心",
}

SOURCE_PAGES = {
    "elecheck": "elecheck",
    "entsoe": "entsoe",
    "elexon": "elexon",
    "gridstatus": "gridstatus",
    "gzpec": "agent",
}

SOURCE_ICONS = {
    "elecheck": "EC",
    "entsoe": "EU",
    "elexon": "GB",
    "gridstatus": "GS",
    "gzpec": "GZ",
}

SOURCE_DISPLAY_LABELS = {
    **SOURCE_LABELS,
    "elecheck": "易能",
    "entsoe": "ENTSO",
    "gridstatus": "Grid",
    "gzpec": "广州",
}


def format_record_count(value: int) -> str:
    return f"{value:,}"


def compact_dashboard_label(label: str, *, max_length: int = 30) -> str:
    """Keep chart legends readable without losing the series identity."""

    parts = [part.strip().replace("_", " ") for part in label.split("|") if part.strip()]
    if len(parts) >= 4:
        dataset_aliases = {
            "elecheck clear price": "Elecheck 现货",
            "entsoe day ahead prices": "ENTSO-E 日前",
            "elexon system prices": "Elexon 系统价",
        }
        dimension_aliases = {
            "day ahead avg price": "日前均价",
            "statistics": "统计价",
            "net imbalance volume": "净不平衡量",
            "real time avg price": "实时均价",
            "replacement price": "替代价格",
            "reserve scarcity price": "备用稀缺价",
            "system buy price": "系统买价",
            "system sell price": "系统卖价",
        }
        dataset = dataset_aliases.get(parts[1].lower(), parts[1])
        dimension_key = parts[-1].lower()
        if dimension_key.startswith("price position "):
            dimension = f"价格时点 {dimension_key.rsplit(' ', 1)[-1]}"
        else:
            dimension = dimension_aliases.get(dimension_key, parts[-1])
        region = parts[2]
        compact_parts = [dataset]
        if region and not region.isdigit() and region.lower() != "unknown":
            compact_parts.append(region)
        compact_parts.append(dimension)
        compact = " · ".join(compact_parts)
    else:
        compact = " · ".join(parts)
    if len(compact) <= max_length:
        return compact
    return f"{compact[: max_length - 1].rstrip()}…"


def load_fast_source_overview() -> list[dict[str, Any]]:
    """Load a responsive overview without scanning every row in large history tables."""

    def approximate_rows(connection, table: str) -> int:
        row = connection.execute(f"SELECT COALESCE(MAX(rowid), 0) AS value FROM {table}").fetchone()
        return int(row["value"] or 0)

    def last_business_value(connection, table: str, expression: str) -> str | None:
        row = connection.execute(
            f"""
            SELECT {expression} AS value
            FROM {table}
            WHERE {expression} IS NOT NULL
              AND TRIM(CAST({expression} AS TEXT)) != ''
            ORDER BY rowid DESC
            LIMIT 1
            """
        ).fetchone()
        return str(row["value"]) if row and row["value"] else None

    table_groups = {
        "elecheck": (
            "elecheck_clear_price_records",
            "elecheck_purchasing_records",
            "elecheck_mechanism_electricity_price_records",
        ),
        "entsoe": ("entsoe_records", "market_records"),
        "elexon": ("elexon_records",),
        "gridstatus": ("gridstatus_records", "gridstatus_dataset_metadata"),
        "gzpec": ("gzpec_news_records",),
    }
    date_lookups = {
        "elecheck": (
            ("elecheck_clear_price_records", "start_date"),
            ("elecheck_purchasing_records", "data_month"),
            ("elecheck_mechanism_electricity_price_records", "collected_at"),
        ),
        "entsoe": (
            ("entsoe_records", "substr(interval_start_utc, 1, 10)"),
            ("market_records", "trade_date"),
        ),
        "elexon": (
            (
                "elexon_records",
                "COALESCE(settlement_date, substr(start_time_utc, 1, 10), "
                "substr(publish_time_utc, 1, 10))",
            ),
        ),
        "gridstatus": (
            (
                "gridstatus_records",
                "substr(COALESCE(interval_start_utc, record_time_utc), 1, 10)",
            ),
            ("gridstatus_dataset_metadata", "substr(latest_available_time_utc, 1, 10)"),
        ),
        "gzpec": (("gzpec_news_records", "publish_date"),),
    }
    result: list[dict[str, Any]] = []
    with connect_database() as connection:
        for source in SOURCE_LABELS:
            row_count = sum(approximate_rows(connection, table) for table in table_groups[source])
            candidates = [
                last_business_value(connection, table, expression)
                for table, expression in date_lookups[source]
            ]
            latest = max((value for value in candidates if value), default=None)
            result.append(
                {
                    "source": source,
                    "record_count": row_count,
                    "latest_business_date": latest,
                }
            )
    return result


class OverviewApp:
    def __init__(
        self,
        root,
        *,
        on_navigate: Callable[[str], None],
        on_agent_question: Callable[[str], None],
    ) -> None:
        self.root = root
        self.ui = UiLifecycle(root)
        self.on_navigate = on_navigate
        self.on_agent_question = on_agent_question
        self.refreshing = False
        self.source_vars: dict[str, dict[str, StringVar]] = {}
        self.total_records_var = StringVar(value="—")
        self.latest_update_var = StringVar(value="—")
        self.recent_tasks_var = StringVar(value="—")
        self.source_summary_var = StringVar(value="5 类")
        self.agent_prompt_var = StringVar(value="")
        self.status_var = StringVar(value="正在读取本地数据概况…")
        self.figure = None
        self.canvas = None
        self._agent_card_hidden: bool | None = None
        self._build_ui()
        self.root.bind("<Configure>", self._on_resize, add="+")
        self.root.after_idle(self.refresh)

    def _build_ui(self) -> None:
        colors = COLORS
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        intro = ttk.Frame(self.root, style="AppSurface.TFrame", padding=(20, 16, 20, 8))
        intro.grid(row=0, column=0, sticky="ew")
        intro.columnconfigure(0, weight=1)
        ttk.Label(intro, text="今日概况", style="PageTitle.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
        )
        ttk.Label(
            intro,
            text="快速查看数据状态与常用任务。",
            style="PageSubtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))
        action_box = ttk.Frame(intro, style="AppSurface.TFrame")
        action_box.grid(row=0, column=1, rowspan=2, sticky="e")
        ttk.Button(
            action_box,
            text="浏览数据",
            command=lambda: self.on_navigate("gridstatus"),
        ).pack(side="left", padx=(0, 7))
        ttk.Button(
            action_box,
            text="定时任务",
            command=lambda: self.on_navigate("schedule"),
        ).pack(side="left", padx=(0, 7))
        self.refresh_button = ttk.Button(
            action_box,
            text="刷新总览",
            style="Primary.TButton",
            command=self.refresh,
        )
        self.refresh_button.pack(side="left")

        kpis = ttk.Frame(self.root, style="AppSurface.TFrame", padding=(20, 4, 20, 8))
        kpis.grid(row=1, column=0, sticky="ew")
        for column in range(4):
            kpis.columnconfigure(column, weight=1, uniform="overview-kpi")
        cards = (
            ("五类数据源", self.source_summary_var, "覆盖与更新时间", "05"),
            ("本地数据记录", self.total_records_var, "快速规模估算", "DB"),
            ("最新业务日期", self.latest_update_var, "各来源最新日期", "↻"),
            ("最近任务", self.recent_tasks_var, "最近运行记录", "▶"),
        )
        for index, (label, variable, note, icon) in enumerate(cards):
            card = ttk.Frame(kpis, style="Card.TFrame", padding=14)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 5, 0 if index == 3 else 5))
            card.columnconfigure(0, weight=1)
            ttk.Label(card, text=label, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Label(
                card,
                text=icon,
                foreground=colors["primary"],
                background="#E9F0FF",
                font=(FONT_FAMILY, 9, "bold"),
                padding=(8, 5),
            ).grid(row=0, column=1, rowspan=2, sticky="ne")
            ttk.Label(card, textvariable=variable, style="KpiValue.TLabel").grid(
                row=1,
                column=0,
                sticky="w",
                pady=(7, 0),
            )
            ttk.Label(card, text=note, style="CardMuted.TLabel").grid(
                row=2,
                column=0,
                columnspan=2,
                sticky="w",
                pady=(6, 0),
            )

        body = ttk.Frame(self.root, style="AppSurface.TFrame", padding=(20, 6, 20, 14))
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=2)
        body.columnconfigure(1, weight=1, minsize=340)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body, style="AppSurface.TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=3)
        left.rowconfigure(1, weight=2)
        chart_card = ttk.Frame(left, style="Card.TFrame", padding=(14, 12))
        chart_card.grid(row=0, column=0, sticky="nsew", pady=(0, 7))
        chart_card.columnconfigure(0, weight=1)
        chart_card.rowconfigure(1, weight=1)
        chart_head = ttk.Frame(chart_card, style="CardBody.TFrame")
        chart_head.grid(row=0, column=0, sticky="ew")
        ttk.Label(chart_head, text="近期价格指标", style="SectionTitle.TLabel").pack(side="left")
        ttk.Label(
            chart_head,
            text="最近 14 天",
            style="CardMuted.TLabel",
        ).pack(side="right")
        self._build_chart(chart_card)

        runs_card = ttk.Frame(left, style="Card.TFrame")
        runs_card.grid(row=1, column=0, sticky="nsew", pady=(7, 0))
        runs_card.columnconfigure(0, weight=1)
        runs_card.rowconfigure(1, weight=1)
        runs_head = ttk.Frame(runs_card, style="CardBody.TFrame", padding=(14, 10, 14, 7))
        runs_head.grid(row=0, column=0, sticky="ew")
        ttk.Label(runs_head, text="最近任务运行", style="SectionTitle.TLabel").pack(side="left")
        ttk.Button(
            runs_head,
            text="查看全部 →",
            style="Link.TButton",
            command=lambda: self.on_navigate("schedule"),
        ).pack(side="right")
        self.runs_tree = ttk.Treeview(
            runs_card,
            columns=("name", "status", "written", "started"),
            show="headings",
            height=4,
        )
        for column, label, width in (
            ("name", "任务", 290),
            ("status", "状态", 100),
            ("written", "写入", 90),
            ("started", "开始时间", 155),
        ):
            self.runs_tree.heading(column, text=label)
            self.runs_tree.column(column, width=width, minwidth=max(70, width // 2))
        runs_scroll = ttk.Scrollbar(
            runs_card,
            orient="horizontal",
            command=self.runs_tree.xview,
        )
        self.runs_tree.configure(xscrollcommand=runs_scroll.set)
        self.runs_tree.grid(row=1, column=0, sticky="nsew")
        runs_scroll.grid(row=2, column=0, sticky="ew")

        self.right_panel = ttk.Frame(body, style="AppSurface.TFrame")
        self.right_panel.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        self.right_panel.columnconfigure(0, weight=1)
        self.right_panel.rowconfigure(0, weight=3)
        self.right_panel.rowconfigure(1, weight=1)
        self.source_card = ttk.Frame(self.right_panel, style="Card.TFrame", padding=(14, 12))
        self.source_card.grid(row=0, column=0, sticky="nsew", pady=(0, 7))
        self.source_card.columnconfigure(0, weight=1)
        source_head = ttk.Frame(self.source_card, style="CardBody.TFrame")
        source_head.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        ttk.Label(source_head, text="数据源状态", style="SectionTitle.TLabel").pack(side="left")
        ttk.Label(source_head, text="本地数据库", style="CardMuted.TLabel").pack(side="right")
        for row_index, source in enumerate(SOURCE_LABELS, start=1):
            row = ttk.Frame(self.source_card, style="CardBody.TFrame", padding=(0, 1))
            row.grid(row=row_index, column=0, sticky="ew")
            row.columnconfigure(0, weight=1)
            ttk.Button(
                row,
                text=f"{SOURCE_ICONS[source]}  {SOURCE_DISPLAY_LABELS[source]}",
                style="SourceLink.TButton",
                command=lambda key=SOURCE_PAGES[source]: self.on_navigate(key),
            ).grid(row=0, column=0, sticky="w")
            latest_var = StringVar(value="正在读取…")
            status_var = StringVar(value="—")
            ttk.Label(row, textvariable=latest_var, style="CardMuted.TLabel").grid(
                row=0,
                column=1,
                sticky="e",
                padx=(4, 6),
            )
            ttk.Label(row, textvariable=status_var, style="Success.TLabel").grid(
                row=0,
                column=2,
                sticky="e",
            )
            self.source_vars[source] = {"latest": latest_var, "status": status_var}

        self.agent_card = ttk.Frame(self.right_panel, style="Card.TFrame", padding=(14, 12))
        self.agent_card.grid(row=1, column=0, sticky="nsew", pady=(7, 0))
        self.agent_card.columnconfigure(0, weight=1)
        ttk.Label(self.agent_card, text="智能 Agent", style="SectionTitle.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
        )
        compose = ttk.Frame(self.agent_card, style="CardBody.TFrame")
        compose.grid(row=1, column=0, sticky="ew", pady=(7, 0))
        compose.columnconfigure(0, weight=1)
        entry = ttk.Entry(compose, textvariable=self.agent_prompt_var)
        entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        entry.bind("<Return>", self._submit_agent_question)
        ttk.Button(
            compose,
            text="提问",
            style="Primary.TButton",
            command=self._submit_agent_question,
        ).grid(row=0, column=1, sticky="e")

        ttk.Label(
            self.root,
            textvariable=self.status_var,
            style="PageSubtitle.TLabel",
        ).grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 6))

    def _on_resize(self, event) -> None:
        if event.widget is not self.root:
            return
        should_hide = event.height < 620
        if should_hide == self._agent_card_hidden:
            return
        self._agent_card_hidden = should_hide
        if should_hide:
            self.agent_card.grid_remove()
            self.right_panel.rowconfigure(0, weight=1)
            self.right_panel.rowconfigure(1, weight=0)
        else:
            self.agent_card.grid()
            self.right_panel.rowconfigure(0, weight=3)
            self.right_panel.rowconfigure(1, weight=1)

    def _build_chart(self, parent) -> None:
        try:
            from matplotlib import rcParams
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
        except ImportError:
            ttk.Label(
                parent,
                text="Matplotlib 未安装，无法显示趋势图。",
                style="Muted.TLabel",
            ).grid(row=1, column=0, sticky="nsew", pady=(8, 0))
            return
        rcParams["font.sans-serif"] = [FONT_FAMILY, "Microsoft YaHei", "SimHei", "DejaVu Sans"]
        rcParams["axes.unicode_minus"] = False
        self.figure = Figure(figsize=(7.2, 3.0), dpi=100, facecolor=COLORS["surface"])
        self.canvas = FigureCanvasTkAgg(self.figure, master=parent)
        self.canvas.get_tk_widget().configure(background=COLORS["surface"], highlightthickness=0)
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew", pady=(6, 0))

    def refresh(self) -> None:
        if self.refreshing:
            return
        self.refreshing = True
        self.refresh_button.configure(state="disabled")
        self.status_var.set("正在刷新本地数据概况…")

        def worker() -> None:
            try:
                sources = load_fast_source_overview()
                runs = list_recent_job_runs(limit=8)
                jobs = list_scheduled_jobs()
            except Exception as exc:
                self.ui.post(self._on_refresh_error, str(exc))
                return
            self.ui.post(
                self._apply_snapshot,
                sources=sources,
                runs=runs,
                jobs=jobs,
                series=[],
            )
            try:
                series = list_dashboard_series(
                    topic="price",
                    start_date=date.today() - timedelta(days=14),
                    end_date=date.today(),
                )
            except Exception:
                return
            self.ui.post(self._render_chart, series)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_snapshot(
        self,
        *,
        sources: list[dict[str, Any]],
        runs: list[Any],
        jobs: list[Any],
        series: list[dict[str, Any]],
    ) -> None:
        total_records = sum(int(item["record_count"]) for item in sources)
        populated = sum(1 for item in sources if item["record_count"] > 0)
        latest_values = [
            str(item["latest_business_date"])
            for item in sources
            if item["latest_business_date"]
        ]
        self.source_summary_var.set(f"{populated} / 5")
        self.total_records_var.set(format_record_count(total_records))
        self.latest_update_var.set(str(max(latest_values))[:10] if latest_values else "暂无")
        self.recent_tasks_var.set(str(len(runs)))

        for item in sources:
            source = str(item["source"])
            variables = self.source_vars[source]
            latest = str(item["latest_business_date"] or "暂无")[:10]
            if len(latest) == 10 and latest[4] == "-":
                latest = latest[2:]
            variables["latest"].set(latest)
            count = int(item["record_count"])
            variables["status"].set("就绪" if count else "待采")

        self.runs_tree.delete(*self.runs_tree.get_children())
        job_names = {int(job.id): str(job.name) for job in jobs}
        status_labels = {
            "success": "已完成",
            "no_data": "无新数据",
            "partial": "部分成功",
            "running": "运行中",
            "failed": "失败",
            "skipped": "已跳过",
        }
        for run in runs[:5]:
            self.runs_tree.insert(
                "",
                END,
                values=(
                    job_names.get(int(run.job_id), f"任务 {run.job_id}"),
                    status_labels.get(str(run.status), str(run.status)),
                    run.records_written if run.records_written is not None else "—",
                    self._display_time(str(run.started_at)),
                ),
            )
        self._render_chart(series)
        self.status_var.set(
            f"总览已刷新：{populated} 类来源已有本地数据，最近显示 {min(len(runs), 5)} 条任务运行。"
        )
        self.refreshing = False
        self.refresh_button.configure(state="normal")

    def _render_chart(self, rows: list[dict[str, Any]]) -> None:
        if self.figure is None or self.canvas is None:
            return
        self.figure.clear()
        axis = self.figure.add_subplot(111)
        axis.set_facecolor(COLORS["surface"])
        if not rows:
            axis.text(
                0.5,
                0.50,
                "暂无价格汇总趋势",
                ha="center",
                va="center",
                color=COLORS["muted"],
                fontsize=11,
                transform=axis.transAxes,
            )
            axis.set_axis_off()
            self.canvas.draw_idle()
            return
        counts = Counter(str(row["label"]) for row in rows)
        labels = [label for label, _count in counts.most_common(3)]
        colors = (COLORS["primary"], COLORS["cyan"], "#7C3AED")
        for label, color in zip(labels, colors, strict=False):
            selected = [row for row in rows if str(row["label"]) == label]
            selected.sort(key=lambda row: str(row["metric_date"]))
            axis.plot(
                [date.fromisoformat(str(row["metric_date"])[:10]) for row in selected],
                [row["avg_value"] for row in selected],
                linewidth=2.0,
                marker="o",
                markersize=3,
                label=compact_dashboard_label(label),
                color=color,
            )
        from matplotlib.dates import DateFormatter, DayLocator

        chart_dates = [
            date.fromisoformat(str(row["metric_date"])[:10])
            for row in rows
            if str(row["label"]) in labels
        ]
        day_span = (max(chart_dates) - min(chart_dates)).days if chart_dates else 0
        axis.xaxis.set_major_locator(DayLocator(interval=max(1, (day_span + 4) // 5)))
        axis.xaxis.set_major_formatter(DateFormatter("%m-%d"))
        axis.grid(True, color="#E7ECF2", linewidth=0.8)
        axis.tick_params(axis="both", colors=COLORS["muted"], labelsize=8)
        axis.spines[["top", "right"]].set_visible(False)
        axis.spines[["left", "bottom"]].set_color(COLORS["border"])
        axis.legend(
            loc="best",
            fontsize=7,
            frameon=False,
            handlelength=1.8,
            labelspacing=0.5,
        )
        self.figure.tight_layout(pad=1.0)
        self.canvas.draw_idle()

    def _on_refresh_error(self, message: str) -> None:
        self.refreshing = False
        self.refresh_button.configure(state="normal")
        self.status_var.set("总览刷新失败，请检查本地数据库状态。")
        messagebox.showerror("刷新总览失败", message, parent=self.root)

    def _submit_agent_question(self, _event=None) -> str:
        prompt = self.agent_prompt_var.get().strip()
        if not prompt:
            messagebox.showinfo("请输入问题", "请先输入要交给多数据源 Agent 的问题。", parent=self.root)
            return "break"
        self.agent_prompt_var.set("")
        self.on_agent_question(prompt)
        return "break"

    @staticmethod
    def _display_time(value: str) -> str:
        return value.replace("T", " ")[:16]
