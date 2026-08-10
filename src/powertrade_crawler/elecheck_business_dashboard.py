import csv
import math
import sqlite3
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from tkinter import BOTH, LEFT, W, X, StringVar, messagebox, ttk
from tkinter.filedialog import asksaveasfilename
from typing import Any

from powertrade_crawler.config import get_settings
from powertrade_crawler.sqlite_utils import sqlite_row_connection
from powertrade_crawler.elecheck_dashboard import configure_matplotlib_fonts


PURCHASING_PRICE = "purchasing_price"
OPERATING_COST = "purchasing_system_operating_cost"
LINE_LOSS_COST = "line_loss_cost"
PURCHASING_TOTAL = "purchasing_sum"
PURCHASING_METRICS = (
    PURCHASING_PRICE,
    OPERATING_COST,
    LINE_LOSS_COST,
    PURCHASING_TOTAL,
)
PRICE_UNIT = "CNY/kWh"
PRICE_UNIT_ZH = "元/千瓦时"
WINDOW_OPTIONS = {
    "近 12 个月": 12,
    "近 24 个月": 24,
    "全部月份": None,
}


@dataclass(frozen=True)
class PurchasingMonthPoint:
    data_month: str
    purchasing_price: float | None
    operating_cost: float | None
    line_loss_cost: float | None
    total: float | None


@dataclass(frozen=True)
class PurchasingRankRow:
    province_name: str
    total: float


@dataclass(frozen=True)
class PurchasingDashboardData:
    province_name: str
    end_month: str
    window_label: str
    timeline: tuple[PurchasingMonthPoint, ...]
    ranking: tuple[PurchasingRankRow, ...]
    displayed_ranking: tuple[PurchasingRankRow, ...]
    rank_position: int | None

    @property
    def current(self) -> PurchasingMonthPoint | None:
        return next(
            (point for point in self.timeline if point.data_month == self.end_month),
            None,
        )

    @property
    def current_total(self) -> float | None:
        return self.current.total if self.current is not None else None

    @property
    def previous_total(self) -> float | None:
        previous_month = shift_month(self.end_month, -1)
        point = next(
            (row for row in self.timeline if row.data_month == previous_month),
            None,
        )
        return point.total if point is not None else None

    @property
    def month_over_month(self) -> float | None:
        if self.current_total is None or self.previous_total is None:
            return None
        return self.current_total - self.previous_total

    @property
    def covered_months(self) -> int:
        return sum(point.total is not None for point in self.timeline)


@dataclass(frozen=True)
class MechanismPriceRow:
    region_name: str
    category: str
    price: float | None
    clear_price: float | None
    collected_at: str | None = None

    @property
    def gap(self) -> float | None:
        if self.price is None or self.clear_price is None:
            return None
        return self.clear_price - self.price

    @property
    def relative_gap(self) -> float | None:
        if self.gap is None or self.price in (None, 0):
            return None
        return self.gap / self.price * 100


@dataclass(frozen=True)
class MechanismDashboardData:
    region_name: str
    category: str
    selected: MechanismPriceRow | None
    category_rows: tuple[MechanismPriceRow, ...]
    region_rows: tuple[MechanismPriceRow, ...]
    displayed_ranking: tuple[MechanismPriceRow, ...]


@dataclass(frozen=True)
class HoverArtist:
    artist: Any
    texts: tuple[str, ...]
    x_values: tuple[float, ...]
    y_values: tuple[float, ...]


class _SQLiteDashboardRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> AbstractContextManager[sqlite3.Connection]:
        return sqlite_row_connection(self.db_path)

    def table_exists(self, table_name: str) -> bool:
        if not self.db_path.exists():
            return False
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
        return row is not None

    def preferred_area_name(self, choices: list[str]) -> str | None:
        configured = get_settings().elecheck_area_code
        if configured in choices:
            return configured
        if not self.table_exists("elecheck_area_records"):
            return None
        with self.connect() as connection:
            row = connection.execute(
                "SELECT area_name FROM elecheck_area_records WHERE area_code = ?",
                (configured,),
            ).fetchone()
        if row is None:
            return None
        area_name = str(row["area_name"])
        return area_name if area_name in choices else None


