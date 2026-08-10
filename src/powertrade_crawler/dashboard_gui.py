from __future__ import annotations

import sqlite3
import threading
from datetime import date, timedelta
from pathlib import Path
from tkinter import BOTH, END, LEFT, W, X, BooleanVar, Menu, StringVar, messagebox, ttk
from tkinter.filedialog import asksaveasfilename

from powertrade_crawler.metrics import (
    export_dashboard_metric_rows,
    list_dashboard_metric_rows,
    list_dashboard_overview,
    list_dashboard_series,
    rebuild_dashboard_daily_metrics,
)
from powertrade_crawler.credential_setup import credential_is_configured
from powertrade_crawler.scheduler import (
    SOURCE_UPDATE_DESCRIPTIONS,
    SOURCE_UPDATE_LABELS,
    create_default_job_templates,
    create_scheduled_job,
    create_source_update_job,
    delete_scheduled_job,
    install_windows_task,
    list_elecheck_source_update_areas,
    list_recent_job_runs,
    list_scheduled_jobs,
    parse_optional_date,
    parse_params_json,
    run_scheduled_job,
    set_scheduled_job_enabled,
    synchronize_windows_task_state,
    uninstall_windows_task,
)
from powertrade_crawler.registry import list_spiders
from powertrade_crawler.spiders.entsoe import ENTSOE_BIDDING_ZONES


