import csv
import sqlite3
from pathlib import Path

import pytest
from matplotlib.figure import Figure

from powertrade_crawler.config import get_settings
from powertrade_crawler.elecheck_business_dashboard import (
    LINE_LOSS_COST,
    OPERATING_COST,
    PURCHASING_PRICE,
    PURCHASING_TOTAL,
    ElecheckMechanismDashboardRepository,
    ElecheckPurchasingDashboardRepository,
    draw_mechanism_figure,
    draw_purchasing_figure,
    export_mechanism_dashboard_csv,
    export_purchasing_dashboard_csv,
    iter_months,
    shift_month,
)


def prepare_business_dashboard_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "powertrade.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE elecheck_area_records (
                area_name TEXT NOT NULL,
                area_code TEXT NOT NULL UNIQUE
            );
            CREATE TABLE elecheck_purchasing_records (
                endpoint TEXT NOT NULL,
                data_kind TEXT NOT NULL,
                data_month TEXT NOT NULL,
                province_name TEXT,
                metric TEXT NOT NULL,
                value REAL,
                unit TEXT NOT NULL
            );
            CREATE TABLE elecheck_mechanism_electricity_price_records (
                region_name TEXT NOT NULL,
                category TEXT NOT NULL,
                price REAL,
                clear_price REAL,
                collected_at TEXT,
                unit TEXT NOT NULL
            );
            INSERT INTO elecheck_area_records(area_name, area_code)
            VALUES ('江苏', '320000000000');
            """
        )
        purchasing_rows = [
            ("national_table", "2026-01", "江苏", PURCHASING_PRICE, 0.30),
            ("national_table", "2026-01", "江苏", OPERATING_COST, -0.01),
            ("national_table", "2026-01", "江苏", LINE_LOSS_COST, 0.02),
            ("national_table", "2026-01", "江苏", PURCHASING_TOTAL, 0.31),
            ("national_table", "2026-03", "江苏", PURCHASING_PRICE, 0.30),
            ("national_table", "2026-03", "江苏", OPERATING_COST, 0.03),
            ("national_table", "2026-03", "江苏", LINE_LOSS_COST, 0.02),
            ("national_table", "2026-03", "江苏", PURCHASING_TOTAL, 0.35),
            ("national_top", "2026-03", "江苏", PURCHASING_TOTAL, 999.0),
            ("province_summary", "2026-03", "江苏", PURCHASING_TOTAL, 888.0),
        ]
        purchasing_rows.extend(
            (
                "national_table",
                "2026-03",
                f"测试省{index:02d}",
                PURCHASING_TOTAL,
                0.10 + index * 0.02,
            )
            for index in range(1, 25)
        )
        connection.executemany(
            """
            INSERT INTO elecheck_purchasing_records(
                endpoint, data_kind, data_month, province_name, metric, value, unit
            ) VALUES ('list', ?, ?, ?, ?, ?, 'CNY/kWh')
            """,
            purchasing_rows,
        )

        mechanism_rows = []
        for index in range(1, 25):
            mechanism_rows.append(
                (
                    f"测试省{index:02d}",
                    "光伏",
                    0.40,
                    0.20 + index * 0.01,
                    "2026-06-16 12:00:00",
                )
            )
        mechanism_rows.extend(
            [
                ("江苏", "光伏", 0.40, 0.35, "2026-06-16 12:00:00"),
                ("江苏", "风电-陆上", 0.40, None, "2026-06-16 12:00:00"),
                ("缺失省", "光伏", 0.40, None, "2026-06-16 12:00:00"),
            ]
        )
        connection.executemany(
            """
            INSERT INTO elecheck_mechanism_electricity_price_records(
                region_name, category, price, clear_price, collected_at, unit
            ) VALUES (?, ?, ?, ?, ?, 'CNY/kWh')
            """,
            mechanism_rows,
        )
    return db_path


def test_month_helpers_keep_calendar_order_and_cross_year_boundaries():
    assert shift_month("2026-01", -1) == "2025-12"
    assert shift_month("2025-12", 1) == "2026-01"
    assert iter_months("2025-11", "2026-02") == (
        "2025-11",
        "2025-12",
        "2026-01",
        "2026-02",
    )


def test_purchasing_repository_filters_kinds_and_preserves_missing_months(tmp_path: Path):
    repository = ElecheckPurchasingDashboardRepository(
        prepare_business_dashboard_db(tmp_path)
    )

    data = repository.load_dashboard_data(
        province_name="江苏",
        end_month="2026-03",
        window_label="全部月份",
    )

    assert [point.data_month for point in data.timeline] == [
        "2026-01",
        "2026-02",
        "2026-03",
    ]
    assert data.timeline[1].total is None
    assert data.current_total == pytest.approx(0.35)
    assert data.previous_total is None
    assert data.month_over_month is None
    assert data.covered_months == 2
    assert max(row.total for row in data.ranking) < 1
    assert len(data.ranking) == 25
    assert len(data.displayed_ranking) == 21
    assert sum(row.province_name == "江苏" for row in data.displayed_ranking) == 1

    recent = repository.load_dashboard_data(
        province_name="江苏",
        end_month="2026-03",
        window_label="近 24 个月",
    )
    assert len(recent.timeline) == 24
    assert recent.timeline[0].data_month == "2024-04"


def test_purchasing_preferred_area_uses_configured_area_name(
    tmp_path: Path,
    monkeypatch,
):
    repository = ElecheckPurchasingDashboardRepository(
        prepare_business_dashboard_db(tmp_path)
    )
    monkeypatch.setenv("ELECHECK_AREA_CODE", "320000000000")
    get_settings.cache_clear()
    try:
        assert repository.preferred_area_name(repository.list_provinces()) == "江苏"
    finally:
        get_settings.cache_clear()


def test_purchasing_csv_and_figure_keep_missing_values_and_negative_costs(tmp_path: Path):
    repository = ElecheckPurchasingDashboardRepository(
        prepare_business_dashboard_db(tmp_path)
    )
    data = repository.load_dashboard_data(
        province_name="江苏",
        end_month="2026-03",
        window_label="全部月份",
    )

    csv_path = tmp_path / "purchasing.csv"
    row_count = export_purchasing_dashboard_csv(csv_path, data)
    with csv_path.open(newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))

    assert row_count == len(data.timeline) + len(data.displayed_ranking)
    missing = next(row for row in rows if row["月份"] == "2026-02")
    assert missing["合计价"] == ""
    january = next(row for row in rows if row["月份"] == "2026-01")
    assert january["系统运行费折价"] == "-0.01"
    assert january["单位"] == "CNY/kWh"

    figure = Figure(figsize=(10, 7), layout="constrained")
    hover_artists = draw_purchasing_figure(figure, data)
    png_path = tmp_path / "purchasing.png"
    figure.savefig(png_path)
    assert len(figure.axes) == 2
    assert hover_artists
    assert png_path.stat().st_size > 0


def test_mechanism_repository_excludes_missing_clear_price_from_ranking(tmp_path: Path):
    repository = ElecheckMechanismDashboardRepository(
        prepare_business_dashboard_db(tmp_path)
    )

    data = repository.load_dashboard_data(region_name="江苏", category="光伏")

    assert data.selected is not None
    assert data.selected.gap == pytest.approx(-0.05)
    assert data.selected.relative_gap == pytest.approx(-12.5)
    assert len(data.category_rows) == 26
    assert len(data.displayed_ranking) == 21
    assert all(row.region_name != "缺失省" for row in data.displayed_ranking)
    assert sum(row.region_name == "江苏" for row in data.displayed_ranking) == 1
    assert [row.category for row in data.region_rows] == ["光伏", "风电-陆上"]


def test_mechanism_csv_and_figure_keep_missing_values_empty(tmp_path: Path):
    repository = ElecheckMechanismDashboardRepository(
        prepare_business_dashboard_db(tmp_path)
    )
    data = repository.load_dashboard_data(region_name="江苏", category="光伏")

    csv_path = tmp_path / "mechanism.csv"
    row_count = export_mechanism_dashboard_csv(csv_path, data)
    with csv_path.open(newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))

    assert row_count == len(data.displayed_ranking) + len(data.region_rows)
    missing = next(row for row in rows if row["电源类型"] == "风电-陆上")
    assert missing["26年增量机制电价"] == ""
    assert missing["差额"] == ""
    assert missing["单位"] == "CNY/kWh"

    figure = Figure(figsize=(10, 7), layout="constrained")
    hover_artists = draw_mechanism_figure(figure, data)
    png_path = tmp_path / "mechanism.png"
    figure.savefig(png_path)
    assert len(figure.axes) == 2
    assert hover_artists
    assert png_path.stat().st_size > 0


def test_empty_business_dashboard_repositories_are_safe(tmp_path: Path):
    missing_path = tmp_path / "missing.db"

    assert ElecheckPurchasingDashboardRepository(missing_path).list_provinces() == []
    assert ElecheckMechanismDashboardRepository(missing_path).list_regions() == []
