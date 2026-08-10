import csv
from datetime import UTC, datetime

import pytest

from powertrade_crawler.config import get_settings
from powertrade_crawler.storage import (
    GridStatusDatasetMetadataRow,
    export_gridstatus_translation_tasks,
    get_session,
    import_gridstatus_translations,
    init_db,
)


def test_export_gridstatus_translation_tasks_writes_expected_csv(tmp_path, monkeypatch):
    db_path = tmp_path / "powertrade.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()

    init_db()
    with get_session() as session:
        session.add(
            GridStatusDatasetMetadataRow(
                dataset_id="aeso_daily_average_pool_price",
                name="AESO Daily Average Pool Price",
                description="Average daily prices in AESO.",
                source="aeso",
                status="active",
                primary_key_columns_json="[]",
                all_columns_json="[]",
                raw_json="{}",
                collected_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        session.commit()

    output_path = tmp_path / "tasks.csv"
    row_count = export_gridstatus_translation_tasks(output_path)

    assert row_count == 1
    with output_path.open(newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))

    assert rows[0]["dataset_id"] == "aeso_daily_average_pool_price"
    assert rows[0]["description"] == "Average daily prices in AESO."
    assert "prompt" not in rows[0]
    assert rows[0]["description_chinese"] == ""
    assert rows[0]["translation_status"] == "pending"
    assert "updated_at" not in rows[0]


def test_import_gridstatus_translations_updates_database(tmp_path, monkeypatch):
    db_path = tmp_path / "powertrade.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()

    init_db()
    with get_session() as session:
        session.add(
            GridStatusDatasetMetadataRow(
                dataset_id="aeso_daily_average_pool_price",
                name="AESO Daily Average Pool Price",
                description="Average daily prices in AESO.",
                source="aeso",
                status="active",
                primary_key_columns_json="[]",
                all_columns_json="[]",
                raw_json="{}",
                collected_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        session.commit()

    input_path = tmp_path / "translated.csv"
    with input_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "id",
                "dataset_id",
                "description",
                "description_chinese",
                "translation_status",
                "translation_error",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "id": "1",
                "dataset_id": "aeso_daily_average_pool_price",
                "description": "Average daily prices in AESO.",
                "description_chinese": "AESO 的每日平均电价。",
                "translation_status": "done",
                "translation_error": "",
            }
        )

    stats = import_gridstatus_translations(input_path)

    assert stats["updated"] == 1
    with get_session() as session:
        row = session.query(GridStatusDatasetMetadataRow).filter_by(
            dataset_id="aeso_daily_average_pool_price"
        ).one()
        assert row.description_chinese == "AESO 的每日平均电价。"


def test_import_gridstatus_translations_requires_columns(tmp_path):
    input_path = tmp_path / "bad.csv"
    with input_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=["dataset_id"])
        writer.writeheader()
        writer.writerow({"dataset_id": "aeso_daily_average_pool_price"})

    with pytest.raises(ValueError):
        import_gridstatus_translations(input_path)
