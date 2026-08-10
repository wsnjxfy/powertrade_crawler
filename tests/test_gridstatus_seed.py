from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from powertrade_crawler.config import get_settings
from powertrade_crawler.gridstatus_seed import (
    GRIDSTATUS_SEED_RELATIVE_PATH,
    load_gridstatus_dataset_seed,
    seed_gridstatus_dataset_catalog_if_empty,
)
from powertrade_crawler.storage import get_engine, init_db


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = PROJECT_ROOT / GRIDSTATUS_SEED_RELATIVE_PATH


def test_gridstatus_seed_contains_complete_unique_catalog() -> None:
    records = load_gridstatus_dataset_seed(SEED_PATH)

    assert len(records) >= 500
    assert len({record.dataset_id for record in records}) == len(records)
    assert sum(bool(record.description_chinese) for record in records) >= 500
    assert any(record.dataset_id == "caiso_fuel_mix" for record in records)


def test_gridstatus_seed_imports_once_into_empty_database(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "seed.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    init_db()

    expected_count = len(load_gridstatus_dataset_seed(SEED_PATH))
    assert seed_gridstatus_dataset_catalog_if_empty(SEED_PATH) == expected_count
    assert seed_gridstatus_dataset_catalog_if_empty(SEED_PATH) == 0

    with get_engine().connect() as connection:
        actual_count = int(
            connection.execute(
                text("SELECT COUNT(*) FROM gridstatus_dataset_metadata")
            ).scalar_one()
        )
    assert actual_count == expected_count
