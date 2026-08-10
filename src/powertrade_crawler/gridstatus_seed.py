from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import text

from powertrade_crawler.models import GridStatusDatasetMetadataRecord
from powertrade_crawler.storage import (
    get_engine,
    upsert_gridstatus_dataset_metadata_records,
)

GRIDSTATUS_SEED_SCHEMA_VERSION = 1
GRIDSTATUS_SEED_RELATIVE_PATH = Path("configs/gridstatus/datasets.initial.json")


def load_gridstatus_dataset_seed(
    seed_path: Path,
) -> list[GridStatusDatasetMetadataRecord]:
    try:
        payload: Any = json.loads(seed_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"GridStatus 初始数据集目录文件不存在：{seed_path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"GridStatus 初始数据集目录文件格式错误：{seed_path}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("GridStatus 初始数据集目录必须是 JSON 对象。")
    if payload.get("schema_version") != GRIDSTATUS_SEED_SCHEMA_VERSION:
        raise RuntimeError("GridStatus 初始数据集目录版本不受支持。")

    datasets = payload.get("datasets")
    expected_count = payload.get("dataset_count")
    if not isinstance(datasets, list) or expected_count != len(datasets):
        raise RuntimeError("GridStatus 初始数据集目录条数校验失败。")

    records = [GridStatusDatasetMetadataRecord.model_validate(item) for item in datasets]
    dataset_ids = [record.dataset_id for record in records]
    if len(dataset_ids) != len(set(dataset_ids)):
        raise RuntimeError("GridStatus 初始数据集目录包含重复 dataset ID。")
    return records


def seed_gridstatus_dataset_catalog_if_empty(seed_path: Path) -> int:
    engine = get_engine()
    with engine.connect() as connection:
        existing_count = int(
            connection.execute(
                text("SELECT COUNT(*) FROM gridstatus_dataset_metadata")
            ).scalar_one()
        )
    if existing_count:
        return 0

    records = load_gridstatus_dataset_seed(seed_path)
    return upsert_gridstatus_dataset_metadata_records(records)
