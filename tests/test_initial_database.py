from __future__ import annotations

import json
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INITIAL_DATABASE = PROJECT_ROOT / "resources" / "initial" / "powertrade.initial.db"
INITIAL_MANIFEST = (
    PROJECT_ROOT / "resources" / "initial" / "powertrade.initial.manifest.json"
)


def scalar(connection: sqlite3.Connection, query: str) -> int | str:
    row = connection.execute(query).fetchone()
    assert row is not None
    return row[0]


def test_initial_database_is_valid_and_matches_manifest() -> None:
    manifest = json.loads(INITIAL_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["contains_credentials"] is False
    assert manifest["database_bytes"] == INITIAL_DATABASE.stat().st_size

    with sqlite3.connect(INITIAL_DATABASE) as connection:
        assert scalar(connection, "PRAGMA integrity_check") == "ok"
        for table_name, expected_count in manifest["table_counts"].items():
            assert scalar(connection, f"SELECT COUNT(*) FROM {table_name}") == expected_count


def test_initial_database_covers_every_source_and_main_demo_page() -> None:
    with sqlite3.connect(INITIAL_DATABASE) as connection:
        assert scalar(connection, "SELECT COUNT(*) FROM market_records") >= 500
        assert scalar(connection, "SELECT COUNT(*) FROM entsoe_records") > 0
        assert scalar(connection, "SELECT COUNT(DISTINCT dataset) FROM elexon_records") >= 4
        assert scalar(connection, "SELECT COUNT(*) FROM gridstatus_records") == 1000
        assert scalar(connection, "SELECT COUNT(*) FROM gridstatus_dataset_metadata") >= 500
        assert scalar(connection, "SELECT COUNT(DISTINCT news_type) FROM gzpec_news_records") == 3
        assert (
            scalar(
                connection,
                "SELECT COUNT(DISTINCT area_code) FROM elecheck_clear_price_records",
            )
            >= 3
        )
        assert (
            scalar(
                connection,
                "SELECT COUNT(DISTINCT data_month) FROM elecheck_purchasing_records",
            )
            == 3
        )
        assert (
            scalar(
                connection,
                "SELECT COUNT(*) FROM elecheck_mechanism_electricity_price_records",
            )
            > 0
        )
        assert scalar(connection, "SELECT COUNT(*) FROM dashboard_daily_metrics") > 0


def test_initial_database_contains_no_user_state_or_scheduled_actions() -> None:
    empty_tables = (
        "scheduled_jobs",
        "scheduled_job_runs",
        "agent_sessions",
        "agent_messages",
        "agent_runs",
        "agent_tool_calls",
        "market_agent_sessions",
        "market_agent_messages",
        "market_agent_runs",
        "market_agent_tool_calls",
    )
    with sqlite3.connect(INITIAL_DATABASE) as connection:
        for table_name in empty_tables:
            assert scalar(connection, f"SELECT COUNT(*) FROM {table_name}") == 0


def test_initial_database_raw_payloads_do_not_contain_credentials() -> None:
    raw_tables = (
        ("market_records", "raw_json"),
        ("entsoe_records", "raw_json"),
        ("elexon_records", "raw_json"),
        ("gridstatus_records", "raw_json"),
        ("gridstatus_dataset_metadata", "raw_json"),
        ("elecheck_clear_price_records", "raw_json"),
        ("elecheck_purchasing_records", "raw_json"),
        ("elecheck_mechanism_electricity_price_records", "raw_json"),
    )
    credential_markers = (
        '"api_key"',
        '"authorization"',
        '"password"',
        '"securitytoken"',
        '"secret"',
    )
    with sqlite3.connect(INITIAL_DATABASE) as connection:
        for table_name, column_name in raw_tables:
            for marker in credential_markers:
                query = (
                    f"SELECT COUNT(*) FROM {table_name} "
                    f"WHERE LOWER({column_name}) LIKE '%{marker}%'"
                )
                assert scalar(connection, query) == 0