class ElecheckPurchasingDashboardRepository(_SQLiteDashboardRepository):
    table_name = "elecheck_purchasing_records"

    def list_provinces(self) -> list[str]:
        if not self.table_exists(self.table_name):
            return []
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT province_name
                FROM elecheck_purchasing_records
                WHERE data_kind = 'national_table'
                  AND province_name IS NOT NULL
                  AND metric IN (:price, :operating, :loss, :total)
                ORDER BY province_name
                """,
                _purchasing_metric_params(),
            ).fetchall()
        return [str(row["province_name"]) for row in rows]

    def list_months(self, province_name: str) -> list[str]:
        if not self.table_exists(self.table_name):
            return []
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT data_month
                FROM elecheck_purchasing_records
                WHERE data_kind = 'national_table'
                  AND province_name = :province_name
                  AND metric IN (:price, :operating, :loss, :total)
                ORDER BY data_month
                """,
                {"province_name": province_name, **_purchasing_metric_params()},
            ).fetchall()
        return [str(row["data_month"]) for row in rows]

    def load_dashboard_data(
        self,
        *,
        province_name: str,
        end_month: str,
        window_label: str,
    ) -> PurchasingDashboardData:
        window_months = WINDOW_OPTIONS[window_label]
        if window_months is None:
            start_month = self.first_month(province_name) or end_month
        else:
            start_month = shift_month(end_month, -(window_months - 1))
        timeline = self.load_timeline(province_name, start_month, end_month)
        ranking = self.load_ranking(end_month)
        rank_position = next(
            (
                index
                for index, row in enumerate(
                    sorted(ranking, key=lambda item: (-item.total, item.province_name)),
                    start=1,
                )
                if row.province_name == province_name
            ),
            None,
        )
        return PurchasingDashboardData(
            province_name=province_name,
            end_month=end_month,
            window_label=window_label,
            timeline=timeline,
            ranking=ranking,
            displayed_ranking=select_purchasing_ranking_rows(
                ranking,
                province_name,
            ),
            rank_position=rank_position,
        )

    def first_month(self, province_name: str) -> str | None:
        if not self.table_exists(self.table_name):
            return None
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT MIN(data_month) AS first_month
                FROM elecheck_purchasing_records
                WHERE data_kind = 'national_table'
                  AND province_name = :province_name
                  AND metric IN (:price, :operating, :loss, :total)
                """,
                {"province_name": province_name, **_purchasing_metric_params()},
            ).fetchone()
        if row is None or row["first_month"] is None:
            return None
        return str(row["first_month"])

    def load_timeline(
        self,
        province_name: str,
        start_month: str,
        end_month: str,
    ) -> tuple[PurchasingMonthPoint, ...]:
        if not self.table_exists(self.table_name):
            return ()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT data_month,
                       MAX(CASE WHEN metric = :price THEN value END) AS purchasing_price,
                       MAX(CASE WHEN metric = :operating THEN value END) AS operating_cost,
                       MAX(CASE WHEN metric = :loss THEN value END) AS line_loss_cost,
                       MAX(CASE WHEN metric = :total THEN value END) AS total
                FROM elecheck_purchasing_records
                WHERE data_kind = 'national_table'
                  AND province_name = :province_name
                  AND data_month >= :start_month
                  AND data_month <= :end_month
                  AND metric IN (:price, :operating, :loss, :total)
                GROUP BY data_month
                ORDER BY data_month
                """,
                {
                    "province_name": province_name,
                    "start_month": start_month,
                    "end_month": end_month,
                    **_purchasing_metric_params(),
                },
            ).fetchall()
        by_month = {
            str(row["data_month"]): PurchasingMonthPoint(
                data_month=str(row["data_month"]),
                purchasing_price=_optional_float(row["purchasing_price"]),
                operating_cost=_optional_float(row["operating_cost"]),
                line_loss_cost=_optional_float(row["line_loss_cost"]),
                total=_optional_float(row["total"]),
            )
            for row in rows
        }
        return tuple(
            by_month.get(
                data_month,
                PurchasingMonthPoint(data_month, None, None, None, None),
            )
            for data_month in iter_months(start_month, end_month)
        )

    def load_ranking(self, data_month: str) -> tuple[PurchasingRankRow, ...]:
        if not self.table_exists(self.table_name):
            return ()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT province_name, value AS total
                FROM elecheck_purchasing_records
                WHERE data_kind = 'national_table'
                  AND data_month = :data_month
                  AND metric = :total
                  AND province_name IS NOT NULL
                  AND value IS NOT NULL
                ORDER BY value, province_name
                """,
                {"data_month": data_month, "total": PURCHASING_TOTAL},
            ).fetchall()
        return tuple(
            PurchasingRankRow(str(row["province_name"]), float(row["total"]))
            for row in rows
        )


class ElecheckMechanismDashboardRepository(_SQLiteDashboardRepository):
    table_name = "elecheck_mechanism_electricity_price_records"

    def list_regions(self) -> list[str]:
        return self._distinct_values("region_name")

    def list_categories(self) -> list[str]:
        return self._distinct_values("category")

    def _distinct_values(self, column_name: str) -> list[str]:
        if not self.table_exists(self.table_name):
            return []
        if column_name not in {"region_name", "category"}:
            raise ValueError(f"Unsupported mechanism column: {column_name}")
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT DISTINCT {column_name} FROM {self.table_name} "
                f"WHERE {column_name} IS NOT NULL ORDER BY {column_name}"
            ).fetchall()
        return [str(row[column_name]) for row in rows]

    def load_dashboard_data(
        self,
        *,
        region_name: str,
        category: str,
    ) -> MechanismDashboardData:
        category_rows = self.load_category_rows(category)
        region_rows = self.load_region_rows(region_name)
        selected = next(
            (row for row in region_rows if row.category == category),
            None,
        )
        complete_rows = tuple(row for row in category_rows if row.gap is not None)
        return MechanismDashboardData(
            region_name=region_name,
            category=category,
            selected=selected,
            category_rows=category_rows,
            region_rows=region_rows,
            displayed_ranking=select_mechanism_ranking_rows(
                complete_rows,
                region_name,
            ),
        )

    def load_category_rows(self, category: str) -> tuple[MechanismPriceRow, ...]:
        return self._load_rows("category = ?", (category,), "region_name")

    def load_region_rows(self, region_name: str) -> tuple[MechanismPriceRow, ...]:
        return self._load_rows("region_name = ?", (region_name,), "category")

    def _load_rows(
        self,
        where_clause: str,
        params: tuple[str, ...],
        order_by: str,
    ) -> tuple[MechanismPriceRow, ...]:
        if not self.table_exists(self.table_name):
            return ()
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT region_name, category, price, clear_price, collected_at
                FROM {self.table_name}
                WHERE {where_clause}
                ORDER BY {order_by}
                """,
                params,
            ).fetchall()
        return tuple(
            MechanismPriceRow(
                region_name=str(row["region_name"]),
                category=str(row["category"]),
                price=_optional_float(row["price"]),
                clear_price=_optional_float(row["clear_price"]),
                collected_at=str(row["collected_at"]) if row["collected_at"] else None,
            )
            for row in rows
        )


