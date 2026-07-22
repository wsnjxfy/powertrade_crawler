import sqlite3
from pathlib import Path

from sqlalchemy import inspect

from powertrade_crawler.config import get_settings
from powertrade_crawler.gui import prepare_gui_database
from powertrade_crawler.storage import get_engine


def test_prepare_gui_database_updates_existing_sqlite_schema(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "powertrade.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE legacy_marker (id INTEGER PRIMARY KEY)")

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()

    resolved_path = prepare_gui_database()

    assert resolved_path == db_path.resolve()
    tables = set(inspect(get_engine()).get_table_names())
    assert "legacy_marker" in tables
    assert "dashboard_daily_metrics" in tables
    assert "scheduled_jobs" in tables
    assert "scheduled_job_runs" in tables
    with sqlite3.connect(db_path) as connection:
        indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(elecheck_clear_price_records)")
        }
    assert "ix_elecheck_clear_price_chart_lookup" in indexes