class DashboardDataApp:
    topic_options = {
        "价格": "price",
        "负荷": "load",
        "发电结构": "generation",
        "跨区/互联线": "interconnector",
        "指标表": "all",
    }

    def __init__(self, root) -> None:
        self.root = root
        try:
            configured_areas = list_elecheck_source_update_areas()
        except (OSError, ValueError, sqlite3.Error):
            configured_areas = self.elecheck_area_options[1:]
        self.elecheck_area_options = ["全部地区", *configured_areas]
        self.summary_var = StringVar(value="Ready")
        self.start_date_var = StringVar(value=(date.today() - timedelta(days=30)).isoformat())
        self.end_date_var = StringVar(value=date.today().isoformat())
        self.topic_var = StringVar(value="价格")
        self.current_metric_rows: list[dict[str, object]] = []
        self.figure = None
        self.canvas = None
        self.build_ui()
        self.refresh_dashboard()

    def build_ui(self) -> None:
        toolbar = ttk.Frame(self.root, padding=8)
        toolbar.pack(fill=X)
        ttk.Label(toolbar, text="开始日期").pack(side=LEFT, padx=(0, 4))
        ttk.Entry(toolbar, textvariable=self.start_date_var, width=12).pack(side=LEFT, padx=(0, 8))
        ttk.Label(toolbar, text="结束日期").pack(side=LEFT, padx=(0, 4))
        ttk.Entry(toolbar, textvariable=self.end_date_var, width=12).pack(side=LEFT, padx=(0, 8))
        ttk.Label(toolbar, text="专题").pack(side=LEFT, padx=(0, 4))
        ttk.Combobox(
            toolbar,
            textvariable=self.topic_var,
            values=list(self.topic_options),
            width=14,
            state="readonly",
        ).pack(side=LEFT, padx=(0, 8))
        ttk.Button(toolbar, text="刷新", command=self.refresh_dashboard).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="重建指标", command=self.rebuild_metrics).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="导出指标 CSV", command=self.export_metrics).pack(side=LEFT, padx=4)

        body = ttk.PanedWindow(self.root, orient="horizontal")
        body.pack(fill=BOTH, expand=True, padx=8, pady=8)
        overview_frame = ttk.Frame(body)
        chart_frame = ttk.Frame(body)
        body.add(overview_frame, weight=1)
        body.add(chart_frame, weight=2)

        ttk.Label(overview_frame, text="数据源概览").pack(anchor=W)
        self.overview_tree = ttk.Treeview(
            overview_frame,
            columns=("source", "table", "count", "latest"),
            show="headings",
            height=10,
        )
        self.configure_tree(
            self.overview_tree,
            (
                ("source", "数据源", 140),
                ("table", "表", 190),
                ("count", "记录数", 90),
                ("latest", "最新时间", 160),
            ),
        )
        self.overview_tree.pack(fill=BOTH, expand=True, pady=(4, 8))

        ttk.Label(overview_frame, text="指标明细").pack(anchor=W)
        self.metrics_tree = ttk.Treeview(
            overview_frame,
            columns=("date", "source", "dataset", "region", "metric", "avg", "count"),
            show="headings",
            height=12,
        )
        self.configure_tree(
            self.metrics_tree,
            (
                ("date", "日期", 90),
                ("source", "来源", 90),
                ("dataset", "数据集", 160),
                ("region", "区域/方向", 110),
                ("metric", "指标", 130),
                ("avg", "均值", 90),
                ("count", "点数", 70),
            ),
        )
        self.metrics_tree.pack(fill=BOTH, expand=True, pady=(4, 0))
        self.build_chart_canvas(chart_frame)
        ttk.Label(self.root, textvariable=self.summary_var, anchor=W).pack(fill=X, padx=8, pady=4)

    def build_chart_canvas(self, parent) -> None:
        try:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib import rcParams
            from matplotlib.figure import Figure
        except ImportError as exc:
            ttk.Label(
                parent,
                text=f"Matplotlib 未安装，无法显示图表：{exc}",
                wraplength=520,
            ).pack(fill=BOTH, expand=True)
            return
        rcParams["font.sans-serif"] = [
            "Microsoft YaHei",
            "SimHei",
            "Arial Unicode MS",
            "DejaVu Sans",
        ]
        rcParams["axes.unicode_minus"] = False
        self.figure = Figure(figsize=(7, 4.5), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.figure, master=parent)
        self.canvas.get_tk_widget().pack(fill=BOTH, expand=True)

    def refresh_dashboard(self) -> None:
        try:
            start, end = self.parse_date_range()
        except ValueError as exc:
            messagebox.showerror("日期错误", str(exc))
            return
        self.refresh_overview()
        topic = self.selected_topic()
        self.current_metric_rows = list_dashboard_metric_rows(
            topic=topic,
            start_date=start,
            end_date=end,
        )
        self.refresh_metric_table()
        self.plot_topic(topic=topic, start_date=start, end_date=end)
        self.summary_var.set(
            f"分析看板：{self.topic_var.get()} 显示 {len(self.current_metric_rows)} 条日指标。"
        )

    def refresh_overview(self) -> None:
        self.clear_tree(self.overview_tree)
        for row in list_dashboard_overview():
            self.overview_tree.insert(
                "",
                END,
                values=(row["source"], row["table"], row["record_count"], row["latest"]),
            )

    def refresh_metric_table(self) -> None:
        self.clear_tree(self.metrics_tree)
        for row in self.current_metric_rows[:1000]:
            avg_value = row.get("avg_value")
            self.metrics_tree.insert(
                "",
                END,
                values=(
                    row.get("metric_date") or "",
                    row.get("source") or "",
                    row.get("dataset") or "",
                    row.get("region") or "",
                    row.get("metric_name") or "",
                    "" if avg_value is None else f"{float(avg_value):.4f}",
                    row.get("record_count") or 0,
                ),
            )

    def plot_topic(self, *, topic: str, start_date: date, end_date: date) -> None:
        if self.figure is None or self.canvas is None:
            return
        series_rows = list_dashboard_series(topic=topic, start_date=start_date, end_date=end_date)
        self.figure.clear()
        axis = self.figure.add_subplot(111)
        if not series_rows:
            axis.text(0.5, 0.5, "暂无可绘制指标，请先重建指标或采集数据。", ha="center")
            axis.set_axis_off()
            self.canvas.draw()
            return
        labels = sorted({str(row["label"]) for row in series_rows})
        dates = sorted({str(row["metric_date"]) for row in series_rows})
        values_by_label = {label: {metric_date: None for metric_date in dates} for label in labels}
        for row in series_rows:
            values_by_label[str(row["label"])][str(row["metric_date"])] = row["avg_value"]

        x_values = list(range(len(dates)))
        if topic == "generation" and len(labels) > 1:
            matrix = [
                [float(values_by_label[label].get(metric_date) or 0.0) for metric_date in dates]
                for label in labels
            ]
            axis.stackplot(x_values, matrix, labels=labels, alpha=0.8)
        else:
            for label in labels:
                y_values = [values_by_label[label].get(metric_date) for metric_date in dates]
                axis.plot(x_values, y_values, marker="o", linewidth=1.5, label=label)
        axis.set_title(f"{self.topic_var.get()} - 日均值")
        axis.set_xticks(x_values)
        axis.set_xticklabels(dates, rotation=35, ha="right")
        axis.grid(True, alpha=0.25)
        axis.legend(loc="best", fontsize=8)
        self.figure.tight_layout()
        self.canvas.draw()

    def rebuild_metrics(self) -> None:
        try:
            start, end = self.parse_date_range()
        except ValueError as exc:
            messagebox.showerror("日期错误", str(exc))
            return
        self.summary_var.set("正在重建指标...")

        def worker() -> None:
            try:
                count = rebuild_dashboard_daily_metrics(start, end)
            except Exception as exc:
                message = str(exc)
                self.root.after(0, lambda: messagebox.showerror("重建指标失败", message))
                self.root.after(0, lambda: self.summary_var.set("指标重建失败。"))
                return
            self.root.after(0, lambda: self.on_rebuild_success(count))

        threading.Thread(target=worker, daemon=True).start()

    def on_rebuild_success(self, count: int) -> None:
        self.summary_var.set(f"指标重建完成：生成 {count} 条日指标。")
        self.refresh_dashboard()

    def export_metrics(self) -> None:
        try:
            start, end = self.parse_date_range()
        except ValueError as exc:
            messagebox.showerror("日期错误", str(exc))
            return
        output_path = asksaveasfilename(
            title="导出看板指标 CSV",
            initialfile=f"dashboard_{self.selected_topic()}_metrics.csv",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not output_path:
            return
        try:
            row_count = export_dashboard_metric_rows(
                output_path=Path(output_path),
                topic=self.selected_topic(),
                start_date=start,
                end_date=end,
            )
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        self.summary_var.set(f"已导出 {row_count} 条看板指标到 {output_path}")

    def parse_date_range(self) -> tuple[date, date]:
        try:
            start = date.fromisoformat(self.start_date_var.get().strip())
            end = date.fromisoformat(self.end_date_var.get().strip())
        except ValueError as exc:
            raise ValueError("日期格式应为 YYYY-MM-DD。") from exc
        if end <= start:
            raise ValueError("结束日期必须晚于开始日期。")
        return start, end

    def selected_topic(self) -> str:
        return self.topic_options.get(self.topic_var.get(), "price")

    @staticmethod
    def configure_tree(tree, columns) -> None:
        for column, label, width in columns:
            tree.heading(column, text=label)
            tree.column(column, width=width, anchor=W)

    @staticmethod
    def clear_tree(tree) -> None:
        for item_id in tree.get_children():
            tree.delete(item_id)


class ScheduleDataApp:
    source_options = {
        "Elecheck（国内电价）": "elecheck",
        "ENTSO-E（欧洲）": "entsoe",
        "Elexon（英国）": "elexon",
        "GridStatus（北美）": "gridstatus",
        "广州电力交易中心": "gzpec",
    }
    source_credentials = {
        "elecheck": ("elecheck_authorization", "Elecheck 采集凭据"),
        "entsoe": ("entsoe_security_token", "ENTSO-E 访问凭据"),
        "gridstatus": ("gridstatus_api_key", "GridStatus API Key"),
    }
    job_type_labels = {
        "source_update": "来源自动更新",
        "crawl": "单项采集",
        "metrics": "指标重建",
        "maintenance": "数据库维护",
    }
    schedule_kind_labels = {"daily": "每日", "weekly": "每周", "monthly": "每月"}
    job_type_options = {
        "单项数据采集": "crawl",
        "指标重建": "metrics",
        "数据库维护": "maintenance",
    }
    schedule_kind_options = {"每日": "daily", "每周": "weekly", "每月": "monthly"}
    date_mode_options = {
        "不自动填写日期": "none",
        "昨天": "yesterday",
        "最近 7 天": "last-7-days",
        "最近 30 天": "last-30-days",
        "自定义范围": "custom",
    }
    date_mode_labels = {value: label for label, value in date_mode_options.items()}
    run_status_labels = {
        "success": "成功",
        "no_data": "无新数据",
        "partial": "部分成功",
        "failed": "失败",
        "skipped": "已跳过",
        "running": "执行中",
    }
    elecheck_area_options = [
        "全部地区",
        "江苏",
        "山西",
        "山东",
        "广东",
        "浙江",
        "安徽",
        "福建",
        "甘肃",
        "蒙西",
        "湖北",
        "湖南",
        "河南",
        "河北",
        "辽宁",
    ]

    def __init__(self, root) -> None:
        self.root = root
        self.summary_var = StringVar(value="Ready")
        self.name_var = StringVar(value="")
        self.job_type_var = StringVar(value="指标重建")
        self.spider_name_var = StringVar(value="")
        self.schedule_kind_var = StringVar(value="每日")
        self.schedule_time_var = StringVar(value="02:00")
        self.date_mode_var = StringVar(value="最近 30 天")
        self.start_date_var = StringVar(value="")
        self.end_date_var = StringVar(value="")
        self.enabled_var = BooleanVar(value=True)
        self.params_json_var = StringVar(value="{}")
        self.quick_source_var = StringVar(value="Elecheck（国内电价）")
        self.quick_time_var = StringVar(value="09:00")
        self.quick_area_var = StringVar(value="全部地区")
        self.quick_enabled_var = BooleanVar(value=True)
        self.quick_install_var = BooleanVar(value=True)
        self.quick_description_var = StringVar()
        self.build_ui()
        self.refresh()

    def build_ui(self) -> None:
        page = ttk.Frame(self.root, style="AppSurface.TFrame", padding=(12, 12, 12, 8))
        page.pack(fill=BOTH, expand=True)
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)

        body = ttk.PanedWindow(page, orient="horizontal")
        body.grid(row=0, column=0, sticky="nsew")
        config_frame = ttk.Frame(body, width=390, padding=(0, 0, 10, 0))
        config_frame.grid_propagate(False)
        workspace = ttk.Frame(body, style="AppSurface.TFrame")
        body.add(config_frame, weight=0)
        body.add(workspace, weight=1)

        config_frame.columnconfigure(0, weight=1)
        config_frame.rowconfigure(1, weight=1)
        ttk.Label(config_frame, text="自动更新", style="SectionTitle.TLabel").grid(
            row=0,
            column=0,
            sticky=W,
            pady=(0, 7),
        )
        create_tabs = ttk.Notebook(config_frame)
        create_tabs.grid(row=1, column=0, sticky="nsew")
        quick_frame = ttk.Frame(create_tabs, padding=(12, 12))
        advanced_frame = ttk.Frame(create_tabs, padding=(12, 12))
        create_tabs.add(quick_frame, text="快捷更新")
        create_tabs.add(advanced_frame, text="高级任务")

        quick_frame.columnconfigure(0, weight=1)
        ttk.Label(
            quick_frame,
            text="选择来源后，系统自动安排适合增量更新的数据集和日期窗口。",
            style="Muted.TLabel",
            wraplength=330,
            justify="left",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(quick_frame, text="数据来源").grid(row=1, column=0, sticky=W)
        source_box = ttk.Combobox(
            quick_frame,
            textvariable=self.quick_source_var,
            values=list(self.source_options),
            state="readonly",
            style="Compact.TCombobox",
        )
        source_box.grid(row=2, column=0, sticky="ew", pady=(3, 9))
        source_box.bind("<<ComboboxSelected>>", self.on_quick_source_changed)

        quick_options = ttk.Frame(quick_frame)
        quick_options.grid(row=3, column=0, sticky="ew")
        quick_options.columnconfigure(0, weight=1)
        quick_options.columnconfigure(1, weight=1)
        ttk.Label(quick_options, text="每天运行时间").grid(row=0, column=0, sticky=W)
        self.quick_area_label = ttk.Label(quick_options, text="地区")
        self.quick_area_label.grid(row=0, column=1, sticky=W, padx=(8, 0))
        ttk.Entry(
            quick_options,
            textvariable=self.quick_time_var,
            style="Compact.TEntry",
        ).grid(row=1, column=0, sticky="ew", pady=(3, 0), padx=(0, 4))
        self.quick_area_box = ttk.Combobox(
            quick_options,
            textvariable=self.quick_area_var,
            values=self.elecheck_area_options,
            state="readonly",
            style="Compact.TCombobox",
        )
        self.quick_area_box.grid(row=1, column=1, sticky="ew", pady=(3, 0), padx=(4, 0))

        detail_card = ttk.LabelFrame(quick_frame, text="本次会更新", padding=(9, 7))
        detail_card.grid(row=4, column=0, sticky="ew", pady=(11, 8))
        detail_card.columnconfigure(0, weight=1)
        ttk.Label(
            detail_card,
            textvariable=self.quick_description_var,
            style="Muted.TLabel",
            wraplength=310,
            justify="left",
        ).grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(
            quick_frame,
            text="启用本地任务",
            variable=self.quick_enabled_var,
            style="Compact.TCheckbutton",
        ).grid(row=5, column=0, sticky=W, pady=(2, 0))
        ttk.Checkbutton(
            quick_frame,
            text="同时安装 Windows 自动触发器",
            variable=self.quick_install_var,
            style="Compact.TCheckbutton",
        ).grid(row=6, column=0, sticky=W, pady=(2, 3))
        ttk.Label(
            quick_frame,
            text="提示：只有安装 Windows 触发器，软件关闭后才会按时运行。",
            style="Muted.TLabel",
            wraplength=330,
            justify="left",
        ).grid(row=7, column=0, sticky="ew", pady=(0, 9))
        ttk.Button(
            quick_frame,
            text="创建每日自动更新",
            style="Primary.TButton",
            command=self.create_quick_source_job,
        ).grid(row=8, column=0, sticky="ew")

        advanced_frame.columnconfigure(1, weight=1)
        ttk.Label(
            advanced_frame,
            text="用于单个采集项目、指标重建或数据库维护；任务参数禁止保存凭据。",
            style="Muted.TLabel",
            wraplength=330,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 7))

        def add_field(row: int, label: str, widget) -> None:
            ttk.Label(advanced_frame, text=label).grid(
                row=row,
                column=0,
                sticky=W,
                padx=(0, 9),
                pady=2,
            )
            widget.grid(row=row, column=1, sticky="ew", pady=2)

        add_field(
            1,
            "名称",
            ttk.Entry(advanced_frame, textvariable=self.name_var, style="Compact.TEntry"),
        )
        add_field(
            2,
            "类型",
            ttk.Combobox(
                advanced_frame,
                textvariable=self.job_type_var,
                values=list(self.job_type_options),
                state="readonly",
                style="Compact.TCombobox",
            ),
        )
        add_field(
            3,
            "采集项目（高级标识）",
            ttk.Combobox(
                advanced_frame,
                textvariable=self.spider_name_var,
                values=["", *list_spiders()],
                state="readonly",
                style="Compact.TCombobox",
            ),
        )
        schedule_row = ttk.Frame(advanced_frame)
        add_field(4, "计划", schedule_row)
        schedule_row.columnconfigure(0, weight=3)
        schedule_row.columnconfigure(1, weight=2)
        ttk.Combobox(
            schedule_row,
            textvariable=self.schedule_kind_var,
            values=list(self.schedule_kind_options),
            state="readonly",
            style="Compact.TCombobox",
            width=8,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ttk.Entry(
            schedule_row,
            textvariable=self.schedule_time_var,
            width=5,
            style="Compact.TEntry",
        ).grid(
            row=0,
            column=1,
            sticky="ew",
        )
        add_field(
            5,
            "日期模式",
            ttk.Combobox(
                advanced_frame,
                textvariable=self.date_mode_var,
                values=list(self.date_mode_options),
                state="readonly",
                style="Compact.TCombobox",
            ),
        )
        date_row = ttk.Frame(advanced_frame)
        add_field(6, "自定义日期（开始 / 结束）", date_row)
        date_row.columnconfigure(0, weight=1)
        date_row.columnconfigure(1, weight=1)
        ttk.Entry(date_row, textvariable=self.start_date_var, style="Compact.TEntry").grid(
            row=0,
            column=0,
            sticky="ew",
            padx=(0, 5),
        )
        ttk.Entry(date_row, textvariable=self.end_date_var, style="Compact.TEntry").grid(
            row=0,
            column=1,
            sticky="ew",
        )
        add_field(
            7,
            "参数 JSON",
            ttk.Entry(advanced_frame, textvariable=self.params_json_var, style="Compact.TEntry"),
        )
        ttk.Checkbutton(
            advanced_frame,
            text="启用本地任务（需另行安装 Windows 触发器）",
            variable=self.enabled_var,
            style="Compact.TCheckbutton",
        ).grid(
            row=8,
            column=1,
            sticky=W,
            pady=(2, 4),
        )
        create_actions = ttk.Frame(advanced_frame)
        create_actions.grid(row=9, column=0, columnspan=2, sticky="ew")
        create_actions.columnconfigure(0, weight=1)
        create_actions.columnconfigure(1, weight=1)
        ttk.Button(
            create_actions,
            text="创建任务",
            style="Primary.TButton",
            command=self.create_job,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(
            create_actions,
            text="默认模板",
            command=self.create_templates,
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.on_quick_source_changed()
        workspace.columnconfigure(0, weight=1)
        workspace.rowconfigure(0, weight=2)
        workspace.rowconfigure(1, weight=1)
        jobs_frame = ttk.LabelFrame(workspace, text="本地定时任务", padding=(10, 8))
        runs_frame = ttk.LabelFrame(workspace, text="最近运行", padding=(10, 8))
        jobs_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        runs_frame.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        jobs_frame.columnconfigure(0, weight=1)
        jobs_frame.rowconfigure(1, weight=1)
        runs_frame.columnconfigure(0, weight=1)
        runs_frame.rowconfigure(0, weight=1)

        actions = ttk.Frame(jobs_frame)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for column in range(5):
            actions.columnconfigure(column, weight=1, uniform="task-action")
        ttk.Button(actions, text="刷新", command=self.refresh).grid(row=0, column=0, sticky="ew")
        ttk.Button(
            actions,
            text="启用",
            command=lambda: self.set_selected_enabled(True),
        ).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Button(
            actions,
            text="禁用",
            command=lambda: self.set_selected_enabled(False),
        ).grid(row=0, column=2, sticky="ew", padx=(6, 0))
        ttk.Button(
            actions,
            text="立即运行",
            style="Primary.TButton",
            command=self.run_selected,
        ).grid(row=0, column=3, sticky="ew", padx=(6, 0))
        more_button = ttk.Menubutton(actions, text="更多")
        more_menu = Menu(more_button, tearoff=False)
        more_menu.add_command(label="安装到 Windows 任务计划", command=self.install_selected)
        more_menu.add_command(label="卸载 Windows 任务计划", command=self.uninstall_selected)
        more_menu.add_separator()
        more_menu.add_command(label="删除选中任务", command=self.delete_selected)
        more_button.configure(menu=more_menu)
        more_button.grid(row=0, column=4, sticky="ew", padx=(6, 0))

        self.jobs_tree = ttk.Treeview(
            jobs_frame,
            columns=("id", "name", "type", "spider", "schedule", "date", "enabled", "win"),
            show="headings",
            height=10,
            selectmode="browse",
        )
        DashboardDataApp.configure_tree(
            self.jobs_tree,
            (
                ("id", "ID", 50),
                ("name", "名称", 220),
                ("type", "类型", 110),
                ("spider", "来源 / 采集项目", 180),
                ("schedule", "频率/时间", 110),
                ("date", "日期模式", 110),
                ("enabled", "启用", 70),
                ("win", "Windows触发器", 100),
            ),
        )
        jobs_scroll = ttk.Scrollbar(
            jobs_frame,
            orient="horizontal",
            command=self.jobs_tree.xview,
        )
        self.jobs_tree.configure(xscrollcommand=jobs_scroll.set)
        self.jobs_tree.grid(row=1, column=0, sticky="nsew")
        jobs_scroll.grid(row=2, column=0, sticky="ew")

        self.runs_tree = ttk.Treeview(
            runs_frame,
            columns=("id", "job_id", "status", "started", "written", "message"),
            show="headings",
            height=8,
        )
        DashboardDataApp.configure_tree(
            self.runs_tree,
            (
                ("id", "Run ID", 70),
                ("job_id", "Job ID", 70),
                ("status", "状态", 80),
                ("started", "开始时间", 150),
                ("written", "写入", 70),
                ("message", "消息", 500),
            ),
        )
        runs_scroll = ttk.Scrollbar(
            runs_frame,
            orient="horizontal",
            command=self.runs_tree.xview,
        )
        self.runs_tree.configure(xscrollcommand=runs_scroll.set)
        self.runs_tree.grid(row=0, column=0, sticky="nsew")
        runs_scroll.grid(row=1, column=0, sticky="ew")
        ttk.Label(
            page,
            textvariable=self.summary_var,
            style="PageSubtitle.TLabel",
            anchor=W,
        ).grid(row=1, column=0, sticky="ew", pady=(7, 0))

    def on_quick_source_changed(self, _event=None) -> None:
        source = self.source_options[self.quick_source_var.get()]
        self.quick_description_var.set(SOURCE_UPDATE_DESCRIPTIONS[source])
        if source == "elecheck":
            self.quick_area_label.configure(text="地区")
            self.quick_area_box.configure(
                values=self.elecheck_area_options,
                state="readonly",
            )
            if self.quick_area_var.get() not in self.elecheck_area_options:
                self.quick_area_var.set("全部地区")
        elif source == "entsoe":
            self.quick_area_label.configure(text="竞价区")
            self.quick_area_box.configure(
                values=sorted(ENTSOE_BIDDING_ZONES),
                state="readonly",
            )
            if self.quick_area_var.get() not in ENTSOE_BIDDING_ZONES:
                self.quick_area_var.set("DE-LU")
        else:
            self.quick_area_label.configure(text="地区（无需选择）")
            self.quick_area_var.set("")
            self.quick_area_box.configure(values=[], state="disabled")

    def create_quick_source_job(self) -> None:
        source = self.source_options[self.quick_source_var.get()]
        area = self.quick_area_var.get().strip() or None
        if area == "全部地区":
            area = None
        if self.quick_install_var.get() and not self.quick_enabled_var.get():
            messagebox.showerror(
                "无法创建自动更新",
                "要安装 Windows 自动触发器，请同时勾选“启用本地任务”。",
            )
            return
        try:
            job = create_source_update_job(
                source=source,
                schedule_time=self.quick_time_var.get().strip(),
                area=area,
                enabled=self.quick_enabled_var.get(),
            )
        except ValueError as exc:
            messagebox.showerror("创建自动更新失败", str(exc))
            return

        if self.quick_install_var.get():
            try:
                task_name = install_windows_task(job.id)
            except Exception as exc:
                self.refresh(
                    message=(
                        f"已创建本地任务 {job.id}，但 Windows 触发器安装失败；"
                        "请检查权限后在任务列表的“更多”中重试。"
                    )
                )
                messagebox.showwarning(
                    "本地任务已创建",
                    (
                        "本地任务已安全保留，但软件关闭后暂时不会自动运行。\n\n"
                        f"Windows 触发器安装失败：{exc}"
                    ),
                )
                return
            self.refresh(
                message=(
                    f"已创建并安装任务 {job.id}：每天 {job.schedule_time} 自动更新；"
                    f"Windows 任务 {task_name}。"
                )
            )
            self.show_quick_credential_warning(source)
            return
        self.refresh(
            message=(
                f"已创建本地任务 {job.id}；尚未安装 Windows 触发器，"
                "软件关闭后不会自动运行。"
            )
        )
        self.show_quick_credential_warning(source)

    def show_quick_credential_warning(self, source: str) -> None:
        requirement = self.source_credentials.get(source)
        if requirement is None:
            return
        credential_name, label = requirement
        if credential_is_configured(credential_name):
            return
        messagebox.showwarning(
            "任务已创建，凭据待配置",
            (
                f"{label} 尚未配置。任务定义和 Windows 触发器可以保留，"
                "但采集会在凭据配置完成前失败。\n\n"
                "请打开左侧“API 配置向导”，按步骤申请、保存并测试凭据。"
            ),
        )

    def create_job(self) -> None:
        try:
            job = create_scheduled_job(
                name=self.name_var.get(),
                job_type=self.job_type_options.get(
                    self.job_type_var.get(),
                    self.job_type_var.get(),
                ),
                spider_name=self.spider_name_var.get().strip() or None,
                schedule_kind=self.schedule_kind_options.get(
                    self.schedule_kind_var.get(),
                    self.schedule_kind_var.get(),
                ),
                schedule_time=self.schedule_time_var.get(),
                date_mode=self.date_mode_options.get(
                    self.date_mode_var.get(),
                    self.date_mode_var.get(),
                ),
                start_date=parse_optional_date(self.start_date_var.get().strip() or None),
                end_date=parse_optional_date(self.end_date_var.get().strip() or None),
                enabled=self.enabled_var.get(),
                params=parse_params_json(self.params_json_var.get()),
            )
        except ValueError as exc:
            messagebox.showerror("创建任务失败", str(exc))
            return
        self.refresh(message=f"已创建任务 {job.id}：{job.name}")

    def create_templates(self) -> None:
        created = create_default_job_templates()
        self.refresh(message=f"已创建 {created} 个默认模板。")

    def selected_job_id(self) -> int | None:
        selection = self.jobs_tree.selection()
        if not selection:
            messagebox.showinfo("请选择任务", "请先在任务列表中选择一行。")
            return None
        return int(self.jobs_tree.item(selection[0], "values")[0])

    def set_selected_enabled(self, enabled: bool) -> None:
        job_id = self.selected_job_id()
        if job_id is None:
            return
        try:
            job = set_scheduled_job_enabled(job_id, enabled)
        except ValueError as exc:
            messagebox.showerror("更新任务失败", str(exc))
            return
        self.refresh(message=f"任务 {job.id} 已{'启用' if enabled else '禁用'}。")

    def run_selected(self) -> None:
        job_id = self.selected_job_id()
        if job_id is None:
            return
        self.summary_var.set(f"正在运行任务 {job_id} ...")

        def worker() -> None:
            result = run_scheduled_job(job_id, force=True)
            self.root.after(0, lambda: self.on_run_done(result["status"], result["message"]))

        threading.Thread(target=worker, daemon=True).start()

    def on_run_done(self, status: str, message: str) -> None:
        self.refresh(message=f"任务运行 {status}：{message}")

    def install_selected(self) -> None:
        job_id = self.selected_job_id()
        if job_id is None:
            return
        try:
            task_name = install_windows_task(job_id)
        except Exception as exc:
            messagebox.showerror("安装 Windows 任务失败", str(exc))
            return
        self.refresh(message=f"已安装 Windows 任务：{task_name}")

    def uninstall_selected(self) -> None:
        job_id = self.selected_job_id()
        if job_id is None:
            return
        try:
            task_name = uninstall_windows_task(job_id)
        except Exception as exc:
            messagebox.showerror("卸载 Windows 任务失败", str(exc))
            return
        self.refresh(message=f"已卸载 Windows 任务：{task_name}")

    def delete_selected(self) -> None:
        job_id = self.selected_job_id()
        if job_id is None:
            return
        if not messagebox.askyesno(
            "确认删除",
            (
                f"删除本地任务 {job_id}？\n\n"
                "如果该任务已安装到 Windows 任务计划程序，将同时卸载。"
            ),
        ):
            return
        try:
            delete_scheduled_job(job_id, remove_windows_task=True)
        except (RuntimeError, ValueError) as exc:
            messagebox.showerror("删除任务失败", str(exc))
            return
        self.refresh(message=f"已删除任务 {job_id}。")

    def refresh(self, *, message: str | None = None) -> None:
        DashboardDataApp.clear_tree(self.jobs_tree)
        for job in list_scheduled_jobs():
            windows_status = synchronize_windows_task_state(job.id)
            self.jobs_tree.insert(
                "",
                END,
                values=(
                    job.id,
                    job.name,
                    self.job_type_labels.get(job.job_type, job.job_type),
                    SOURCE_UPDATE_LABELS.get(
                        job.spider_name or "",
                        job.spider_name or "",
                    ),
                    f"{self.schedule_kind_labels.get(job.schedule_kind, job.schedule_kind)} {job.schedule_time}",
                    "自动增量"
                    if job.job_type == "source_update"
                    else self.date_mode_labels.get(job.date_mode, job.date_mode),
                    "是" if job.enabled else "否",
                    {
                        "installed": "已安装",
                        "missing": "外部已删除",
                        "unknown": "状态未知",
                        "unavailable": "当前系统无法核验",
                    }.get(windows_status, "未安装"),
                ),
            )

        DashboardDataApp.clear_tree(self.runs_tree)
        for run in list_recent_job_runs(limit=50):
            self.runs_tree.insert(
                "",
                END,
                values=(
                    run.id,
                    run.job_id,
                    self.run_status_labels.get(run.status, run.status),
                    run.started_at,
                    run.records_written if run.records_written is not None else "",
                    run.message,
                ),
            )
        self.summary_var.set(message or "定时任务/数据维护：已刷新。")

    @staticmethod
    def add_labeled_entry(parent, label: str, variable, row: int, column: int, width: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky=W, padx=4, pady=3)
        ttk.Entry(parent, textvariable=variable, width=width).grid(
            row=row,
            column=column + 1,
            sticky=W,
            padx=4,
            pady=3,
        )