class _ElecheckChartAppBase:
    def __init__(self, root, status_text: str) -> None:
        self.root = root
        self.status_var = StringVar(value=status_text)
        self.figure = None
        self.canvas = None
        self.hover_artists: list[HoverArtist] = []
        self.annotations: dict[Any, Any] = {}

    def build_chart_canvas(self, parent, *, height: float = 6.6) -> None:
        try:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
        except ImportError as exc:
            ttk.Label(parent, text=f"Matplotlib 未安装，无法显示图表：{exc}").pack(
                fill=BOTH,
                expand=True,
            )
            return
        configure_matplotlib_fonts()
        self.figure = Figure(figsize=(10, height), dpi=100, layout="constrained")
        self.canvas = FigureCanvasTkAgg(self.figure, master=parent)
        self.canvas.get_tk_widget().pack(fill=BOTH, expand=True)
        self.canvas.mpl_connect("motion_notify_event", self.on_hover)
        self.canvas.mpl_connect("figure_leave_event", self.hide_annotations)

    def finish_draw(self, hover_artists: list[HoverArtist]) -> None:
        if self.figure is None or self.canvas is None:
            return
        self.hover_artists = hover_artists
        self.annotations = create_hover_annotations(hover_artists)
        self.canvas.draw()

    def draw_empty(self, message: str) -> None:
        self.status_var.set(message)
        if self.figure is None or self.canvas is None:
            return
        self.figure.clear()
        axis = self.figure.add_subplot(111)
        axis.text(0.5, 0.5, message, ha="center", va="center", transform=axis.transAxes)
        axis.set_axis_off()
        self.hover_artists = []
        self.annotations = {}
        self.canvas.draw()

    def on_hover(self, event) -> None:
        if self.canvas is None:
            return
        selected: tuple[HoverArtist, int] | None = None
        for entry in self.hover_artists:
            if event.inaxes is not entry.artist.axes:
                continue
            contains, details = entry.artist.contains(event)
            if not contains:
                continue
            indexes = details.get("ind") or [0]
            selected = (entry, int(indexes[0]))
            break

        changed = self.hide_annotations(redraw=False)
        if selected is not None:
            entry, index = selected
            if index >= len(entry.texts):
                index = 0
            annotation = self.annotations[entry.artist.axes]
            annotation.xy = (entry.x_values[index], entry.y_values[index])
            annotation.set_text(entry.texts[index])
            annotation.set_visible(True)
            changed = True
        if changed:
            self.canvas.draw_idle()

    def hide_annotations(self, _event=None, *, redraw: bool = True) -> bool:
        changed = False
        for annotation in self.annotations.values():
            if annotation.get_visible():
                annotation.set_visible(False)
                changed = True
        if changed and redraw and self.canvas is not None:
            self.canvas.draw_idle()
        return changed


