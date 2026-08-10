from datetime import datetime
from pathlib import Path

from powertrade_crawler.config import get_settings
from powertrade_crawler.metrics import (
    dashboard_label,
    list_dashboard_metric_rows,
    list_dashboard_overview,
    rebuild_dashboard_daily_metrics,
)
from powertrade_crawler.models import EntsoeRecord, GridStatusRecord
from powertrade_crawler.storage import (
    get_session,
    init_db,
    upsert_entsoe_records,
    upsert_gridstatus_records,
)


def test_dashboard_label_keeps_summary_metrics_as_separate_series():
    row = {
        "source": "Elecheck",
        "dataset": "elecheck_clear_price",
        "region": "320000000000",
        "dimension": "statistics",
        "metric_name": "day_ahead_avg_price",
    }

    assert dashboard_label(row).endswith("| day_ahead_avg_price")


def prepare_db(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "powertrade.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    init_db()
    return db_path


def test_dashboard_metrics_rebuilds_daily_entsoe_stats(tmp_path: Path, monkeypatch):
    prepare_db(tmp_path, monkeypatch)
    records = [
        EntsoeRecord(
            dataset="entsoe_actual_total_load",
            category="load",
            title_en="Actual Total Load",
            title_zh="实际总负荷",
            row_key="load-1",
            area="DE-LU",
            interval_start_utc="2026-06-01T00:00Z",
            interval_end_utc="2026-06-01T01:00Z",
            value=10,
            value_field="quantity",
            unit="MAW",
        ),
        EntsoeRecord(
            dataset="entsoe_actual_total_load",
            category="load",
            title_en="Actual Total Load",
            title_zh="实际总负荷",
            row_key="load-2",
            area="DE-LU",
            interval_start_utc="2026-06-01T01:00Z",
            interval_end_utc="2026-06-01T02:00Z",
            value=20,
            value_field="quantity",
            unit="MAW",
        ),
    ]
    upsert_entsoe_records(records)

    count = rebuild_dashboard_daily_metrics(
        datetime(2026, 6, 1).date(),
        datetime(2026, 6, 2).date(),
    )

    assert count == 1
    rows = list_dashboard_metric_rows(
        topic="load",
        start_date=datetime(2026, 6, 1).date(),
        end_date=datetime(2026, 6, 2).date(),
    )
    assert len(rows) == 1
    assert rows[0]["source"] == "ENTSO-E"
    assert rows[0]["dataset"] == "entsoe_actual_total_load"
    assert rows[0]["record_count"] == 2
    assert rows[0]["avg_value"] == 15
    assert rows[0]["min_value"] == 10
    assert rows[0]["max_value"] == 20
    assert rows[0]["spread_value"] == 10


def test_dashboard_metrics_extracts_gridstatus_numeric_raw_fields(tmp_path: Path, monkeypatch):
    prepare_db(tmp_path, monkeypatch)
    upsert_gridstatus_records(
        [
            GridStatusRecord(
                request_name="gridstatus_ercot_load",
                request_type="dataset_query",
                dataset="ercot_load",
                location="ERCOT",
                row_key="ercot-1",
                interval_start_utc="2026-06-01T00:00:00Z",
                raw={"load": 45000, "interval_start_utc": "2026-06-01T00:00:00Z"},
            )
        ]
    )

    count = rebuild_dashboard_daily_metrics(
        datetime(2026, 6, 1).date(),
        datetime(2026, 6, 2).date(),
    )

    assert count == 1
    rows = list_dashboard_metric_rows(
        topic="load",
        start_date=datetime(2026, 6, 1).date(),
        end_date=datetime(2026, 6, 2).date(),
    )
    assert rows[0]["source"] == "GridStatus"
    assert rows[0]["metric_name"] == "load"
    assert rows[0]["avg_value"] == 45000


def test_dashboard_overview_includes_empty_sources(tmp_path: Path, monkeypatch):
    prepare_db(tmp_path, monkeypatch)

    overview = list_dashboard_overview()

    sources = {row["source"] for row in overview}
    assert "ENTSO-E" in sources
    assert "Elexon" in sources
    assert "GZPEC 新闻" in sources
    with get_session() as session:
        assert session is not None
