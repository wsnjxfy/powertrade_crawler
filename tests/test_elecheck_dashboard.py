import csv
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from matplotlib.figure import Figure

from powertrade_crawler.elecheck_dashboard import (
    DAY_AHEAD_METRIC,
    REAL_TIME_METRIC,
    ElecheckAreaOption,
    ElecheckPriceDashboardRepository,
    draw_elecheck_price_figure,
    export_elecheck_intraday_csv,
    format_completeness,
    parse_time_to_minutes,
)


def prepare_dashboard_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "powertrade.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE elecheck_area_records (
                area_name TEXT NOT NULL,
                area_code TEXT NOT NULL UNIQUE
            );
            CREATE TABLE elecheck_clear_price_records (
                endpoint TEXT NOT NULL,
                area_code TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                time96 TEXT,
                metric TEXT NOT NULL,
                value REAL,
                unit TEXT NOT NULL
            );
            INSERT INTO elecheck_area_records(area_name, area_code)
            VALUES ('江西', '360000000000');
            """
        )
        rows = [
            ("detail", "2026-07-19", "2026-07-19", "00:15", DAY_AHEAD_METRIC, 90.0),
            ("detail", "2026-07-19", "2026-07-19", "00:30", DAY_AHEAD_METRIC, 100.0),
            ("detail", "2026-07-19", "2026-07-19", "24:00", DAY_AHEAD_METRIC, 110.0),
            ("detail", "2026-07-19", "2026-07-19", "00:05", REAL_TIME_METRIC, 80.0),
            ("detail", "2026-07-19", "2026-07-19", "00:10", REAL_TIME_METRIC, 85.0),
            ("detail", "2026-07-19", "2026-07-19", "00:15", REAL_TIME_METRIC, 95.0),
            ("detail", "2026-07-19", "2026-07-19", "00:20", REAL_TIME_METRIC, 97.0),
            ("detail", "2026-07-19", "2026-07-19", "00:30", REAL_TIME_METRIC, 105.0),
            ("detail", "2026-07-19", "2026-07-19", "24:00", REAL_TIME_METRIC, 115.0),
            ("detail", "2026-07-20", "2026-07-20", "00:30", DAY_AHEAD_METRIC, 100.0),
            ("detail", "2026-07-20", "2026-07-20", "24:00", DAY_AHEAD_METRIC, 200.0),
            ("detail", "2026-07-20", "2026-07-20", "00:05", REAL_TIME_METRIC, 110.0),
            ("detail", "2026-07-20", "2026-07-20", "00:30", REAL_TIME_METRIC, 120.0),
            ("detail", "2026-07-20", "2026-07-20", "24:00", REAL_TIME_METRIC, 180.0),
            ("detail", "2026-07-01", "2026-07-20", "12:00", DAY_AHEAD_METRIC, 999.0),
        ]
        connection.executemany(
            """
            INSERT INTO elecheck_clear_price_records(
                endpoint, area_code, start_date, end_date, time96, metric, value, unit
            ) VALUES (?, '360000000000', ?, ?, ?, ?, ?, 'CNY/MWh')
            """,
            rows,
        )
    return db_path


@pytest.mark.parametrize(
    ("time_label", "expected"),
    [
        ("00:00", 0),
        ("00:05", 5),
        ("00:15", 15),
        ("01:00", 60),
        ("23:30", 1410),
        ("24:00", 1440),
    ],
)
def test_parse_time_to_minutes_supports_elecheck_granularities(time_label, expected):
    assert parse_time_to_minutes(time_label) == expected


def test_parse_time_to_minutes_rejects_invalid_24_hour_value():
    with pytest.raises(ValueError):
        parse_time_to_minutes("24:15")


def test_repository_loads_only_daily_rows_and_builds_exact_time_spread(tmp_path: Path):
    repository = ElecheckPriceDashboardRepository(prepare_dashboard_db(tmp_path))
    areas = repository.list_areas()

    assert areas == [ElecheckAreaOption(area_code="360000000000", area_name="江西")]
    assert repository.list_dates("360000000000") == [
        date(2026, 7, 19),
        date(2026, 7, 20),
    ]

    data = repository.load_dashboard_data(
        area=areas[0],
        selected_date=date(2026, 7, 20),
    )

    assert [point.minute for point in data.day_ahead] == [30, 1440]
    assert [point.minute for point in data.real_time] == [5, 30, 1440]
    assert [(point.minute, point.value) for point in data.spread] == [
        (30, 20.0),
        (1440, -20.0),
    ]
    assert data.day_ahead_average == 150.0
    assert data.real_time_average == pytest.approx(136.6666667)
    assert data.expected_day_ahead_points == 3
    assert data.expected_real_time_points == 6
    assert format_completeness(len(data.real_time), data.expected_real_time_points) == (
        "3/6（未完结）"
    )
    assert {point.metric_date for point in data.daily_trend} == {
        date(2026, 7, 19),
        date(2026, 7, 20),
    }
    assert all(point.value != 999.0 for point in data.daily_trend)


def test_csv_and_png_exports_keep_missing_values_empty(tmp_path: Path):
    repository = ElecheckPriceDashboardRepository(prepare_dashboard_db(tmp_path))
    data = repository.load_dashboard_data(
        area=repository.list_areas()[0],
        selected_date=date(2026, 7, 20),
    )

    csv_path = tmp_path / "chart.csv"
    assert export_elecheck_intraday_csv(csv_path, data) == 3
    with csv_path.open(newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))

    assert rows[0] == {
        "日期": "2026-07-20",
        "地区": "江西",
        "时点": "00:05",
        "日前价格": "",
        "实时价格": "110.0",
        "价差": "",
        "单位": "CNY/MWh",
    }
    assert rows[-1]["时点"] == "24:00"
    assert rows[-1]["价差"] == "-20.0"

    day_ahead_path = tmp_path / "day-ahead.csv"
    assert export_elecheck_intraday_csv(
        day_ahead_path,
        data,
        series="day_ahead",
    ) == 2
    with day_ahead_path.open(newline="", encoding="utf-8-sig") as file:
        day_ahead_rows = list(csv.DictReader(file))
    assert list(day_ahead_rows[0]) == ["日期", "地区", "时点", "日前价格", "单位"]
    assert all("实时价格" not in row and "价差" not in row for row in day_ahead_rows)

    figure = Figure(figsize=(8, 6), layout="constrained")
    hover_series = draw_elecheck_price_figure(figure, data)
    png_path = tmp_path / "chart.png"
    figure.savefig(png_path)

    assert len(figure.axes) == 3
    assert {series.label for series in hover_series} == {
        "日前价格",
        "实时价格",
        "实时－日前",
        "日前日均价",
        "实时日均价",
    }
    assert png_path.stat().st_size > 0


def test_empty_repository_is_safe(tmp_path: Path):
    repository = ElecheckPriceDashboardRepository(tmp_path / "missing.db")

    assert repository.list_areas() == []
    assert repository.list_dates("360000000000") == []
