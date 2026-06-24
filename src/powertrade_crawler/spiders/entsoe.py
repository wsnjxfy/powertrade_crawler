import hashlib
import json
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from powertrade_crawler.clients.entsoe import EntsoeClient
from powertrade_crawler.models import EntsoeRecord, MarketRecord
from powertrade_crawler.spiders.base import BaseSpider


ENTSOE_BIDDING_ZONES = {
    "AL": "10YAL-KESH-----5",
    "AT": "10YAT-APG------L",
    "BA": "10YBA-JPCC-----D",
    "BE": "10YBE----------2",
    "BG": "10YCA-BULGARIA-R",
    "CH": "10YCH-SWISSGRIDZ",
    "CZ": "10YCZ-CEPS-----N",
    "DE-LU": "10Y1001A1001A82H",
    "DK1": "10YDK-1--------W",
    "DK2": "10YDK-2--------M",
    "EE": "10Y1001A1001A39I",
    "ES": "10YES-REE------0",
    "FI": "10YFI-1--------U",
    "FR": "10YFR-RTE------C",
    "GB": "10YGB----------A",
    "GR": "10YGR-HTSO-----Y",
    "HR": "10YHR-HEP------M",
    "HU": "10YHU-MAVIR----U",
    "IE-SEM": "10Y1001A1001A59C",
    "IT-CENTRE-NORTH": "10Y1001A1001A70O",
    "IT-CENTRE-SOUTH": "10Y1001A1001A71M",
    "IT-NORTH": "10Y1001A1001A73I",
    "IT-SARDINIA": "10Y1001A1001A74G",
    "IT-SICILY": "10Y1001A1001A75E",
    "IT-SOUTH": "10Y1001A1001A788",
    "LT": "10YLT-1001A0008Q",
    "LV": "10YLV-1001A00074",
    "ME": "10YCS-CG-TSO---S",
    "MK": "10YMK-MEPSO----8",
    "NL": "10YNL----------L",
    "NO1": "10YNO-1--------2",
    "NO2": "10YNO-2--------T",
    "NO3": "10YNO-3--------J",
    "NO4": "10YNO-4--------9",
    "NO5": "10Y1001A1001A48H",
    "PL": "10YPL-AREA-----S",
    "PT": "10YPT-REN------W",
    "RO": "10YRO-TEL------P",
    "RS": "10YCS-SERBIATSOV",
    "SE1": "10Y1001A1001A44P",
    "SE2": "10Y1001A1001A45N",
    "SE3": "10Y1001A1001A46L",
    "SE4": "10Y1001A1001A47J",
    "SI": "10YSI-ELES-----O",
    "SK": "10YSK-SEPS-----K",
    "TR": "10YTR-TEIAS----W",
}


class EntsoeDayAheadPricesSpider(BaseSpider):
    name = "entsoe_day_ahead_prices"
    source = "ENTSO-E Transparency Platform"
    source_url = "https://web-api.tp.entsoe.eu/api"

    def __init__(
        self,
        area_code: str | None = None,
        area: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        security_token: str | None = None,
    ) -> None:
        self.area_alias = (area or "DE-LU").upper()
        self.area_code = area_code or self.resolve_area_code(self.area_alias)
        self.start = self.parse_cli_datetime(start_date) if start_date else self.default_start()
        self.end = self.parse_cli_datetime(end_date) if end_date else self.start + timedelta(days=1)
        self.client = EntsoeClient(security_token=security_token)

    def crawl(self) -> Iterable[MarketRecord]:
        rows = self.client.query_day_ahead_prices(
            bidding_zone_eic=self.area_code,
            period_start=self.start,
            period_end=self.end,
        )
        region = self.area_alias if self.area_alias in ENTSOE_BIDDING_ZONES else self.area_code
        for row in rows:
            yield MarketRecord(
                source=self.source,
                market="day_ahead",
                region=region,
                trade_date=datetime.fromisoformat(
                    row["interval_start_utc"].replace("Z", "+00:00")
                ).date(),
                metric=f"price_position_{row['position']:03d}",
                value=row["price"],
                unit=row["unit"],
                currency=row["currency"],
                raw={
                    **row,
                    "source_url": self.source_url,
                    "documentType": "A44",
                    "contract_MarketAgreement.type": "A01",
                    "in_Domain": self.area_code,
                    "out_Domain": self.area_code,
                },
            )

    def resolve_area_code(self, area: str) -> str:
        try:
            return ENTSOE_BIDDING_ZONES[area]
        except KeyError as exc:
            available = ", ".join(sorted(ENTSOE_BIDDING_ZONES))
            raise ValueError(
                f"Unknown ENTSO-E bidding zone: {area}. Available aliases: {available}. "
                "Use --area-code to pass an EIC code directly."
            ) from exc

    def parse_cli_datetime(self, value: str) -> datetime:
        if len(value) == 10:
            return datetime.fromisoformat(value)
        if len(value) == 12 and value.isdigit():
            return datetime.strptime(value, "%Y%m%d%H%M")
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)

    def default_start(self) -> datetime:
        now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        return now.replace(hour=0)

    def close(self) -> None:
        self.client.close()


