import hashlib
import json
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from powertrade_crawler.clients.elexon import ElexonClient
from powertrade_crawler.models import ElexonRecord
from powertrade_crawler.spiders.base import BaseSpider


class ConfiguredElexonSpider(BaseSpider):
    source = "Elexon Insights API"
    source_url = "https://data.elexon.co.uk/bmrs/api/v1"

    def __init__(
        self,
        request_config: dict[str, Any],
        start_date: str | None = None,
        end_date: str | None = None,
        extra_params: dict[str, str] | None = None,
        api_key: str | None = None,
    ) -> None:
        self.request_config = request_config
        self.name = request_config["name"]
        today = date.today()
        self.start = self.parse_cli_datetime(start_date) if start_date else self.start_of_day(today - timedelta(days=1))
        self.end = self.parse_cli_datetime(end_date) if end_date else self.start_of_day(today)
        if self.end <= self.start:
            raise ValueError("Elexon end date must be later than start date.")
        self.extra_params = extra_params or {}
        self.client = ElexonClient(api_key=api_key)

    def crawl(self) -> Iterable[ElexonRecord]:
        for spec in self.build_request_specs():
            rows = self.client.query(path=spec["path"], params=spec["params"])
            for row in rows:
                yield from self.to_records(row, spec)

    def build_request_specs(self) -> list[dict[str, Any]]:
        time_mode = self.request_config["time_mode"]
        if time_mode in {"publish_range", "from_to"}:
            return self.build_datetime_range_specs(time_mode)
        if time_mode == "settlement_date_range":
            return self.build_settlement_date_range_specs()
        if time_mode == "settlement_date_path":
            return self.build_settlement_date_path_specs()
        if time_mode == "settlement_date_period":
            return self.build_settlement_date_period_specs()
        if time_mode == "snapshot":
            return self.build_snapshot_specs()
        raise ValueError(f"Unsupported Elexon time mode: {time_mode}")

    def build_datetime_range_specs(self, time_mode: str) -> list[dict[str, Any]]:
        specs = []
        from_name, to_name = (
            ("publishDateTimeFrom", "publishDateTimeTo")
            if time_mode == "publish_range"
            else ("from", "to")
        )
        for chunk_start, chunk_end in self.iter_datetime_chunks():
            params = self.base_params()
            params[from_name] = self.format_elexon_time(chunk_start)
            params[to_name] = self.format_elexon_time(chunk_end)
            specs.append({"path": self.request_config["endpoint"], "params": params})
        return specs

    def build_settlement_date_range_specs(self) -> list[dict[str, Any]]:
        specs = []
        for chunk_start, chunk_end in self.iter_date_chunks():
            params = self.base_params()
            params["settlementDateFrom"] = chunk_start.isoformat()
            params["settlementDateTo"] = (chunk_end - timedelta(days=1)).isoformat()
            specs.append({"path": self.request_config["endpoint"], "params": params})
        return specs

    def build_settlement_date_path_specs(self) -> list[dict[str, Any]]:
        specs = []
        settlement_period = self.extra_params.get("settlementPeriod")
        base_params = self.base_params(exclude={"settlementPeriod"})
        for settlement_date in self.iter_settlement_dates():
            if settlement_period:
                path = str(self.request_config["period_endpoint"]).format(
                    settlement_date=settlement_date.isoformat(),
                    settlement_period=settlement_period,
                )
            else:
                path = str(self.request_config["endpoint"]).format(
                    settlement_date=settlement_date.isoformat()
                )
            specs.append({"path": path, "params": dict(base_params)})
        return specs

    def build_settlement_date_period_specs(self) -> list[dict[str, Any]]:
        specs = []
        base_params = self.base_params(exclude={"settlementPeriod"})
        settlement_periods = self.settlement_period_values()
        for settlement_date in self.iter_settlement_dates():
            for settlement_period in settlement_periods:
                params = {
                    **base_params,
                    "settlementDate": settlement_date.isoformat(),
                    "settlementPeriod": settlement_period,
                }
                specs.append({"path": self.request_config["endpoint"], "params": params})
        return specs

    def build_snapshot_specs(self) -> list[dict[str, Any]]:
        return [{"path": self.request_config["endpoint"], "params": self.base_params()}]

    def base_params(self, *, exclude: set[str] | None = None) -> dict[str, str]:
        exclude = exclude or set()
        params = {
            str(key): str(value)
            for key, value in self.request_config.get("default_params", {}).items()
        }
        params.update(self.extra_params)
        for key in exclude:
            params.pop(key, None)
        return params

    def settlement_period_values(self) -> list[str]:
        raw_value = self.extra_params.get(
            "settlementPeriod",
            self.request_config.get("default_params", {}).get("settlementPeriod", "1"),
        )
        values = [value.strip() for value in str(raw_value).split(",") if value.strip()]
        return values or ["1"]

    def to_records(
        self,
        row: dict[str, Any],
        request_spec: dict[str, Any],
    ) -> Iterable[ElexonRecord]:
        for value_config in self.request_config["value_fields"]:
            value_field = value_config["field"]
            value = self.parse_optional_float(row.get(value_field))
            if value is None:
                continue
            metric = value_config["metric"]
            raw = {
                **row,
                "request": {
                    "dataset": self.name,
                    "path": request_spec["path"],
                    "params": request_spec["params"],
                    "source_url": self.source_url,
                },
            }
            yield ElexonRecord(
                dataset=self.name,
                category=self.request_config["category"],
                title_en=self.request_config["title_en"],
                title_zh=self.request_config["title_zh"],
                endpoint=self.request_config["endpoint"],
                row_key=self.build_row_key(row, request_spec, metric, value_field),
                area="GB",
                settlement_date=self.optional_str(row.get("settlementDate") or row.get("forecastDate")),
                settlement_period=self.parse_optional_int(row.get("settlementPeriod")),
                publish_time_utc=self.resolve_publish_time(row),
                start_time_utc=self.resolve_start_time(row),
                end_time_utc=self.resolve_end_time(row),
                fuel_type=self.resolve_dimension(row),
                bm_unit=self.optional_str(row.get("bmUnit")),
                national_grid_bm_unit=self.optional_str(row.get("nationalGridBmUnit")),
                metric=metric,
                value=value,
                value_field=value_field,
                unit=value_config.get("unit"),
                currency=value_config.get("currency"),
                raw=raw,
            )

    def build_row_key(
        self,
        row: dict[str, Any],
        request_spec: dict[str, Any],
        metric: str,
        value_field: str,
    ) -> str:
        identity = {
            "dataset": self.name,
            "path": request_spec["path"],
            "params": request_spec["params"],
            "metric": metric,
            "value_field": value_field,
            "row": row,
        }
        payload = json.dumps(identity, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def iter_datetime_chunks(self) -> Iterable[tuple[datetime, datetime]]:
        chunk_size = timedelta(days=int(self.request_config.get("chunk_days", 7)))
        chunk_start = self.start
        while chunk_start < self.end:
            chunk_end = min(chunk_start + chunk_size, self.end)
            yield chunk_start, chunk_end
            chunk_start = chunk_end

    def iter_date_chunks(self) -> Iterable[tuple[date, date]]:
        chunk_size = int(self.request_config.get("chunk_days", 14))
        chunk_start = self.start.date()
        final_end = self.end.date()
        while chunk_start < final_end:
            chunk_end = min(chunk_start + timedelta(days=chunk_size), final_end)
            yield chunk_start, chunk_end
            chunk_start = chunk_end

    def iter_settlement_dates(self) -> Iterable[date]:
        current = self.start.date()
        end = self.end.date()
        while current < end:
            yield current
            current += timedelta(days=1)

    def resolve_publish_time(self, row: dict[str, Any]) -> str | None:
        return self.optional_str(row.get("publishTime") or row.get("createdDateTime"))

    def resolve_start_time(self, row: dict[str, Any]) -> str | None:
        return self.optional_str(row.get("startTime") or row.get("timeFrom"))

    def resolve_end_time(self, row: dict[str, Any]) -> str | None:
        return self.optional_str(row.get("endTime") or row.get("timeTo"))

    def resolve_dimension(self, row: dict[str, Any]) -> str | None:
        for key in (
            "fuelType",
            "psrType",
            "interconnectorName",
            "dataProvider",
            "boundary",
        ):
            value = self.optional_str(row.get(key))
            if value:
                return value
        return None

    def parse_cli_datetime(self, value: str) -> datetime:
        stripped = value.strip()
        if len(stripped) == 10:
            return self.start_of_day(date.fromisoformat(stripped))
        return datetime.fromisoformat(stripped.replace("Z", "+00:00")).replace(tzinfo=None)

    def start_of_day(self, value: date) -> datetime:
        return datetime.combine(value, time.min)

    def format_elexon_time(self, value: datetime) -> str:
        return value.strftime("%Y-%m-%dT%H:%MZ")

    def parse_optional_float(self, value: Any) -> float | None:
        if isinstance(value, bool) or value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def parse_optional_int(self, value: Any) -> int | None:
        if isinstance(value, bool) or value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def optional_str(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def close(self) -> None:
        self.client.close()


def load_elexon_request_configs() -> list[dict[str, Any]]:
    config_path = Path(__file__).resolve().parents[3] / "configs" / "elexon" / "requests.json"
    with config_path.open(encoding="utf-8") as file:
        payload = json.load(file)
    return payload["requests"]


def get_elexon_request_config(name: str) -> dict[str, Any]:
    for request_config in load_elexon_request_configs():
        if request_config["name"] == name:
            return request_config
    available = ", ".join(config["name"] for config in load_elexon_request_configs())
    raise ValueError(f"Unknown Elexon dataset: {name}. Available datasets: {available}")


def build_elexon_spider_classes() -> dict[str, type[ConfiguredElexonSpider]]:
    spider_classes: dict[str, type[ConfiguredElexonSpider]] = {}
    for request_config in load_elexon_request_configs():
        spider_name = request_config["name"]

        class CatalogElexonSpider(ConfiguredElexonSpider):
            name = spider_name

            def __init__(self, config=request_config, **kwargs) -> None:
                super().__init__(config, **kwargs)

        CatalogElexonSpider.__name__ = "".join(
            part.capitalize() for part in spider_name.split("_")
        )
        spider_classes[spider_name] = CatalogElexonSpider
    return spider_classes
