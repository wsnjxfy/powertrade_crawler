from __future__ import annotations

import csv
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import fmean
from tkinter import BOTH, LEFT, W, X, StringVar, messagebox, ttk
from tkinter.filedialog import asksaveasfilename
from typing import Any

from powertrade_crawler.config import get_settings


DAY_AHEAD_METRIC = "avg_day_ahead_price"
REAL_TIME_METRIC = "avg_real_time_price"
PRICE_UNIT = "CNY/MWh"
PRICE_UNIT_ZH = "元/MWh"
TREND_DAYS = 30


@dataclass(frozen=True)
class ElecheckAreaOption:
    area_code: str
    area_name: str


@dataclass(frozen=True)
class ElecheckPricePoint:
    time_label: str
    minute: int
    value: float


@dataclass(frozen=True)
class ElecheckSpreadPoint:
    time_label: str
    minute: int
    value: float


@dataclass(frozen=True)
class ElecheckDailyAverage:
    metric_date: date
    metric: str
    value: float
    point_count: int


@dataclass(frozen=True)
class ElecheckPriceDashboardData:
    area: ElecheckAreaOption
    selected_date: date
    day_ahead: tuple[ElecheckPricePoint, ...]
    real_time: tuple[ElecheckPricePoint, ...]
    spread: tuple[ElecheckSpreadPoint, ...]
    daily_trend: tuple[ElecheckDailyAverage, ...]
    expected_day_ahead_points: int | None
    expected_real_time_points: int | None

    @property
    def day_ahead_average(self) -> float | None:
        return average_price(self.day_ahead)

    @property
    def real_time_average(self) -> float | None:
        return average_price(self.real_time)

    @property
    def spread_average(self) -> float | None:
        if not self.spread:
            return None
        return fmean(point.value for point in self.spread)


@dataclass(frozen=True)
class HoverSeries:
    line: Any
    label: str
    value_unit: str
    x_kind: str


class ElecheckPriceDashboardRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def table_exists(self, table_name: str) -> bool:
        if not self.db_path.exists():
            return False
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
        return row is not None

    def list_areas(self) -> list[ElecheckAreaOption]:
        if not self.table_exists("elecheck_clear_price_records"):
            return []
        has_area_table = self.table_exists("elecheck_area_records")
        if has_area_table:
            query = """
                SELECT DISTINCT prices.area_code,
                       COALESCE(areas.area_name, prices.area_code) AS area_name
                FROM elecheck_clear_price_records AS prices
                LEFT JOIN elecheck_area_records AS areas
                  ON areas.area_code = prices.area_code
                WHERE prices.endpoint = 'detail'
                  AND prices.start_date = prices.end_date
                  AND prices.metric IN (:day_ahead, :real_time)
                ORDER BY area_name, prices.area_code
            """
        else:
            query = """
                SELECT DISTINCT area_code, area_code AS area_name
                FROM elecheck_clear_price_records
                WHERE endpoint = 'detail'
                  AND start_date = end_date
                  AND metric IN (:day_ahead, :real_time)
                ORDER BY area_code
            """
        with self.connect() as connection:
            rows = connection.execute(
                query,
                {"day_ahead": DAY_AHEAD_METRIC, "real_time": REAL_TIME_METRIC},
            ).fetchall()
        return [
            ElecheckAreaOption(
                area_code=str(row["area_code"]),
                area_name=str(row["area_name"]),
            )
            for row in rows
        ]

    def list_dates(self, area_code: str) -> list[date]:
        if not self.table_exists("elecheck_clear_price_records"):
            return []
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT start_date
                FROM elecheck_clear_price_records
                WHERE endpoint = 'detail'
                  AND area_code = :area_code
                  AND start_date = end_date
                  AND metric IN (:day_ahead, :real_time)
                ORDER BY start_date
                """,
                {
                    "area_code": area_code,
                    "day_ahead": DAY_AHEAD_METRIC,
                    "real_time": REAL_TIME_METRIC,
                },
            ).fetchall()
        return [date.fromisoformat(str(row["start_date"])[:10]) for row in rows]

    def load_dashboard_data(
        self,
        *,
        area: ElecheckAreaOption,
        selected_date: date,
    ) -> ElecheckPriceDashboardData:
        points = self.load_intraday_points(area.area_code, selected_date)
        day_ahead = points.get(DAY_AHEAD_METRIC, ())
        real_time = points.get(REAL_TIME_METRIC, ())
        return ElecheckPriceDashboardData(
            area=area,
            selected_date=selected_date,
            day_ahead=day_ahead,
            real_time=real_time,
            spread=build_spread_points(day_ahead, real_time),
            daily_trend=self.load_daily_trend(area.area_code, selected_date),
            expected_day_ahead_points=self.expected_point_count(
                area.area_code,
                DAY_AHEAD_METRIC,
                selected_date,
            ),
            expected_real_time_points=self.expected_point_count(
                area.area_code,
                REAL_TIME_METRIC,
                selected_date,
            ),
        )

    def load_intraday_points(
        self,
        area_code: str,
        selected_date: date,
    ) -> dict[str, tuple[ElecheckPricePoint, ...]]:
        if not self.table_exists("elecheck_clear_price_records"):
            return {}
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT metric, time96, value
                FROM elecheck_clear_price_records
                WHERE endpoint = 'detail'
                  AND area_code = :area_code
                  AND start_date = :selected_date
                  AND end_date = :selected_date
                  AND metric IN (:day_ahead, :real_time)
                  AND time96 IS NOT NULL
                  AND value IS NOT NULL
                ORDER BY metric, time96
                """,
                {
                    "area_code": area_code,
                    "selected_date": selected_date.isoformat(),
                    "day_ahead": DAY_AHEAD_METRIC,
                    "real_time": REAL_TIME_METRIC,
                },
            ).fetchall()

        grouped: dict[str, dict[int, ElecheckPricePoint]] = {
            DAY_AHEAD_METRIC: {},
            REAL_TIME_METRIC: {},
        }
        for row in rows:
            time_label = str(row["time96"])
            try:
                minute = parse_time_to_minutes(time_label)
            except ValueError:
                continue
            grouped[str(row["metric"])][minute] = ElecheckPricePoint(
                time_label=time_label,
                minute=minute,
                value=float(row["value"]),
            )
        return {
            metric: tuple(sorted(values.values(), key=lambda point: point.minute))
            for metric, values in grouped.items()
            if values
        }

    def load_daily_trend(
        self,
        area_code: str,
        selected_date: date,
    ) -> tuple[ElecheckDailyAverage, ...]:
        if not self.table_exists("elecheck_clear_price_records"):
            return ()
        start_date = selected_date - timedelta(days=TREND_DAYS - 1)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT start_date, metric, AVG(value) AS avg_value,
                       COUNT(DISTINCT time96) AS point_count
                FROM elecheck_clear_price_records
                WHERE endpoint = 'detail'
                  AND area_code = :area_code
                  AND start_date = end_date
                  AND start_date >= :start_date
                  AND start_date <= :end_date
                  AND metric IN (:day_ahead, :real_time)
                  AND time96 IS NOT NULL
                  AND value IS NOT NULL
                GROUP BY start_date, metric
                ORDER BY start_date, metric
                """,
                {
                    "area_code": area_code,
                    "start_date": start_date.isoformat(),
                    "end_date": selected_date.isoformat(),
                    "day_ahead": DAY_AHEAD_METRIC,
                    "real_time": REAL_TIME_METRIC,
                },
            ).fetchall()
        return tuple(
            ElecheckDailyAverage(
                metric_date=date.fromisoformat(str(row["start_date"])[:10]),
                metric=str(row["metric"]),
                value=float(row["avg_value"]),
                point_count=int(row["point_count"]),
            )
            for row in rows
        )

    def expected_point_count(
        self,
        area_code: str,
        metric: str,
        selected_date: date,
    ) -> int | None:
        if not self.table_exists("elecheck_clear_price_records"):
            return None
        start_date = selected_date - timedelta(days=TREND_DAYS)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT MAX(point_count) AS expected_count
                FROM (
                    SELECT start_date, COUNT(DISTINCT time96) AS point_count
                    FROM elecheck_clear_price_records
                    WHERE endpoint = 'detail'
                      AND area_code = :area_code
                      AND start_date = end_date
                      AND start_date >= :start_date
                      AND start_date < :selected_date
                      AND metric = :metric
                      AND time96 IS NOT NULL
                      AND value IS NOT NULL
                    GROUP BY start_date
                )
                """,
                {
                    "area_code": area_code,
                    "start_date": start_date.isoformat(),
                    "selected_date": selected_date.isoformat(),
                    "metric": metric,
                },
            ).fetchone()
        if row is None or row["expected_count"] is None:
            return None
        return int(row["expected_count"])