class ConfiguredEntsoeSpider(BaseSpider):
    source = "ENTSO-E Transparency Platform"
    source_url = "https://web-api.tp.entsoe.eu/api"

    def __init__(
        self,
        request_config: dict[str, Any],
        area_code: str | None = None,
        area: str | None = None,
        in_area_code: str | None = None,
        in_area: str | None = None,
        out_area_code: str | None = None,
        out_area: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        psr_type: str | None = None,
        extra_params: dict[str, str] | None = None,
        security_token: str | None = None,
    ) -> None:
        self.request_config = request_config
        self.name = request_config["name"]
        self.area_alias = area.upper() if area else None
        self.area_code = area_code or self.resolve_optional_area(area)
        self.in_area_alias = in_area.upper() if in_area else None
        self.in_area_code = in_area_code or self.resolve_optional_area(in_area)
        self.out_area_alias = out_area.upper() if out_area else None
        self.out_area_code = out_area_code or self.resolve_optional_area(out_area)
        self.start = self.parse_cli_datetime(start_date) if start_date else self.default_start()
        self.end = self.parse_cli_datetime(end_date) if end_date else self.start + timedelta(days=1)
        if self.end <= self.start:
            raise ValueError("ENTSO-E end date must be later than start date.")
        self.psr_type = psr_type
        self.extra_params = extra_params or {}
        self.client = EntsoeClient(security_token=security_token)

    def crawl(self) -> Iterable[EntsoeRecord]:
        base_params = self.build_request_params()
        chunk_start = self.start
        chunk_size = timedelta(days=self.request_config.get("chunk_days", 31))
        while chunk_start < self.end:
            chunk_end = min(chunk_start + chunk_size, self.end)
            params = {
                **base_params,
                "periodStart": self.client.format_period(chunk_start),
                "periodEnd": self.client.format_period(chunk_end),
            }
            for row in self.client.query_document(params):
                yield self.to_record(row, params)
            chunk_start = chunk_end

    def build_request_params(self) -> dict[str, Any]:
        params = dict(self.request_config["params"])
        domain_mode = self.request_config["domain_mode"]
        if domain_mode == "single":
            if not self.area_code:
                raise ValueError(f"--area or --area-code is required for {self.name}.")
            params[self.request_config["domain_parameter"]] = self.area_code
        elif domain_mode == "same_pair":
            if not self.area_code:
                raise ValueError(f"--area or --area-code is required for {self.name}.")
            params["in_Domain"] = self.area_code
            params["out_Domain"] = self.area_code
        elif domain_mode == "border":
            if not self.in_area_code or not self.out_area_code:
                raise ValueError(
                    f"--in-area/--in-area-code and --out-area/--out-area-code "
                    f"are required for {self.name}."
                )
            params["in_Domain"] = self.in_area_code
            params["out_Domain"] = self.out_area_code
        else:
            raise ValueError(f"Unsupported ENTSO-E domain mode: {domain_mode}")

        if self.psr_type:
            params["psrType"] = self.psr_type
        params.update(self.extra_params)
        return params

    def to_record(self, row: dict[str, Any], params: dict[str, Any]) -> EntsoeRecord:
        document = row["document"]
        series = row["series"]
        document_type = self.lookup_value(document, "type") or params.get("documentType")
        process_type = (
            self.lookup_value(series, "process.processType")
            or self.lookup_value(document, "process.processType")
            or params.get("processType")
        )
        business_type = self.lookup_value(series, "businessType") or params.get("businessType")
        psr_type = self.lookup_value(series, "psrType") or params.get("psrType")
        time_series_id = self.lookup_value(series, "mRID")
        currency = self.lookup_value(series, "currency_Unit.name")
        quantity_unit = self.lookup_value(series, "quantity_Measure_Unit.name")
        price_unit = self.lookup_value(series, "price_Measure_Unit.name")
        unit = self.resolve_unit(row["value_field"], currency, price_unit, quantity_unit)
        raw = {
            **row,
            "request": {
                "dataset": self.name,
                "params": params,
                "source_url": self.source_url,
            },
        }
        return EntsoeRecord(
            dataset=self.name,
            category=self.request_config["category"],
            title_en=self.request_config["title_en"],
            title_zh=self.request_config["title_zh"],
            row_key=self.build_row_key(row, params),
            document_type=document_type,
            process_type=process_type,
            business_type=business_type,
            area=self.resolve_record_area(),
            in_domain=params.get("in_Domain"),
            out_domain=params.get("out_Domain"),
            time_series_id=time_series_id,
            psr_type=psr_type,
            interval_start_utc=row.get("interval_start_utc"),
            interval_end_utc=row.get("interval_end_utc"),
            position=row.get("position"),
            resolution=row.get("resolution"),
            value=row.get("value"),
            value_field=row.get("value_field"),
            unit=unit,
            currency=currency,
            raw=raw,
        )

    def build_row_key(self, row: dict[str, Any], params: dict[str, Any]) -> str:
        identity = {
            "dataset": self.name,
            "domain_params": {
                key: value
                for key, value in params.items()
                if "domain" in key.lower()
            },
            "series": row["series"],
            "period_start_utc": row.get("period_start_utc"),
            "interval_start_utc": row.get("interval_start_utc"),
            "interval_end_utc": row.get("interval_end_utc"),
            "position": row.get("position"),
            "point": row["point"],
        }
        payload = json.dumps(identity, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def resolve_record_area(self) -> str | None:
        if self.area_alias:
            return self.area_alias
        if self.in_area_alias or self.out_area_alias:
            return f"{self.in_area_alias or self.in_area_code}->{self.out_area_alias or self.out_area_code}"
        return self.area_code

    def lookup_value(self, values: dict[str, Any], suffix: str) -> str | None:
        exact_value = values.get(suffix)
        if exact_value is not None:
            if isinstance(exact_value, list):
                return str(exact_value[0]) if exact_value else None
            return str(exact_value)
        for key, value in values.items():
            if key.endswith(f".{suffix}"):
                if isinstance(value, list):
                    return str(value[0]) if value else None
                return str(value)
        return None

    def resolve_unit(
        self,
        value_field: str | None,
        currency: str | None,
        price_unit: str | None,
        quantity_unit: str | None,
    ) -> str | None:
        if value_field and ("price" in value_field.lower() or value_field.endswith("amount")):
            if currency and price_unit:
                return f"{currency}/{price_unit}"
            return currency or price_unit
        return quantity_unit

    def resolve_optional_area(self, area: str | None) -> str | None:
        if not area:
            return None
        normalized = area.upper()
        try:
            return ENTSOE_BIDDING_ZONES[normalized]
        except KeyError as exc:
            available = ", ".join(sorted(ENTSOE_BIDDING_ZONES))
            raise ValueError(
                f"Unknown ENTSO-E area: {area}. Available aliases: {available}. "
                "Use an explicit EIC option for areas not in the alias list."
            ) from exc

    def parse_cli_datetime(self, value: str) -> datetime:
        if len(value) == 10:
            return datetime.fromisoformat(value)
        if len(value) == 12 and value.isdigit():
            return datetime.strptime(value, "%Y%m%d%H%M")
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)

    def default_start(self) -> datetime:
        now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        return now.replace(hour=0)

    def close(self) -> None:
        self.client.close()


