import csv
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from powertrade_crawler.config import get_settings
from powertrade_crawler.credentials import clear_process_credentials, set_process_credential
from powertrade_crawler.gridstatus_downloader import (
    download_dataset_csv_adaptive,
    parse_gridstatus_time,
)


def make_payload(rows, cursor: str | None = None):
    return {
        "status_code": 200,
        "data": rows,
        "meta": {
            "hasNextPage": cursor is not None,
            "cursor": cursor,
        },
    }


def test_adaptive_downloader_pages_each_time_window(tmp_path: Path, monkeypatch):
    set_process_credential("gridstatus_api_key", "test-key")
    get_settings.cache_clear()

    calls = []

    def fake_fetch(url: str) -> dict:
        calls.append(url)
        params = parse_qs(urlparse(url).query, keep_blank_values=True)
        if params["start_time"] == ["2024-01-01T00:00:00Z"] and params.get("cursor") == [""]:
            return make_payload(
                [
                    {"interval_start_utc": "2024-01-01T00:00:00Z", "value": "1"},
                    {"interval_start_utc": "2024-01-01T00:30:00Z", "value": "2"},
                ],
                cursor="next-page",
            )
        if params["start_time"] == ["2024-01-01T00:00:00Z"] and params.get("cursor") == [
            "next-page"
        ]:
            return make_payload(
                [{"interval_start_utc": "2024-01-01T00:45:00Z", "value": "3"}],
            )
        return make_payload(
            [
                {"interval_start_utc": "2024-01-01T01:00:00Z", "value": "4"},
                {"interval_start_utc": "2024-01-01T01:30:00Z", "value": "5"},
            ]
        )

    output_path = tmp_path / "out.csv"
    result = download_dataset_csv_adaptive(
        metadata={
            "dataset_id": "demo_dataset",
            "earliest_available_time_utc": "2024-01-01T00:00:00Z",
            "latest_available_time_utc": "2024-01-01T02:00:00Z",
            "primary_key_columns_json": '["interval_start_utc"]',
        },
        output_path=output_path,
        limit=2,
        min_interval_seconds=0,
        batch_pause_seconds=0,
        window_seconds=3600,
        fetch_json=fake_fetch,
    )
    clear_process_credentials()

    with output_path.open(newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))

    assert result.requests_made == 3
    assert result.intervals_completed == 2
    assert result.rows_written == 5
    assert len(calls) == 3
    assert [row["value"] for row in rows] == ["1", "2", "3", "4", "5"]
    assert "page_size=2" in calls[0]
    assert "limit=" not in calls[0]


def test_parse_gridstatus_time_accepts_z_suffix():
    assert parse_gridstatus_time("2024-01-01T00:00:00Z").isoformat().startswith(
        "2024-01-01T00:00:00+00:00"
    )


def test_adaptive_downloader_uses_custom_download_time_range(tmp_path: Path, monkeypatch):
    set_process_credential("gridstatus_api_key", "test-key")
    get_settings.cache_clear()

    calls = []

    def fake_fetch(url: str) -> dict:
        calls.append(url)
        return make_payload([{"interval_start_utc": "2024-01-02T00:00:00Z", "value": "1"}])

    download_dataset_csv_adaptive(
        metadata={
            "dataset_id": "demo_dataset",
            "earliest_available_time_utc": "2024-01-01T00:00:00Z",
            "latest_available_time_utc": "2024-01-10T00:00:00Z",
            "download_start_time_utc": "2024-01-02T00:00:00Z",
            "download_end_time_utc": "2024-01-03T00:00:00Z",
            "primary_key_columns_json": '["interval_start_utc"]',
        },
        output_path=tmp_path / "custom.csv",
        min_interval_seconds=0,
        batch_pause_seconds=0,
        fetch_json=fake_fetch,
    )
    clear_process_credentials()

    assert "start_time=2024-01-02T00%3A00%3A00Z" in calls[0]
    assert "end_time=2024-01-03T00%3A00%3A00Z" in calls[0]


def test_adaptive_downloader_can_write_sqlite_database(tmp_path: Path, monkeypatch):
    set_process_credential("gridstatus_api_key", "test-key")
    get_settings.cache_clear()

    def fake_fetch(_url: str) -> dict:
        return make_payload(
            [
                {
                    "interval_start_utc": "2024-01-01T00:00:00Z",
                    "location": "A",
                    "value": "1",
                },
                {
                    "interval_start_utc": "2024-01-01T01:00:00Z",
                    "location": "B",
                    "value": "2",
                },
            ]
        )

    output_path = tmp_path / "dataset.db"
    result = download_dataset_csv_adaptive(
        metadata={
            "dataset_id": "demo_dataset",
            "earliest_available_time_utc": "2024-01-01T00:00:00Z",
            "latest_available_time_utc": "2024-01-01T01:00:00Z",
            "primary_key_columns_json": '["interval_start_utc", "location"]',
            "data_frequency": "1_HOUR",
        },
        output_path=output_path,
        min_interval_seconds=0,
        batch_pause_seconds=0,
        output_format="sqlite",
        fetch_json=fake_fetch,
    )
    clear_process_credentials()

    assert result.rows_written == 2
    with sqlite3.connect(output_path) as connection:
        rows = connection.execute(
            'SELECT interval_start_utc, location, value FROM gridstatus_rows ORDER BY location'
        ).fetchall()
        metadata_rows = dict(connection.execute("SELECT key, value FROM gridstatus_metadata"))

    assert rows == [
        ("2024-01-01T00:00:00Z", "A", "1"),
        ("2024-01-01T01:00:00Z", "B", "2"),
    ]
    assert metadata_rows["dataset_id"] == "demo_dataset"