class ElecheckPriceDashboardApp:
    def __init__(self, root, db_path: Path) -> None:
        self.root = root
        self.repository = ElecheckPriceDashboardRepository(db_path)
        self.area_var = StringVar()
        self.date_var = StringVar()
        self.day_ahead_summary_var = StringVar(value="日前均价 -")
        self.real_time_summary_var = StringVar(value="实时均价 -")
        self.spread_summary_var = StringVar(value="平均价差 -")
        self.completeness_var = StringVar(value="数据完整度 -")
        self.status_var = StringVar(value="正在读取 Elecheck 现货价格数据...")
        self.areas_by_name: dict[str, ElecheckAreaOption] = {}
        self.available_dates: list[date] = []
        self.current_data: ElecheckPriceDashboardData | None = None
        self.figure = None
        self.canvas = None
        self.hover_series: list[HoverSeries] = []
        self.annotations: dict[Any, Any] = {}
        self.previous_button: ttk.Button | None = None
        self.next_button: ttk.Button | None = None
        self.build_ui()
        self.refresh_after_crawl(select_latest=True)

    def build_ui(self) -> None:
        toolbar = ttk.Frame(self.root, padding=8)
        toolbar.pack(fill=X)
        ttk.Label(toolbar, text="地区").pack(side=LEFT, padx=(0, 4))
        self.area_combo = ttk.Combobox(
            toolbar,
            textvariable=self.area_var,
            state="readonly",
            width=16,
        )
        self.area_combo.pack(side=LEFT, padx=(0, 10))
        self.area_combo.bind("<<ComboboxSelected>>", self.on_area_changed)

        ttk.Label(toolbar, text="日期").pack(side=LEFT, padx=(0, 4))
        self.date_combo = ttk.Combobox(
            toolbar,
            textvariable=self.date_var,
            state="readonly",
            width=12,
        )
        self.date_combo.pack(side=LEFT, padx=(0, 6))
        self.date_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_chart())
        self.previous_button = ttk.Button(toolbar, text="上一有数据日", command=self.show_previous_date)
        self.previous_button.pack(side=LEFT, padx=(0, 4))
        self.next_button = ttk.Button(toolbar, text="下一有数据日", command=self.show_next_date)
        self.next_button.pack(side=LEFT, padx=(0, 8))
        ttk.Button(toolbar, text="刷新", command=self.refresh_chart).pack(side=LEFT, padx=(0, 4))
        ttk.Button(toolbar, text="导出 CSV", command=self.export_csv).pack(side=LEFT, padx=(0, 4))
        ttk.Button(toolbar, text="导出 PNG", command=self.export_png).pack(side=LEFT)

        summary = ttk.Frame(self.root, padding=(8, 0, 8, 6))
        summary.pack(fill=X)
        for index, variable in enumerate(
            (
                self.day_ahead_summary_var,
                self.real_time_summary_var,
                self.spread_summary_var,
                self.completeness_var,
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

    def build_chart_canvas(self, parent) -> None:
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
        self.figure = Figure(figsize=(10, 6.6), dpi=100, layout="constrained")
        self.canvas = FigureCanvasTkAgg(self.figure, master=parent)
        self.canvas.get_tk_widget().pack(fill=BOTH, expand=True)
        self.canvas.mpl_connect("motion_notify_event", self.on_hover)
        self.canvas.mpl_connect("figure_leave_event", self.hide_annotations)

    def refresh_after_crawl(self, *, select_latest: bool = True) -> None:
        current_area_code = self.selected_area().area_code if self.selected_area() else None
        preferred_area_code = current_area_code or get_settings().elecheck_area_code
        areas = self.repository.list_areas()
        self.areas_by_name = {area.area_name: area for area in areas}
        self.area_combo["values"] = list(self.areas_by_name)
        if not areas:
            self.area_var.set("")
            self.date_var.set("")
            self.available_dates = []
            self.current_data = None
            self.show_empty("暂无每日 Elecheck 现货价格数据。")
            return

        selected = next(
            (area for area in areas if area.area_code == preferred_area_code),
            areas[0],
        )
        self.area_var.set(selected.area_name)
        self.load_dates(selected, select_latest=select_latest)

    def selected_area(self) -> ElecheckAreaOption | None:
        return self.areas_by_name.get(self.area_var.get())

    def load_dates(self, area: ElecheckAreaOption, *, select_latest: bool) -> None:
        previous_date = self.date_var.get()
        self.available_dates = self.repository.list_dates(area.area_code)
        self.date_combo["values"] = [value.isoformat() for value in reversed(self.available_dates)]
        if not self.available_dates:
            self.date_var.set("")
            self.current_data = None
            self.show_empty(f"{area.area_name} 暂无每日现货价格数据。")
            return
        if not select_latest and previous_date in self.date_combo["values"]:
            self.date_var.set(previous_date)
        else:
            self.date_var.set(self.available_dates[-1].isoformat())
        self.refresh_chart()

    def on_area_changed(self, _event) -> None:
        area = self.selected_area()
        if area is not None:
            self.load_dates(area, select_latest=True)

    def refresh_chart(self) -> None:
        area = self.selected_area()
        try:
            selected_date = date.fromisoformat(self.date_var.get())
        except ValueError:
            selected_date = None
        if area is None or selected_date is None:
            self.current_data = None
            self.show_empty("请选择有数据的地区和日期。")
            return
        self.current_data = self.repository.load_dashboard_data(
            area=area,
            selected_date=selected_date,
        )
        self.update_summaries(self.current_data)
        self.update_navigation_state(selected_date)
        self.draw_current_data()

    def show_previous_date(self) -> None:
        self.move_date(-1)

    def show_next_date(self) -> None:
        self.move_date(1)

    def move_date(self, offset: int) -> None:
        try:
            selected = date.fromisoformat(self.date_var.get())
            index = self.available_dates.index(selected)
        except (ValueError, IndexError):
            return
        target_index = index + offset
        if 0 <= target_index < len(self.available_dates):
            self.date_var.set(self.available_dates[target_index].isoformat())
            self.refresh_chart()

    def update_navigation_state(self, selected_date: date) -> None:
        try:
            index = self.available_dates.index(selected_date)
        except ValueError:
            index = -1
        if self.previous_button is not None:
            self.previous_button.configure(state="normal" if index > 0 else "disabled")
        if self.next_button is not None:
            self.next_button.configure(
                state="normal" if 0 <= index < len(self.available_dates) - 1 else "disabled"
            )

    def update_summaries(self, data: ElecheckPriceDashboardData) -> None:
        self.day_ahead_summary_var.set(
            format_average_summary("日前均价", data.day_ahead_average)
        )
        self.real_time_summary_var.set(
            format_average_summary("实时均价", data.real_time_average)
        )
        self.spread_summary_var.set(format_average_summary("平均价差", data.spread_average))
        day_text = format_completeness(
            len(data.day_ahead),
            data.expected_day_ahead_points,
        )
        real_text = format_completeness(
            len(data.real_time),
            data.expected_real_time_points,
        )
        self.completeness_var.set(f"数据完整度 日前 {day_text} | 实时 {real_text}")

        status_parts = [f"{data.area.area_name} {data.selected_date.isoformat()}"]
        if not data.day_ahead:
            status_parts.append("暂无日前价格")
        if not data.real_time:
            status_parts.append("暂无实时价格")
        elif is_partial(len(data.real_time), data.expected_real_time_points):
            status_parts.append("实时数据未完结，仅展示已发布时点")
        if data.day_ahead and data.real_time and not data.spread:
            status_parts.append("两类价格没有共同时间点，未计算价差")
        self.status_var.set(" | ".join(status_parts))

    def draw_current_data(self) -> None:
        if self.figure is None or self.canvas is None or self.current_data is None:
            return
        self.hover_series = draw_elecheck_price_figure(self.figure, self.current_data)
        self.annotations = create_hover_annotations(self.hover_series)
        self.canvas.draw()

    def show_empty(self, message: str) -> None:
        self.day_ahead_summary_var.set("日前均价 -")
        self.real_time_summary_var.set("实时均价 -")
        self.spread_summary_var.set("平均价差 -")
        self.completeness_var.set("数据完整度 -")
        self.status_var.set(message)
        if self.figure is None or self.canvas is None:
            return
        self.figure.clear()
        axis = self.figure.add_subplot(111)
        axis.text(0.5, 0.5, message, ha="center", va="center", transform=axis.transAxes)
        axis.set_axis_off()
        self.hover_series = []
        self.annotations = {}
        self.canvas.draw()

    def on_hover(self, event) -> None:
        if self.canvas is None:
            return
        nearest: tuple[float, HoverSeries, float, float] | None = None
        for series in self.hover_series:
            if event.inaxes is not series.line.axes:
                continue
            for x_value, y_value in series.line.get_xydata():
                if not math.isfinite(float(y_value)):
                    continue
                x_pixel, y_pixel = series.line.axes.transData.transform((x_value, y_value))
                distance = (x_pixel - event.x) ** 2 + (y_pixel - event.y) ** 2
                if nearest is None or distance < nearest[0]:
                    nearest = (distance, series, float(x_value), float(y_value))

        changed = False
        for annotation in self.annotations.values():
            if annotation.get_visible():
                annotation.set_visible(False)
                changed = True
        if nearest is not None and nearest[0] <= 100:
            _, series, x_value, y_value = nearest
            annotation = self.annotations[series.line.axes]
            annotation.xy = (x_value, y_value)
            annotation.set_text(
                f"{series.label}\n{format_hover_x(x_value, series.x_kind)}\n"
                f"{y_value:.2f} {series.value_unit}"
            )
            annotation.set_visible(True)
            changed = True
        if changed:
            self.canvas.draw_idle()

    def hide_annotations(self, _event=None) -> None:
        changed = False
        for annotation in self.annotations.values():
            if annotation.get_visible():
                annotation.set_visible(False)
                changed = True
        if changed and self.canvas is not None:
            self.canvas.draw_idle()

    def export_csv(self) -> None:
        if self.current_data is None or not (
            self.current_data.day_ahead or self.current_data.real_time
        ):
            messagebox.showinfo("没有可导出数据", "当前地区和日期没有现货价格数据。")
            return
        output_path = asksaveasfilename(
            title="导出 Elecheck 现货价格图表数据",
            initialfile=(
                f"elecheck_{self.current_data.area.area_name}_"
                f"{self.current_data.selected_date.isoformat()}.csv"
            ),
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not output_path:
            return
        row_count = export_elecheck_intraday_csv(Path(output_path), self.current_data)
        self.status_var.set(f"已导出 {row_count} 个时点到 {output_path}")

    def export_png(self) -> None:
        if self.figure is None or self.current_data is None or not (
            self.current_data.day_ahead or self.current_data.real_time
        ):
            messagebox.showinfo("没有可导出图表", "当前地区和日期没有现货价格图表。")
            return
        output_path = asksaveasfilename(
            title="导出 Elecheck 现货价格图表",
            initialfile=(
                f"elecheck_{self.current_data.area.area_name}_"
                f"{self.current_data.selected_date.isoformat()}.png"
            ),
            defaultextension=".png",
            filetypes=[("PNG image", "*.png"), ("All files", "*.*")],
        )
        if not output_path:
            return
        self.figure.savefig(output_path, dpi=160, bbox_inches="tight")
        self.status_var.set(f"图表已导出到 {output_path}")


def parse_time_to_minutes(value: str) -> int:
    hour_text, separator, minute_text = value.strip().partition(":")
    if not separator:
        raise ValueError(f"Invalid Elecheck time label: {value}")
    try:
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError as exc:
        raise ValueError(f"Invalid Elecheck time label: {value}") from exc
    if hour < 0 or hour > 24 or minute < 0 or minute > 59 or (hour == 24 and minute != 0):
        raise ValueError(f"Invalid Elecheck time label: {value}")
    return hour * 60 + minute


def format_minutes(value: float) -> str:
    total_minutes = int(round(value))
    hour, minute = divmod(total_minutes, 60)
    return f"{hour:02d}:{minute:02d}"


def average_price(points: tuple[ElecheckPricePoint, ...]) -> float | None:
    if not points:
        return None
    return fmean(point.value for point in points)


def build_spread_points(
    day_ahead: tuple[ElecheckPricePoint, ...],
    real_time: tuple[ElecheckPricePoint, ...],
) -> tuple[ElecheckSpreadPoint, ...]:
    day_values = {point.minute: point.value for point in day_ahead}
    real_values = {point.minute: point.value for point in real_time}
    return tuple(
        ElecheckSpreadPoint(
            time_label=format_minutes(minute),
            minute=minute,
            value=real_values[minute] - day_values[minute],
        )
        for minute in sorted(day_values.keys() & real_values.keys())
    )


def format_average_summary(label: str, value: float | None) -> str:
    if value is None:
        return f"{label} -"
    return f"{label} {value:.2f} {PRICE_UNIT_ZH}"


def format_completeness(actual: int, expected: int | None) -> str:
    if actual == 0:
        return "暂无"
    if expected is None:
        return f"已采集 {actual} 点"
    suffix = "（未完结）" if actual < expected else ""
    return f"{actual}/{expected}{suffix}"


def is_partial(actual: int, expected: int | None) -> bool:
    return expected is not None and 0 < actual < expected


def draw_elecheck_price_figure(
    figure,
    data: ElecheckPriceDashboardData,
) -> list[HoverSeries]:
    import matplotlib.dates as mdates

    configure_matplotlib_fonts()
    figure.clear()
    grid = figure.add_gridspec(3, 1, height_ratios=(3.0, 1.35, 2.0))
    price_axis = figure.add_subplot(grid[0])
    spread_axis = figure.add_subplot(grid[1], sharex=price_axis)
    trend_axis = figure.add_subplot(grid[2])
    hover_series: list[HoverSeries] = []

    price_lines = []
    for points, label, color in (
        (data.day_ahead, "日前价格", "#1f77b4"),
        (data.real_time, "实时价格", "#ff7f0e"),
    ):
        if not points:
            continue
        marker = "o" if len(points) <= 48 else None
        (line,) = price_axis.plot(
            [point.minute for point in points],
            [point.value for point in points],
            color=color,
            linewidth=1.6,
            marker=marker,
            markersize=3,
            label=label,
        )
        price_lines.append(line)
        hover_series.append(HoverSeries(line, label, PRICE_UNIT_ZH, "time"))
    price_axis.set_title(f"{data.area.area_name} {data.selected_date.isoformat()} 日内现货价格")
    price_axis.set_ylabel(PRICE_UNIT_ZH)
    price_axis.grid(True, alpha=0.2)
    if price_lines:
        price_axis.legend(loc="best")
    else:
        price_axis.text(
            0.5,
            0.5,
            "当前日期没有日前或实时价格数据",
            ha="center",
            va="center",
            transform=price_axis.transAxes,
        )

    if data.spread:
        x_values = [point.minute for point in data.spread]
        y_values = [point.value for point in data.spread]
        (spread_line,) = spread_axis.plot(
            x_values,
            y_values,
            color="#2f6f4e",
            linewidth=1.2,
            label="实时－日前",
        )
        spread_axis.fill_between(
            x_values,
            0,
            y_values,
            where=[value >= 0 for value in y_values],
            color="#d95f02",
            alpha=0.22,
            interpolate=True,
        )
        spread_axis.fill_between(
            x_values,
            0,
            y_values,
            where=[value < 0 for value in y_values],
            color="#1b78a5",
            alpha=0.22,
            interpolate=True,
        )
        hover_series.append(HoverSeries(spread_line, "实时－日前", PRICE_UNIT_ZH, "time"))
    else:
        spread_axis.set_yticks([])
        spread_axis.text(
            0.5,
            0.5,
            "没有可计算价差的共同时间点",
            ha="center",
            va="center",
            transform=spread_axis.transAxes,
        )
    spread_axis.axhline(0, color="#666666", linewidth=0.8)
    spread_axis.set_ylabel("价差")
    spread_axis.set_xlabel("时点")
    spread_axis.grid(True, alpha=0.18)
    time_ticks = list(range(0, 24 * 60 + 1, 120))
    spread_axis.set_xlim(0, 24 * 60)
    spread_axis.set_xticks(time_ticks)
    spread_axis.set_xticklabels([format_minutes(value) for value in time_ticks])
    price_axis.tick_params(axis="x", labelbottom=False)

    trend_start = data.selected_date - timedelta(days=TREND_DAYS - 1)
    trend_dates = [trend_start + timedelta(days=offset) for offset in range(TREND_DAYS)]
    trend_by_metric = {
        DAY_AHEAD_METRIC: {},
        REAL_TIME_METRIC: {},
    }
    for point in data.daily_trend:
        trend_by_metric[point.metric][point.metric_date] = point.value
    trend_lines = []
    for metric, label, color in (
        (DAY_AHEAD_METRIC, "日前日均价", "#1f77b4"),
        (REAL_TIME_METRIC, "实时日均价", "#ff7f0e"),
    ):
        values = [trend_by_metric[metric].get(metric_date, math.nan) for metric_date in trend_dates]
        if all(math.isnan(value) for value in values):
            continue
        (line,) = trend_axis.plot(
            trend_dates,
            values,
            color=color,
            linewidth=1.5,
            marker="o",
            markersize=3,
            label=label,
        )
        trend_lines.append(line)
        hover_series.append(HoverSeries(line, label, PRICE_UNIT_ZH, "date"))
    trend_axis.set_title("最近 30 个自然日日均价趋势")
    trend_axis.set_ylabel(PRICE_UNIT_ZH)
    trend_axis.set_xlabel("日期")
    trend_axis.grid(True, alpha=0.2)
    trend_axis.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
    trend_axis.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    if trend_lines:
        trend_axis.legend(loc="best")
    else:
        trend_axis.text(
            0.5,
            0.5,
            "最近 30 日没有每日价格数据",
            ha="center",
            va="center",
            transform=trend_axis.transAxes,
        )
    return hover_series


def configure_matplotlib_fonts() -> None:
    from matplotlib import rcParams

    rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "SimHei",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]
    rcParams["axes.unicode_minus"] = False


def create_hover_annotations(series_rows: list[HoverSeries]) -> dict[Any, Any]:
    annotations = {}
    for series in series_rows:
        axis = series.line.axes
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


def format_hover_x(value: float, kind: str) -> str:
    if kind == "time":
        return format_minutes(value)
    if kind == "date":
        import matplotlib.dates as mdates

        return mdates.num2date(value).date().isoformat()
    return str(value)


def export_elecheck_intraday_csv(
    output_path: Path,
    data: ElecheckPriceDashboardData,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    day_values = {point.minute: point.value for point in data.day_ahead}
    real_values = {point.minute: point.value for point in data.real_time}
    minutes = sorted(day_values.keys() | real_values.keys())
    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["日期", "地区", "时点", "日前价格", "实时价格", "价差", "单位"],
        )
        writer.writeheader()
        for minute in minutes:
            day_value = day_values.get(minute)
            real_value = real_values.get(minute)
            writer.writerow(
                {
                    "日期": data.selected_date.isoformat(),
                    "地区": data.area.area_name,
                    "时点": format_minutes(minute),
                    "日前价格": "" if day_value is None else day_value,
                    "实时价格": "" if real_value is None else real_value,
                    "价差": (
                        ""
                        if day_value is None or real_value is None
                        else real_value - day_value
                    ),
                    "单位": PRICE_UNIT,
                }
            )
    return len(minutes)
