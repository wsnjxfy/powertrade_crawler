import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from powertrade_crawler.clients.gridstatus import GridStatusClient
from powertrade_crawler.models import GridStatusDatasetMetadataRecord, GridStatusRecord
from powertrade_crawler.spiders.base import BaseSpider


class GridStatusSpider(BaseSpider):
    source = "GridStatus"
    source_url = "https://api.gridstatus.io"

    def __init__(self, request_config: dict[str, Any]) -> None:
        self.request_config = request_config
        self.name = request_config["name"]
        self.client = GridStatusClient()

    def crawl(self) -> Iterable[GridStatusRecord | GridStatusDatasetMetadataRecord]:
        rows = self.client.request_rows(self.request_config)
        for row in rows:
            if self.request_config["type"] == "datasets":
                yield self.to_dataset_metadata_record(row)
                continue
            yield self.to_record(row)

    def to_dataset_metadata_record(self, row: dict[str, Any]) -> GridStatusDatasetMetadataRecord:
        return GridStatusDatasetMetadataRecord(
            dataset_id=row["id"],
            name=row.get("name"),
            description=row.get("description"),
            source=row.get("source"),
            status=row.get("status"),
            earliest_available_time_utc=row.get("earliest_available_time_utc"),
            latest_available_time_utc=row.get("latest_available_time_utc"),
            last_checked_time_utc=row.get("last_checked_time_utc"),
            time_index_column=row.get("time_index_column"),
            publish_time_column=row.get("publish_time_column"),
            subseries_index_column=row.get("subseries_index_column"),
            primary_key_columns=row.get("primary_key_columns") or [],
            all_columns=row.get("all_columns") or [],
            number_of_rows_approximate=row.get("number_of_rows_approximate"),
            table_type=row.get("table_type"),
            data_frequency=row.get("data_frequency"),
            source_url=row.get("source_url"),
            publication_frequency=row.get("publication_frequency"),
            is_in_snowflake=row.get("is_in_snowflake"),
            is_published=row.get("is_published"),
            created_at_utc=row.get("created_at_utc"),
            popularity_rank=row.get("popularity_rank"),
            raw=row,
        )

    def to_record(self, row: dict[str, Any]) -> GridStatusRecord:
        return GridStatusRecord(
            request_name=self.request_config["name"],
            request_type=self.request_config["type"],
            dataset=self.resolve_dataset(row),
            location=self.resolve_location(row),
            row_key=self.build_row_key(row),
            interval_start_utc=row.get("interval_start_utc"),
            interval_end_utc=row.get("interval_end_utc"),
            record_time_utc=self.resolve_record_time(row),
            raw=row,
        )

    def resolve_dataset(self, row: dict[str, Any]) -> str | None:
        return self.request_config.get("dataset") or row.get("id") or row.get("dataset")

    def resolve_location(self, row: dict[str, Any]) -> str | None:
        return self.request_config.get("location") or row.get("location")

    def resolve_record_time(self, row: dict[str, Any]) -> str | None:
        return row.get("time_utc") or row.get("interval_start_utc") or row.get("publish_time_utc")

    def build_row_key(self, row: dict[str, Any]) -> str:
        key_parts = [
            row.get("id"),
            row.get("dataset") or self.request_config.get("dataset"),
            row.get("interval_start_utc"),
            row.get("interval_end_utc"),
            row.get("time_utc"),
            row.get("publish_time_utc"),
            row.get("location") or self.request_config.get("location"),
        ]
        key = "|".join(str(part) for part in key_parts if part not in (None, ""))
        if key:
            return key[:120]

        raw_json = json.dumps(row, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw_json.encode("utf-8")).hexdigest()

    def close(self) -> None:
        self.client.close()


def load_gridstatus_request_configs() -> list[dict[str, Any]]:
    config_path = Path(__file__).resolve().parents[3] / "configs" / "gridstatus" / "requests.json"
    with config_path.open(encoding="utf-8") as file:
        payload = json.load(file)
    return payload["requests"]


def build_gridstatus_spider_classes() -> dict[str, type[GridStatusSpider]]:
    spider_classes: dict[str, type[GridStatusSpider]] = {}
    for request_config in load_gridstatus_request_configs():
        spider_name = request_config["name"]

        class ConfiguredGridStatusSpider(GridStatusSpider):
            name = spider_name

            def __init__(self, config=request_config) -> None:
                super().__init__(config)

        ConfiguredGridStatusSpider.__name__ = "".join(
            part.capitalize() for part in spider_name.split("_")
        )
        spider_classes[spider_name] = ConfiguredGridStatusSpider
    return spider_classes