class ElecheckPurchasingDashboardApp(_ElecheckChartAppBase):
    def __init__(self, root, db_path: Path) -> None:
        super().__init__(root, "正在读取 Elecheck 代理购电数据...")
        self.repository = ElecheckPurchasingDashboardRepository(db_path)
        self.province_var = StringVar()
        self.month_var = StringVar()
        self.window_var = StringVar(value="近 24 个月")
        self.total_summary_var = StringVar(value="当前合计价 -")
        self.change_summary_var = StringVar(value="环比 -")
        self.rank_summary_var = StringVar(value="全国排名 -")
        self.coverage_summary_var = StringVar(value="历史覆盖 -")
        self.current_data: PurchasingDashboardData | None = None
        self.available_months: list[str] = []
        self.build_ui()
        self.refresh_after_crawl(select_latest=True)

    def build_ui(self) -> None:
        toolbar = ttk.Frame(self.root, padding=8)
        toolbar.pack(fill=X)
        ttk.Label(toolbar, text="省份").pack(side=LEFT, padx=(0, 4))
        self.province_combo = ttk.Combobox(
            toolbar,
            textvariable=self.province_var,
            state="readonly",
            width=16,
        )
        self.province_combo.pack(side=LEFT, padx=(0, 10))
        self.province_combo.bind("<<ComboboxSelected>>", self.on_province_changed)
        ttk.Label(toolbar, text="截止月份").pack(side=LEFT, padx=(0, 4))
        self.month_combo = ttk.Combobox(
            toolbar,
            textvariable=self.month_var,
            state="readonly",
            width=10,
        )
        self.month_combo.pack(side=LEFT, padx=(0, 10))
        self.month_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_chart())
        ttk.Label(toolbar, text="趋势窗口").pack(side=LEFT, padx=(0, 4))
        self.window_combo = ttk.Combobox(
            toolbar,
            textvariable=self.window_var,
            values=list(WINDOW_OPTIONS),
            state="readonly",
            width=12,
        )
        self.window_combo.pack(side=LEFT, padx=(0, 10))
        self.window_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_chart())
        ttk.Button(toolbar, text="刷新", command=self.refresh_chart).pack(side=LEFT, padx=(0, 4))
        ttk.Button(toolbar, text="导出 CSV", command=self.export_csv).pack(side=LEFT, padx=(0, 4))
        ttk.Button(toolbar, text="导出 PNG", command=self.export_png).pack(side=LEFT)

        summary = ttk.Frame(self.root, padding=(8, 0, 8, 6))
        summary.pack(fill=X)
        for index, variable in enumerate(
            (
                self.total_summary_var,
                self.change_summary_var,
                self.rank_summary_var,
                self.coverage_summary_var,
            )
        ):
            if index:
                ttk.Separator(summary, orient="vertical").pack(side=LEFT, fill="y", padx=10)
            ttk.Label(summary, textvariable=variable).pack(side=LEFT)

        chart_frame = ttk.Frame(self.root)
        chart_frame.pack(fill=BOTH, expand=True, padx=8)
        self.build_chart_canvas(chart_frame)
        ttk.Label(self.root, textvariable=self.status_var, anchor=W).pack(
            fill=X,
            padx=8,
            pady=(3, 6),
        )

    def refresh_after_crawl(self, *, select_latest: bool = True) -> None:
        provinces = self.repository.list_provinces()
        self.province_combo["values"] = provinces
        if not provinces:
            self.province_var.set("")
            self.month_var.set("")
            self.available_months = []
            self.current_data = None
            self.show_empty("暂无全国月度代理购电价格数据。")
            return
        current = self.province_var.get()
        preferred = self.repository.preferred_area_name(provinces)
        selected = current if current in provinces else preferred or provinces[0]
        self.province_var.set(selected)
        self.load_months(select_latest=select_latest)

    def on_province_changed(self, _event) -> None:
        self.load_months(select_latest=True)

    def load_months(self, *, select_latest: bool) -> None:
        previous_month = self.month_var.get()
        self.available_months = self.repository.list_months(self.province_var.get())
        self.month_combo["values"] = list(reversed(self.available_months))
        if not self.available_months:
            self.month_var.set("")
            self.current_data = None
            self.show_empty(f"{self.province_var.get()} 暂无代理购电价格数据。")
            return
        if not select_latest and previous_month in self.available_months:
            self.month_var.set(previous_month)
        else:
            self.month_var.set(self.available_months[-1])
        self.refresh_chart()

    def refresh_chart(self) -> None:
        province_name = self.province_var.get()
        data_month = self.month_var.get()
        window_label = self.window_var.get()
        if not province_name or not data_month or window_label not in WINDOW_OPTIONS:
            self.current_data = None
            self.show_empty("请选择有数据的省份、月份和趋势窗口。")
            return
        self.current_data = self.repository.load_dashboard_data(
            province_name=province_name,
            end_month=data_month,
            window_label=window_label,
        )
        self.update_summaries(self.current_data)
        if self.figure is not None:
            self.finish_draw(draw_purchasing_figure(self.figure, self.current_data))

    def update_summaries(self, data: PurchasingDashboardData) -> None:
        self.total_summary_var.set(format_price_summary("当前合计价", data.current_total))
        self.change_summary_var.set(format_signed_summary("环比", data.month_over_month))
        rank_total = len(data.ranking)
        rank_text = f"{data.rank_position}/{rank_total}" if data.rank_position else "-"
        self.rank_summary_var.set(f"全国排名 {rank_text}")
        self.coverage_summary_var.set(
            f"历史覆盖 {data.covered_months}/{len(data.timeline)} 个月"
        )
        status_parts = [f"{data.province_name} | 截止 {data.end_month} | {data.window_label}"]
        if data.current_total is None:
            status_parts.append("所选月份暂无合计价")
        if data.covered_months < len(data.timeline):
            status_parts.append("缺失月份保留断点")
        self.status_var.set(" | ".join(status_parts))

    def show_empty(self, message: str) -> None:
        self.total_summary_var.set("当前合计价 -")
        self.change_summary_var.set("环比 -")
        self.rank_summary_var.set("全国排名 -")
        self.coverage_summary_var.set("历史覆盖 -")
        self.draw_empty(message)

    def export_csv(self) -> None:
        if self.current_data is None or not self.current_data.timeline:
            messagebox.showinfo("没有可导出数据", "当前条件没有代理购电分析数据。")
            return
        output_path = asksaveasfilename(
            title="导出 Elecheck 代理购电分析数据",
            initialfile=(
                f"elecheck_代理购电_{self.current_data.province_name}_"
                f"{self.current_data.end_month}.csv"
            ),
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not output_path:
            return
        row_count = export_purchasing_dashboard_csv(Path(output_path), self.current_data)
        self.status_var.set(f"已导出 {row_count} 行到 {output_path}")

    def export_png(self) -> None:
        if self.figure is None or self.current_data is None:
            messagebox.showinfo("没有可导出图表", "当前条件没有代理购电图表。")
            return
        output_path = asksaveasfilename(
            title="导出 Elecheck 代理购电分析图表",
            initialfile=(
                f"elecheck_代理购电_{self.current_data.province_name}_"
                f"{self.current_data.end_month}.png"
            ),
            defaultextension=".png",
            filetypes=[("PNG image", "*.png"), ("All files", "*.*")],
        )
        if not output_path:
            return
        self.figure.savefig(output_path, dpi=160, bbox_inches="tight")
        self.status_var.set(f"图表已导出到 {output_path}")


class ElecheckMechanismDashboardApp(_ElecheckChartAppBase):
    def __init__(self, root, db_path: Path) -> None:
        super().__init__(root, "正在读取 Elecheck 增量机制电价数据...")
        self.repository = ElecheckMechanismDashboardRepository(db_path)
        self.category_var = StringVar()
        self.region_var = StringVar()
        self.base_summary_var = StringVar(value="燃煤基准价 -")
        self.clear_summary_var = StringVar(value="机制电价 -")
        self.gap_summary_var = StringVar(value="绝对差额 -")
        self.relative_summary_var = StringVar(value="相对差幅 -")
        self.current_data: MechanismDashboardData | None = None
        self.build_ui()
        self.refresh_after_crawl()

    def build_ui(self) -> None:
        toolbar = ttk.Frame(self.root, padding=8)
        toolbar.pack(fill=X)
        ttk.Label(toolbar, text="电源类型").pack(side=LEFT, padx=(0, 4))
        self.category_combo = ttk.Combobox(
            toolbar,
            textvariable=self.category_var,
            state="readonly",
            width=16,
        )
        self.category_combo.pack(side=LEFT, padx=(0, 10))
        self.category_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_chart())
        ttk.Label(toolbar, text="地区").pack(side=LEFT, padx=(0, 4))
        self.region_combo = ttk.Combobox(
            toolbar,
            textvariable=self.region_var,
            state="readonly",
            width=16,
        )
        self.region_combo.pack(side=LEFT, padx=(0, 10))
        self.region_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_chart())
        ttk.Button(toolbar, text="刷新", command=self.refresh_chart).pack(side=LEFT, padx=(0, 4))
        ttk.Button(toolbar, text="导出 CSV", command=self.export_csv).pack(side=LEFT, padx=(0, 4))
        ttk.Button(toolbar, text="导出 PNG", command=self.export_png).pack(side=LEFT)

        summary = ttk.Frame(self.root, padding=(8, 0, 8, 6))
        summary.pack(fill=X)
        for index, variable in enumerate(
            (
                self.base_summary_var,
                self.clear_summary_var,
                self.gap_summary_var,
                self.relative_summary_var,
            )
        ):
            if index:
                ttk.Separator(summary, orient="vertical").pack(side=LEFT, fill="y", padx=10)
            ttk.Label(summary, textvariable=variable).pack(side=LEFT)

        chart_frame = ttk.Frame(self.root)
        chart_frame.pack(fill=BOTH, expand=True, padx=8)
        self.build_chart_canvas(chart_frame)
        ttk.Label(self.root, textvariable=self.status_var, anchor=W).pack(
            fill=X,
            padx=8,
            pady=(3, 6),
        )

    def refresh_after_crawl(self) -> None:
        regions = self.repository.list_regions()
        categories = self.repository.list_categories()
        self.region_combo["values"] = regions
        self.category_combo["values"] = categories
        if not regions or not categories:
            self.region_var.set("")
            self.category_var.set("")
            self.current_data = None
            self.show_empty("暂无增量机制电价快照数据。")
            return
        current_region = self.region_var.get()
        preferred_region = self.repository.preferred_area_name(regions)
        self.region_var.set(
            current_region if current_region in regions else preferred_region or regions[0]
        )
        current_category = self.category_var.get()
        default_category = "光伏" if "光伏" in categories else categories[0]
        self.category_var.set(
            current_category if current_category in categories else default_category
        )
        self.refresh_chart()

    def refresh_chart(self) -> None:
        region_name = self.region_var.get()
        category = self.category_var.get()
        if not region_name or not category:
            self.current_data = None
            self.show_empty("请选择有数据的地区和电源类型。")
            return
        self.current_data = self.repository.load_dashboard_data(
            region_name=region_name,
            category=category,
        )
        self.update_summaries(self.current_data)
        if self.figure is not None:
            self.finish_draw(draw_mechanism_figure(self.figure, self.current_data))

    def update_summaries(self, data: MechanismDashboardData) -> None:
        selected = data.selected
        self.base_summary_var.set(
            format_price_summary("燃煤基准价", selected.price if selected else None)
        )
        self.clear_summary_var.set(
            format_price_summary("机制电价", selected.clear_price if selected else None)
        )
        self.gap_summary_var.set(
            format_signed_summary("绝对差额", selected.gap if selected else None)
        )
        relative = selected.relative_gap if selected else None
        self.relative_summary_var.set(
            "相对差幅 -" if relative is None else f"相对差幅 {relative:+.2f}%"
        )
        status_parts = [f"当前快照 | {data.region_name} | {data.category}"]
        if selected is None:
            status_parts.append("当前地区没有该电源类型")
        elif selected.clear_price is None:
            status_parts.append("暂无机制电价，未进入价差排名")
        self.status_var.set(" | ".join(status_parts))

    def show_empty(self, message: str) -> None:
        self.base_summary_var.set("燃煤基准价 -")
        self.clear_summary_var.set("机制电价 -")
        self.gap_summary_var.set("绝对差额 -")
        self.relative_summary_var.set("相对差幅 -")
        self.draw_empty(message)

    def export_csv(self) -> None:
        if self.current_data is None or not (
            self.current_data.category_rows or self.current_data.region_rows
        ):
            messagebox.showinfo("没有可导出数据", "当前条件没有增量机制分析数据。")
            return
        output_path = asksaveasfilename(
            title="导出 Elecheck 增量机制分析数据",
            initialfile=(
                f"elecheck_增量机制_{self.current_data.region_name}_"
                f"{self.current_data.category}.csv"
            ),
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not output_path:
            return
        row_count = export_mechanism_dashboard_csv(Path(output_path), self.current_data)
        self.status_var.set(f"已导出 {row_count} 行到 {output_path}")

    def export_png(self) -> None:
        if self.figure is None or self.current_data is None:
            messagebox.showinfo("没有可导出图表", "当前条件没有增量机制图表。")
            return
        output_path = asksaveasfilename(
            title="导出 Elecheck 增量机制分析图表",
            initialfile=(
                f"elecheck_增量机制_{self.current_data.region_name}_"
                f"{self.current_data.category}.png"
            ),
            defaultextension=".png",
            filetypes=[("PNG image", "*.png"), ("All files", "*.*")],
        )
        if not output_path:
            return
        self.figure.savefig(output_path, dpi=160, bbox_inches="tight")
        self.status_var.set(f"图表已导出到 {output_path}")


def draw_purchasing_figure(figure, data: PurchasingDashboardData) -> list[HoverArtist]:
    configure_matplotlib_fonts()
    figure.clear()
    grid = figure.add_gridspec(2, 1, height_ratios=(3.2, 2.5))
    trend_axis = figure.add_subplot(grid[0])
    rank_axis = figure.add_subplot(grid[1])
    hover_artists: list[HoverArtist] = []

    x_values = list(range(len(data.timeline)))
    positive_bases = [0.0] * len(data.timeline)
    negative_bases = [0.0] * len(data.timeline)
    components = (
        ("代理购电价", "purchasing_price", "#4c78a8"),
        ("系统运行费折价", "operating_cost", "#f2a541"),
        ("线损折价", "line_loss_cost", "#59a14f"),
    )
    for label, field_name, color in components:
        legend_added = False
        for index, point in enumerate(data.timeline):
            value = getattr(point, field_name)
            if value is None:
                continue
            bottom = positive_bases[index] if value >= 0 else negative_bases[index]
            container = trend_axis.bar(
                index,
                value,
                width=0.72,
                bottom=bottom,
                color=color,
                label=label if not legend_added else None,
            )
            legend_added = True
            if value >= 0:
                positive_bases[index] += value
            else:
                negative_bases[index] += value
            rectangle = container.patches[0]
            rectangle.set_picker(True)
            hover_artists.append(
                HoverArtist(
                    rectangle,
                    (f"{label}\n{point.data_month}\n{value:.6f} {PRICE_UNIT_ZH}",),
                    (float(index),),
                    (bottom + value,),
                )
            )

    totals = [point.total if point.total is not None else math.nan for point in data.timeline]
    (total_line,) = trend_axis.plot(
        x_values,
        totals,
        color="#c33c54",
        linewidth=2.0,
        marker="o",
        markersize=4,
        label="合计价",
    )
    total_line.set_picker(5)
    hover_artists.append(
        HoverArtist(
            total_line,
            tuple(
                f"合计价\n{point.data_month}\n"
                + (
                    f"{point.total:.6f} {PRICE_UNIT_ZH}"
                    if point.total is not None
                    else "暂无数据"
                )
                for point in data.timeline
            ),
            tuple(float(value) for value in x_values),
            tuple(totals),
        )
    )
    trend_axis.axhline(0, color="#666666", linewidth=0.8)
    trend_axis.set_title(f"{data.province_name} 代理购电月度费用构成")
    trend_axis.set_ylabel(PRICE_UNIT_ZH)
    trend_axis.grid(True, axis="y", alpha=0.2)
    tick_step = 1 if len(data.timeline) <= 12 else 2
    tick_indexes = list(range(0, len(data.timeline), tick_step))
    if x_values and x_values[-1] not in tick_indexes:
        tick_indexes.append(x_values[-1])
    trend_axis.set_xticks(tick_indexes)
    trend_axis.set_xticklabels(
        [data.timeline[index].data_month for index in tick_indexes],
        rotation=30,
        ha="right",
    )
    trend_axis.legend(loc="best", ncols=4)

    ranking = data.displayed_ranking
    if ranking:
        y_values = list(range(len(ranking)))
        colors = [
            "#2f6f4e" if row.province_name == data.province_name else "#4c78a8"
            for row in ranking
        ]
        containers = rank_axis.barh(
            y_values,
            [row.total for row in ranking],
            color=colors,
            height=0.72,
        )
        labels = [
            f"{row.province_name}（当前）"
            if row.province_name == data.province_name
            else row.province_name
            for row in ranking
        ]
        rank_axis.set_yticks(y_values)
        rank_axis.set_yticklabels(labels)
        for rectangle, row, y_value in zip(containers.patches, ranking, y_values, strict=True):
            rectangle.set_picker(True)
            hover_artists.append(
                HoverArtist(
                    rectangle,
                    (
                        f"{row.province_name}\n{data.end_month} 合计价\n"
                        f"{row.total:.6f} {PRICE_UNIT_ZH}",
                    ),
                    (row.total,),
                    (float(y_value),),
                )
            )
        rank_axis.set_title(
            f"{data.end_month} 全国合计价：最低 10、最高 10 与当前省份"
        )
        rank_axis.set_xlabel(PRICE_UNIT_ZH)
        rank_axis.grid(True, axis="x", alpha=0.2)
    else:
        rank_axis.text(
            0.5,
            0.5,
            f"{data.end_month} 暂无省际合计价排名",
            ha="center",
            va="center",
            transform=rank_axis.transAxes,
        )
        rank_axis.set_axis_off()
    return hover_artists


def draw_mechanism_figure(figure, data: MechanismDashboardData) -> list[HoverArtist]:
    configure_matplotlib_fonts()
    figure.clear()
    grid = figure.add_gridspec(2, 1, height_ratios=(3.4, 2.0))
    rank_axis = figure.add_subplot(grid[0])
    region_axis = figure.add_subplot(grid[1])
    hover_artists: list[HoverArtist] = []

    ranking = data.displayed_ranking
    if ranking:
        y_values = list(range(len(ranking)))
        base_values = [float(row.price) for row in ranking if row.price is not None]
        clear_values = [float(row.clear_price) for row in ranking if row.clear_price is not None]
        for y_value, row in zip(y_values, ranking, strict=True):
            line_color = "#2f6f4e" if row.region_name == data.region_name else "#888888"
            rank_axis.hlines(
                y_value,
                float(row.price),
                float(row.clear_price),
                color=line_color,
                linewidth=2 if row.region_name == data.region_name else 1,
            )
        base_scatter = rank_axis.scatter(
            base_values,
            y_values,
            color="#4c78a8",
            marker="o",
            label="燃煤基准价",
            picker=True,
        )
        clear_scatter = rank_axis.scatter(
            clear_values,
            y_values,
            color="#e07b39",
            marker="D",
            label="26年增量机制电价",
            picker=True,
        )
        base_texts = tuple(
            f"{row.region_name}\n燃煤基准价\n{row.price:.6f} {PRICE_UNIT_ZH}"
            for row in ranking
        )
        clear_texts = tuple(
            f"{row.region_name}\n26年增量机制电价\n{row.clear_price:.6f} {PRICE_UNIT_ZH}"
            f"\n差额 {row.gap:+.6f} {PRICE_UNIT_ZH}"
            for row in ranking
        )
        hover_artists.extend(
            [
                HoverArtist(
                    base_scatter,
                    base_texts,
                    tuple(base_values),
                    tuple(float(value) for value in y_values),
                ),
                HoverArtist(
                    clear_scatter,
                    clear_texts,
                    tuple(clear_values),
                    tuple(float(value) for value in y_values),
                ),
            ]
        )
        labels = [
            f"{row.region_name}（当前）"
            if row.region_name == data.region_name
            else row.region_name
            for row in ranking
        ]
        rank_axis.set_yticks(y_values)
        rank_axis.set_yticklabels(labels)
        rank_axis.invert_yaxis()
        rank_axis.set_title(
            f"{data.category}：机制电价与燃煤基准价（差额最低 10、最高 10）"
        )
        rank_axis.set_xlabel(PRICE_UNIT_ZH)
        rank_axis.grid(True, axis="x", alpha=0.2)
        rank_axis.legend(loc="best")
    else:
        rank_axis.text(
            0.5,
            0.5,
            f"{data.category} 暂无可计算价差的地区",
            ha="center",
            va="center",
            transform=rank_axis.transAxes,
        )
        rank_axis.set_axis_off()

    if data.region_rows:
        x_values = list(range(len(data.region_rows)))
        width = 0.36
        base_legend = False
        clear_legend = False
        for index, row in enumerate(data.region_rows):
            if row.price is not None:
                container = region_axis.bar(
                    index - width / 2,
                    row.price,
                    width,
                    color="#4c78a8",
                    label="燃煤基准价" if not base_legend else None,
                )
                base_legend = True
                rectangle = container.patches[0]
                rectangle.set_picker(True)
                hover_artists.append(
                    HoverArtist(
                        rectangle,
                        (
                            f"{data.region_name} {row.category}\n燃煤基准价\n"
                            f"{row.price:.6f} {PRICE_UNIT_ZH}",
                        ),
                        (float(index - width / 2),),
                        (row.price,),
                    )
                )
            if row.clear_price is not None:
                container = region_axis.bar(
                    index + width / 2,
                    row.clear_price,
                    width,
                    color="#e07b39",
                    label="26年增量机制电价" if not clear_legend else None,
                )
                clear_legend = True
                rectangle = container.patches[0]
                rectangle.set_picker(True)
                hover_artists.append(
                    HoverArtist(
                        rectangle,
                        (
                            f"{data.region_name} {row.category}\n26年增量机制电价\n"
                            f"{row.clear_price:.6f} {PRICE_UNIT_ZH}",
                        ),
                        (float(index + width / 2),),
                        (row.clear_price,),
                    )
                )
        region_axis.set_xticks(x_values)
        region_axis.set_xticklabels([row.category for row in data.region_rows])
        region_axis.set_title(f"{data.region_name} 各电源类型价格对比")
        region_axis.set_ylabel(PRICE_UNIT_ZH)
        region_axis.grid(True, axis="y", alpha=0.2)
        region_axis.legend(loc="best")
    else:
        region_axis.text(
            0.5,
            0.5,
            f"{data.region_name} 暂无增量机制电价记录",
            ha="center",
            va="center",
            transform=region_axis.transAxes,
        )
        region_axis.set_axis_off()
    return hover_artists


def export_purchasing_dashboard_csv(
    output_path: Path,
    data: PurchasingDashboardData,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "记录类型",
        "月份",
        "省份",
        "代理购电价",
        "系统运行费折价",
        "线损折价",
        "合计价",
        "全国排名",
        "单位",
    ]
    row_count = 0
    rank_by_province = {
        row.province_name: index
        for index, row in enumerate(
            sorted(data.ranking, key=lambda item: (-item.total, item.province_name)),
            start=1,
        )
    }
    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for point in data.timeline:
            writer.writerow(
                {
                    "记录类型": "月度趋势",
                    "月份": point.data_month,
                    "省份": data.province_name,
                    "代理购电价": _csv_value(point.purchasing_price),
                    "系统运行费折价": _csv_value(point.operating_cost),
                    "线损折价": _csv_value(point.line_loss_cost),
                    "合计价": _csv_value(point.total),
                    "全国排名": "",
                    "单位": PRICE_UNIT,
                }
            )
            row_count += 1
        for row in data.displayed_ranking:
            writer.writerow(
                {
                    "记录类型": "省际排名",
                    "月份": data.end_month,
                    "省份": row.province_name,
                    "代理购电价": "",
                    "系统运行费折价": "",
                    "线损折价": "",
                    "合计价": row.total,
                    "全国排名": rank_by_province[row.province_name],
                    "单位": PRICE_UNIT,
                }
            )
            row_count += 1
    return row_count


def export_mechanism_dashboard_csv(
    output_path: Path,
    data: MechanismDashboardData,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "记录类型",
        "地区",
        "电源类型",
        "燃煤基准价",
        "26年增量机制电价",
        "差额",
        "相对差幅",
        "单位",
    ]
    rows = [
        ("省际比较", row)
        for row in data.displayed_ranking
    ] + [
        ("地区电源类型", row)
        for row in data.region_rows
    ]
    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row_type, row in rows:
            writer.writerow(
                {
                    "记录类型": row_type,
                    "地区": row.region_name,
                    "电源类型": row.category,
                    "燃煤基准价": _csv_value(row.price),
                    "26年增量机制电价": _csv_value(row.clear_price),
                    "差额": _csv_value(row.gap),
                    "相对差幅": _csv_value(row.relative_gap),
                    "单位": PRICE_UNIT,
                }
            )
    return len(rows)


def select_purchasing_ranking_rows(
    rows: tuple[PurchasingRankRow, ...],
    selected_province: str,
    limit: int = 10,
) -> tuple[PurchasingRankRow, ...]:
    ordered = sorted(rows, key=lambda row: (row.total, row.province_name))
    selected_names = {
        row.province_name for row in ordered[:limit] + ordered[-limit:]
    }
    selected_names.add(selected_province)
    return tuple(row for row in ordered if row.province_name in selected_names)


def select_mechanism_ranking_rows(
    rows: tuple[MechanismPriceRow, ...],
    selected_region: str,
    limit: int = 10,
) -> tuple[MechanismPriceRow, ...]:
    ordered = sorted(
        (row for row in rows if row.gap is not None),
        key=lambda row: (float(row.gap), row.region_name),
    )
    selected_names = {
        row.region_name for row in ordered[:limit] + ordered[-limit:]
    }
    if any(row.region_name == selected_region for row in ordered):
        selected_names.add(selected_region)
    return tuple(row for row in ordered if row.region_name in selected_names)


def iter_months(start_month: str, end_month: str) -> tuple[str, ...]:
    start_index = month_to_index(start_month)
    end_index = month_to_index(end_month)
    if start_index > end_index:
        return ()
    return tuple(index_to_month(index) for index in range(start_index, end_index + 1))


def shift_month(data_month: str, offset: int) -> str:
    return index_to_month(month_to_index(data_month) + offset)


def month_to_index(data_month: str) -> int:
    year_text, separator, month_text = data_month.partition("-")
    if not separator:
        raise ValueError(f"Invalid month: {data_month}")
    try:
        year = int(year_text)
        month = int(month_text)
    except ValueError as exc:
        raise ValueError(f"Invalid month: {data_month}") from exc
    if year < 1 or month < 1 or month > 12:
        raise ValueError(f"Invalid month: {data_month}")
    return year * 12 + month - 1


def index_to_month(index: int) -> str:
    year, month_index = divmod(index, 12)
    return f"{year:04d}-{month_index + 1:02d}"


def format_price_summary(label: str, value: float | None) -> str:
    if value is None:
        return f"{label} -"
    return f"{label} {value:.6f} {PRICE_UNIT_ZH}"


def format_signed_summary(label: str, value: float | None) -> str:
    if value is None:
        return f"{label} -"
    return f"{label} {value:+.6f} {PRICE_UNIT_ZH}"


def create_hover_annotations(entries: list[HoverArtist]) -> dict[Any, Any]:
    annotations = {}
    for entry in entries:
        axis = entry.artist.axes
        if axis in annotations:
            continue
        annotation = axis.annotate(
            "",
            xy=(0, 0),
            xytext=(10, 10),
            textcoords="offset points",
            bbox={"boxstyle": "round,pad=0.35", "fc": axis.get_facecolor(), "alpha": 0.92},
            arrowprops={"arrowstyle": "->", "color": "#666666"},
        )
        annotation.set_visible(False)
        annotations[axis] = annotation
    return annotations


def _purchasing_metric_params() -> dict[str, str]:
    return {
        "price": PURCHASING_PRICE,
        "operating": OPERATING_COST,
        "loss": LINE_LOSS_COST,
        "total": PURCHASING_TOTAL,
    }


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _csv_value(value: float | None) -> str | float:
    return "" if value is None else value
