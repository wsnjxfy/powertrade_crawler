from datetime import datetime, timezone

from powertrade_crawler.gui import (
    full_download_range,
    gridstatus_dataset_columns,
    suggested_download_range,
    suggested_gridstatus_filter,
)


NOW = datetime(2026, 7, 20, 8, 30, tzinfo=timezone.utc)


def test_suggested_download_range_uses_runtime_current_utc_not_stale_metadata():
    start, end = suggested_download_range(
        {
            "status": "active",
            "data_frequency": "1_HOUR",
            "earliest_available_time_utc": "2020-01-01T00:00:00Z",
            "latest_available_time_utc": "2026-05-27T00:00:00Z",
        },
        now=NOW,
    )

    assert start == "2026-07-17T08:30:00Z"
    assert end == "2026-07-20T08:30:00Z"


def test_full_download_range_uses_runtime_current_utc_for_active_dataset():
    start, end = full_download_range(
        {
            "status": "active",
            "earliest_available_time_utc": "2020-01-01T00:00:00Z",
            "latest_available_time_utc": "2026-05-27T00:00:00Z",
        },
        now=NOW,
    )

    assert start == "2020-01-01T00:00:00Z"
    assert end == "2026-07-20T08:30:00Z"


def test_full_download_range_preserves_snapshot_end_for_inactive_dataset():
    start, end = full_download_range(
        {
            "status": "inactive",
            "earliest_available_time_utc": "2020-01-01T00:00:00Z",
            "latest_available_time_utc": "2024-06-01T00:00:00Z",
        },
        now=NOW,
    )

    assert start == "2020-01-01T00:00:00Z"
    assert end == "2024-06-01T00:00:00Z"


def test_gridstatus_download_filter_uses_dataset_columns_and_ercot_spp_default():
    metadata = {
        "dataset_id": "ercot_spp_day_ahead_hourly",
        "all_columns_json": (
            '[{"name": "interval_start_utc"}, {"name": "location_type"}, '
            '{"name": "spp"}]'
        ),
    }

    assert gridstatus_dataset_columns(metadata) == [
        "interval_start_utc",
        "location_type",
        "spp",
    ]
    assert suggested_gridstatus_filter(metadata) == ("location_type", "Load Zone")


def test_gridstatus_download_filter_is_blank_for_other_datasets():
    assert suggested_gridstatus_filter(
        {
            "dataset_id": "ercot_load",
            "all_columns_json": '[{"name": "location_type"}]',
        }
    ) == ("", "")
