from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


JSON_COLUMNS = {
    "primary_key_columns_json": "primary_key_columns",
    "all_columns_json": "all_columns",
    "raw_json": "raw",
}
SENSITIVE_KEY_PATTERN = re.compile(
    r"api[_-]?key|authorization|credential|password|secret|token",
    re.IGNORECASE,
)
SENSITIVE_QUERY_PATTERN = re.compile(
    r"(?:api[_-]?key|authorization|password|secret|token)=[^&\s]+",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a secret-free GridStatus dataset catalog seed from SQLite."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/powertrade.db"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("configs/gridstatus/datasets.initial.json"),
    )
    return parser.parse_args()


def assert_secret_free(value: Any, *, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if SENSITIVE_KEY_PATTERN.search(str(key)):
                raise ValueError(f"Sensitive-looking key found at {path}.{key}")
            assert_secret_free(item, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            assert_secret_free(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and SENSITIVE_QUERY_PATTERN.search(value):
        raise ValueError(f"Sensitive-looking query parameter found at {path}")


def load_datasets(database_path: Path) -> list[dict[str, Any]]:
    database_uri = f"file:{database_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM gridstatus_dataset_metadata ORDER BY dataset_id"
        ).fetchall()

    datasets: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item.pop("id", None)
        for database_name, model_name in JSON_COLUMNS.items():
            raw_value = item.pop(database_name)
            item[model_name] = json.loads(raw_value)
        assert_secret_free(item, path=f"dataset[{item['dataset_id']}]")
        datasets.append(item)
    return datasets


def main() -> int:
    args = parse_args()
    datasets = load_datasets(args.database)
    if not datasets:
        raise RuntimeError("GridStatus dataset catalog is empty; refusing to export a seed.")

    payload = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source": "GridStatus API dataset catalog",
        "dataset_count": len(datasets),
        "datasets": datasets,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Exported {len(datasets)} GridStatus datasets to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