def load_entsoe_request_configs() -> list[dict[str, Any]]:
    config_path = Path(__file__).resolve().parents[3] / "configs" / "entsoe" / "requests.json"
    with config_path.open(encoding="utf-8") as file:
        payload = json.load(file)
    return payload["requests"]


def get_entsoe_request_config(name: str) -> dict[str, Any]:
    for request_config in load_entsoe_request_configs():
        if request_config["name"] == name:
            return request_config
    available = ", ".join(config["name"] for config in load_entsoe_request_configs())
    raise ValueError(f"Unknown ENTSO-E dataset: {name}. Available datasets: {available}")


def build_entsoe_spider_classes() -> dict[str, type[ConfiguredEntsoeSpider]]:
    spider_classes: dict[str, type[ConfiguredEntsoeSpider]] = {}
    for request_config in load_entsoe_request_configs():
        spider_name = request_config["name"]
        if spider_name == EntsoeDayAheadPricesSpider.name:
            continue

        class CatalogEntsoeSpider(ConfiguredEntsoeSpider):
            name = spider_name

            def __init__(self, config=request_config, **kwargs) -> None:
                super().__init__(config, **kwargs)

        CatalogEntsoeSpider.__name__ = "".join(
            part.capitalize() for part in spider_name.split("_")
        )
        spider_classes[spider_name] = CatalogEntsoeSpider
    return spider_classes
