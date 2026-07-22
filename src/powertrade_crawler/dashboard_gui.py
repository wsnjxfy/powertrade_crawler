from __future__ import annotations

import threading
from datetime import date, timedelta
from pathlib import Path
from tkinter import BOTH, END, LEFT, W, X, BooleanVar, StringVar, messagebox, ttk
from tkinter.filedialog import asksaveasfilename

from powertrade_crawler.metrics import (
    export_dashboard_metric_rows,
    list_dashboard_metric_rows,
    list_dashboard_overview,
    list_dashboard_series,
    rebuild_dashboard_daily_metrics,
)
from powertrade_crawler.scheduler import (
    create_default_job_templates,
    create_scheduled_job,
    delete_scheduled_job,
    install_windows_task,
    list_recent_job_runs,
    list_scheduled_jobs,
    parse_optional_date,
    parse_params_json,
    run_scheduled_job,
    set_scheduled_job_enabled,
    uninstall_windows_task,
)


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
    def __init__(self, root) -> None:
        self.root = root
        self.summary_var = StringVar(value="Ready")
        self.name_var = StringVar(value="")
        self.job_type_var = StringVar(value="metrics")
        self.spider_name_var = StringVar(value="")
        self.schedule_kind_var = StringVar(value="daily")
        self.schedule_time_var = StringVar(value="02:00")
        self.date_mode_var = StringVar(value="last-30-days")
        self.start_date_var = StringVar(value="")
        self.end_date_var = StringVar(value="")
        self.enabled_var = BooleanVar(value=True)
        self.params_json_var = StringVar(value="{}")
        self.build_ui()
        self.refresh()

    def build_ui(self) -> None:
        form = ttk.Frame(self.root, padding=8)
        form.pack(fill=X)
        self.add_labeled_entry(form, "名称", self.name_var, 0, 0, 24)
        ttk.Label(form, text="类型").grid(row=0, column=2, sticky=W, padx=4, pady=3)
        ttk.Combobox(
            form,
            textvariable=self.job_type_var,
            values=["crawl", "metrics", "maintenance"],
            state="readonly",
            width=12,
        ).grid(row=0, column=3, sticky=W, padx=4, pady=3)
        self.add_labeled_entry(form, "Spider", self.spider_name_var, 0, 4, 28)

        ttk.Label(form, text="频率").grid(row=1, column=0, sticky=W, padx=4, pady=3)
        ttk.Combobox(
            form,
            textvariable=self.schedule_kind_var,
            values=["daily", "weekly", "monthly"],
            state="readonly",
            width=12,
        ).grid(row=1, column=1, sticky=W, padx=4, pady=3)
        self.add_labeled_entry(form, "时间", self.schedule_time_var, 1, 2, 8)
        ttk.Label(form, text="日期模式").grid(row=1, column=4, sticky=W, padx=4, pady=3)
        ttk.Combobox(
            form,
            textvariable=self.date_mode_var,
            values=["none", "yesterday", "last-7-days", "last-30-days", "custom"],
            state="readonly",
            width=16,
        ).grid(row=1, column=5, sticky=W, padx=4, pady=3)

        self.add_labeled_entry(form, "开始", self.start_date_var, 2, 0, 12)
        self.add_labeled_entry(form, "结束", self.end_date_var, 2, 2, 12)
        ttk.Checkbutton(form, text="启用", variable=self.enabled_var).grid(
            row=2,
            column=4,
            sticky=W,
            padx=4,
            pady=3,
        )
        ttk.Entry(form, textvariable=self.params_json_var, width=46).grid(
            row=2,
            column=5,
            sticky=W,
            padx=4,
            pady=3,
        )

        actions = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        actions.pack(fill=X)
        for label, command in (
            ("创建任务", self.create_job),
            ("创建默认模板", self.create_templates),
            ("刷新", self.refresh),
            ("启用", lambda: self.set_selected_enabled(True)),
            ("禁用", lambda: self.set_selected_enabled(False)),
            ("立即运行", self.run_selected),
            ("安装 Windows 任务", self.install_selected),
            ("卸载 Windows 任务", self.uninstall_selected),
            ("删除", self.delete_selected),
        ):
            ttk.Button(actions, text=label, command=command).pack(side=LEFT, padx=4)

        body = ttk.PanedWindow(self.root, orient="vertical")
        body.pack(fill=BOTH, expand=True, padx=8, pady=8)
        jobs_frame = ttk.Frame(body)
        runs_frame = ttk.Frame(body)
        body.add(jobs_frame, weight=2)
        body.add(runs_frame, weight=1)

        ttk.Label(jobs_frame, text="本地定时任务").pack(anchor=W)
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
                ("type", "类型", 90),
                ("spider", "Spider", 180),
                ("schedule", "频率/时间", 110),
                ("date", "日期模式", 110),
                ("enabled", "启用", 70),
                ("win", "Windows任务", 180),
            ),
        )
        self.jobs_tree.pack(fill=BOTH, expand=True, pady=(4, 0))

        ttk.Label(runs_frame, text="最近运行").pack(anchor=W)
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
        self.runs_tree.pack(fill=BOTH, expand=True, pady=(4, 0))
        ttk.Label(self.root, textvariable=self.summary_var, anchor=W).pack(fill=X, padx=8, pady=4)

    def create_job(self) -> None:
        try:
            job = create_scheduled_job(
                name=self.name_var.get(),
                job_type=self.job_type_var.get(),
                spider_name=self.spider_name_var.get().strip() or None,
                schedule_kind=self.schedule_kind_var.get(),
                schedule_time=self.schedule_time_var.get(),
                date_mode=self.date_mode_var.get(),
                start_date=parse_optional_date(self.start_date_var.get().strip() or None),
                end_date=parse_optional_date(self.end_date_var.get().strip() or None),
                enabled=self.enabled_var.get(),
                params=parse_params_json(self.params_json_var.get()),
            )
        except ValueError as exc:
            messagebox.showerror("创建任务失败", str(exc))
            return
        self.summary_var.set(f"已创建任务 {job.id}：{job.name}")
        self.refresh()

    def create_templates(self) -> None:
        created = create_default_job_templates()
        self.summary_var.set(f"已创建 {created} 个默认模板。")
        self.refresh()

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
        self.summary_var.set(f"任务 {job.id} 已{'启用' if enabled else '禁用'}。")
        self.refresh()

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
        self.summary_var.set(f"任务运行 {status}：{message}")
        self.refresh()

    def install_selected(self) -> None:
        job_id = self.selected_job_id()
        if job_id is None:
            return
        try:
            task_name = install_windows_task(job_id)
        except Exception as exc:
            messagebox.showerror("安装 Windows 任务失败", str(exc))
            return
        self.summary_var.set(f"已安装 Windows 任务：{task_name}")
        self.refresh()

    def uninstall_selected(self) -> None:
        job_id = self.selected_job_id()
        if job_id is None:
            return
        try:
            task_name = uninstall_windows_task(job_id)
        except Exception as exc:
            messagebox.showerror("卸载 Windows 任务失败", str(exc))
            return
        self.summary_var.set(f"已卸载 Windows 任务：{task_name}")
        self.refresh()

    def delete_selected(self) -> None:
        job_id = self.selected_job_id()
        if job_id is None:
            return
        if not messagebox.askyesno("确认删除", f"删除本地任务 {job_id}？"):
            return
        try:
            delete_scheduled_job(job_id)
        except ValueError as exc:
            messagebox.showerror("删除任务失败", str(exc))
            return
        self.summary_var.set(f"已删除任务 {job_id}。")
        self.refresh()

    def refresh(self) -> None:
        DashboardDataApp.clear_tree(self.jobs_tree)
        for job in list_scheduled_jobs():
            self.jobs_tree.insert(
                "",
                END,
                values=(
                    job.id,
                    job.name,
                    job.job_type,
                    job.spider_name or "",
                    f"{job.schedule_kind} {job.schedule_time}",
                    job.date_mode,
                    "是" if job.enabled else "否",
                    job.windows_task_name or "",
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
                    run.status,
                    run.started_at,
                    run.records_written if run.records_written is not None else "",
                    run.message,
                ),
            )
        self.summary_var.set("定时任务/数据维护：已刷新。")

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
