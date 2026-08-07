from __future__ import annotations

import csv
import json
import math
import re
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import fmean
from typing import Any, Literal

from pydantic import Field, model_validator

from powertrade_crawler.config import get_settings
from powertrade_crawler.credentials import get_credential
from powertrade_crawler.market_agent.schemas import (
    BusinessMetric,
    ComparisonResult,
    GroundedFact,
    RiskLevel,
    SeriesPoint,
    SeriesResult,
    StrictModel,
)
from powertrade_crawler.market_agent.tools import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
)
from powertrade_crawler.scheduler import run_spider_and_upsert
from powertrade_crawler.spiders.elexon import (
    get_elexon_request_config,
    load_elexon_request_configs,
)
from powertrade_crawler.spiders.entsoe import ENTSOE_BIDDING_ZONES
from powertrade_crawler.spiders.gridstatus import GridStatusSpider
from powertrade_crawler.storage import (
    resolve_elecheck_area_code,
    upsert_gridstatus_dataset_metadata_records,
    upsert_gridstatus_records,
)


SourceName = Literal["elecheck", "entsoe", "elexon", "gridstatus", "gzpec"]
Aggregation = Literal["raw", "daily", "monthly"]
Statistic = Literal["average", "minimum", "maximum", "latest", "count"]
GroupBy = Literal["none", "area", "metric", "fuel_type"]

SOURCE_LABELS = {
    "elecheck": "Elecheck 易能电易查",
    "entsoe": "ENTSO-E Transparency Platform",
    "elexon": "Elexon Insights API",
    "gridstatus": "GridStatus",
    "gzpec": "广州电力交易中心",
}

DATASET_CATALOG: dict[str, dict[str, Any]] = {
    "elecheck_spot": {
        "source": "elecheck",
        "title": "现货出清价格",
        "metric": "avg_day_ahead_price",
        "canonical_metric": "spot_price",
        "unit": "CNY/MWh",
        "currency": "CNY",
        "time_basis": "中国市场业务日期（Asia/Shanghai）",
    },
    "elecheck_purchasing": {
        "source": "elecheck",
        "title": "代理购电价格与费用",
        "metric": "purchasing_price",
        "canonical_metric": "purchasing_price",
        "unit": "CNY/kWh",
        "currency": "CNY",
        "time_basis": "中国市场业务月份",
    },
    "elecheck_mechanism": {
        "source": "elecheck",
        "title": "增量机制电价快照",
        "metric": "mechanism_price",
        "canonical_metric": "mechanism_price",
        "unit": "CNY/kWh",
        "currency": "CNY",
        "time_basis": "最新快照",
    },
    "entsoe_day_ahead_prices": {
        "source": "entsoe",
        "title": "日前电价",
        "metric": "price.amount",
        "canonical_metric": "spot_price",
        "unit": "EUR/MWh",
        "currency": "EUR",
        "time_basis": "ENTSO-E UTC 时段",
    },
    "entsoe_actual_total_load": {
        "source": "entsoe",
        "title": "实际总负荷",
        "metric": "quantity",
        "canonical_metric": "actual_load",
        "unit": "MW",
        "currency": None,
        "time_basis": "ENTSO-E UTC 时段",
    },
    "entsoe_actual_generation_by_type": {
        "source": "entsoe",
        "title": "按类型实际发电",
        "metric": "quantity",
        "canonical_metric": "actual_generation",
        "unit": "MW",
        "currency": None,
        "time_basis": "ENTSO-E UTC 时段",
    },
    "entsoe_cross_border_physical_flows": {
        "source": "entsoe",
        "title": "跨境物理潮流",
        "metric": "quantity",
        "canonical_metric": "physical_flow",
        "unit": "MW",
        "currency": None,
        "time_basis": "ENTSO-E UTC 时段",
    },
    "elexon_system_prices": {
        "source": "elexon",
        "title": "结算系统价格",
        "metric": "system_sell_price",
        "canonical_metric": "imbalance_price",
        "unit": "GBP/MWh",
        "currency": "GBP",
        "time_basis": "英国结算日期与结算时段",
    },
    "elexon_initial_demand_outturn": {
        "source": "elexon",
        "title": "初始全国负荷实绩",
        "metric": "initial_demand_outturn",
        "canonical_metric": "actual_load",
        "unit": "MW",
        "currency": None,
        "time_basis": "英国结算日期",
    },
    "elexon_generation_by_fuel_half_hourly": {
        "source": "elexon",
        "title": "半小时燃料类型发电",
        "metric": "generation",
        "canonical_metric": "actual_generation",
        "unit": "MW",
        "currency": None,
        "time_basis": "Elexon UTC 发布时间",
    },
    "elexon_wind_generation_forecast": {
        "source": "elexon",
        "title": "风电发电预测",
        "metric": "wind_generation_forecast",
        "canonical_metric": "wind_generation_forecast",
        "unit": "MW",
        "currency": None,
        "time_basis": "Elexon UTC 预测时段",
    },
    "elexon_interconnector_flows": {
        "source": "elexon",
        "title": "互联线潮流",
        "metric": "interconnector_flow",
        "canonical_metric": "physical_flow",
        "unit": "MW",
        "currency": None,
        "time_basis": "英国结算日期与结算时段",
    },
    "gzpec_news": {
        "source": "gzpec",
        "title": "广州电力交易中心公开信息",
        "metric": "article_count",
        "canonical_metric": "article_count",
        "unit": "篇",
        "currency": None,
        "time_basis": "页面发布日期",
    },
}

ENTSOE_COLLECTION_DATASETS = {
    "entsoe_day_ahead_prices",
    "entsoe_actual_total_load",
    "entsoe_actual_generation_by_type",
    "entsoe_cross_border_physical_flows",
}
ELEXON_COLLECTION_DATASETS = {
    "elexon_system_prices",
    "elexon_initial_demand_outturn",
    "elexon_generation_by_fuel_half_hourly",
    "elexon_wind_generation_forecast",
    "elexon_interconnector_flows",
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_sqlite_path() -> Path:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        raise ValueError("多数据源 Agent MVP 仅支持本地 SQLite 数据库。")
    path = Path(url.removeprefix("sqlite:///"))
    return path if path.is_absolute() else (project_root() / path).resolve()


def connect_database() -> sqlite3.Connection:
    connection = sqlite3.connect(resolve_sqlite_path(), timeout=60)
    connection.row_factory = sqlite3.Row
    return connection


def _iso_range(
    start_date: date | None,
    end_date: date | None,
    *,
    max_days: int,
) -> tuple[str | None, str | None]:
    if start_date and end_date and start_date > end_date:
        raise ValueError("start_date must be before or equal to end_date.")
    if start_date and end_date and (end_date - start_date).days + 1 > max_days:
        raise ValueError(f"Date range cannot exceed {max_days} days.")
    return (
        start_date.isoformat() if start_date else None,
        end_date.isoformat() if end_date else None,
    )


def _month_count(start_month: str, end_month: str) -> int:
    start_year, start_number = (int(item) for item in start_month.split("-"))
    end_year, end_number = (int(item) for item in end_month.split("-"))
    if not (1 <= start_number <= 12 and 1 <= end_number <= 12):
        raise ValueError("Month must use YYYY-MM.")
    return (end_year - start_year) * 12 + end_number - start_number + 1


def _safe_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _canonical_unit(value: str | None, fallback: str | None = None) -> str | None:
    raw = value or fallback
    if not raw:
        return None
    return {
        "CNY/MWH": "CNY/MWh",
        "EUR/MWH": "EUR/MWh",
        "GBP/MWH": "GBP/MWh",
        "CNY/KWH": "CNY/kWh",
    }.get(raw.upper(), raw)


def _bucket(period: str, aggregation: Aggregation) -> str:
    if aggregation == "monthly":
        return period[:7]
    if aggregation == "daily":
        return period[:10]
    return period


def _aggregate_values(values: list[tuple[str, float]], statistic: Statistic) -> float:
    if statistic == "count":
        return float(len(values))
    if statistic == "minimum":
        return min(value for _, value in values)
    if statistic == "maximum":
        return max(value for _, value in values)
    if statistic == "latest":
        return sorted(values, key=lambda item: item[0])[-1][1]
    return fmean(value for _, value in values)


def _aggregate_rows(
    rows: list[dict[str, Any]],
    *,
    aggregation: Aggregation,
    statistic: Statistic,
    group_by: GroupBy,
    limit: int,
    equal_area_weight: bool = False,
) -> tuple[list[SeriesPoint], bool]:
    grouped: dict[tuple[str, str | None], list[tuple[str, float]]] = defaultdict(list)
    for row in rows:
        value = _safe_number(row.get("value"))
        period = str(row.get("period") or "")
        if value is None or not period:
            continue
        output_group = str(row.get("group")) if row.get("group") not in {None, ""} else None
        key = (_bucket(period, aggregation), output_group if group_by != "none" else None)
        if equal_area_weight and group_by == "none":
            key = (_bucket(period, aggregation), str(row.get("area") or ""))
        grouped[key].append((period, value))

    if equal_area_weight and group_by == "none":
        area_values: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for (period, _area), values in grouped.items():
            area_values[period].append((period, _aggregate_values(values, statistic)))
        grouped = {
            (period, None): values
            for period, values in area_values.items()
        }

    points = [
        SeriesPoint(
            period=period,
            group=group,
            value=(
                int(_aggregate_values(values, statistic))
                if statistic == "count"
                else _aggregate_values(values, statistic)
            ),
        )
        for (period, group), values in grouped.items()
    ]
    points.sort(key=lambda item: (item.period, item.group or ""))
    truncated = len(points) > limit
    return points[:limit], truncated


def _facts_for_series(series: SeriesResult) -> list[GroundedFact]:
    values = [
        float(point.value)
        for point in series.points
        if isinstance(point.value, (int, float))
    ]
    if not values:
        return []
    prefix = re.sub(r"[^a-z0-9]+", "_", f"{series.source}_{series.dataset}".lower())
    coverage = (
        f"{series.points[0].period} 至 {series.points[-1].period}"
        if series.points
        else None
    )
    return [
        GroundedFact(
            fact_id=f"{prefix}_{name}",
            label=label,
            value=value,
            unit=series.unit,
            source=series.source,
            dataset=series.dataset,
            time_basis=series.time_basis,
            calculation=calculation,
            coverage=coverage,
        )
        for name, label, value, calculation in (
            ("count", "有效点数", len(values), "非空结果点计数"),
            ("average", "结果均值", fmean(values), "结果点算术平均"),
            ("minimum", "结果最小值", min(values), "结果点最小值"),
            ("maximum", "结果最大值", max(values), "结果点最大值"),
            ("latest", "最新值", values[-1], "按结果时间排序后的最后一个值"),
        )
    ]


class EmptyArgs(StrictModel):
    pass


class DataOverviewArgs(StrictModel):
    source: SourceName | None = None


class ListDatasetsArgs(StrictModel):
    source: SourceName | None = None
    search: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=100, ge=1, le=500)


class QuerySeriesArgs(StrictModel):
    source: SourceName
    dataset: str = Field(min_length=1, max_length=160)
    metric: str | None = Field(default=None, max_length=160)
    area: str | None = Field(default=None, max_length=160)
    start_date: date | None = None
    end_date: date | None = None
    aggregation: Aggregation = "daily"
    statistic: Statistic = "average"
    group_by: GroupBy = "none"
    limit: int = Field(default=366, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_window(self) -> "QuerySeriesArgs":
        _iso_range(self.start_date, self.end_date, max_days=366)
        if self.source == "gzpec":
            raise ValueError("GZPEC 新闻请使用 market_search_gzpec_news。")
        if self.source != "gridstatus":
            config = DATASET_CATALOG.get(self.dataset)
            if config is None or config["source"] != self.source:
                raise ValueError("Dataset is not allowed for the selected source.")
        return self


class SeriesSpec(QuerySeriesArgs):
    limit: int = Field(default=366, ge=1, le=1000)


class CompareSeriesArgs(StrictModel):
    series: list[SeriesSpec] = Field(min_length=2, max_length=4)


class AnalyzeSpotArgs(StrictModel):
    area: str | None = Field(default=None, max_length=120)
    start_date: date | None = None
    end_date: date | None = None
    metric: Literal["day_ahead", "real_time", "spread"] = "day_ahead"
    aggregation: Literal["daily", "monthly"] = "daily"

    @model_validator(mode="after")
    def validate_window(self) -> "AnalyzeSpotArgs":
        _iso_range(self.start_date, self.end_date, max_days=366)
        return self


class AnalyzePurchasingArgs(StrictModel):
    province: str | None = Field(default=None, max_length=120)
    start_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    end_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    metric: Literal[
        "purchasing_price",
        "line_loss_cost",
        "purchasing_system_operating_cost",
        "purchasing_sum",
    ] = "purchasing_price"


class AnalyzeMechanismArgs(StrictModel):
    region: str | None = Field(default=None, max_length=120)
    limit: int = Field(default=100, ge=1, le=200)


class SearchNewsArgs(StrictModel):
    keyword: str | None = Field(default=None, max_length=100)
    category: str | None = Field(default=None, max_length=80)
    news_type: Literal["green_certificate", "spot_market", "ordinary"] | None = None
    start_date: date | None = None
    end_date: date | None = None
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_window(self) -> "SearchNewsArgs":
        _iso_range(self.start_date, self.end_date, max_days=3660)
        return self


class GetNewsArticleArgs(StrictModel):
    url: str = Field(min_length=10, max_length=1000)


class ExportResultArgs(StrictModel):
    series: list[SeriesSpec] = Field(min_length=1, max_length=4)
    formats: list[Literal["csv", "png"]] = Field(default_factory=lambda: ["csv"])


class CollectElecheckArgs(StrictModel):
    category: Literal["spot", "purchasing", "mechanism"]
    area: str | None = Field(default=None, max_length=120)
    start_date: date | None = None
    end_date: date | None = None
    start_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    end_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")

    @model_validator(mode="after")
    def validate_category_arguments(self) -> "CollectElecheckArgs":
        if self.category == "spot":
            if not self.area or not self.start_date or not self.end_date:
                raise ValueError("Spot collection requires area, start_date and end_date.")
            _iso_range(self.start_date, self.end_date, max_days=31)
        elif self.category == "purchasing":
            if not self.start_month or not self.end_month:
                raise ValueError("Purchasing collection requires start_month and end_month.")
            count = _month_count(self.start_month, self.end_month)
            if count < 1 or count > 24:
                raise ValueError("Purchasing collection range must be 1 to 24 months.")
        return self


class CollectEntsoeArgs(StrictModel):
    dataset: Literal[
        "entsoe_day_ahead_prices",
        "entsoe_actual_total_load",
        "entsoe_actual_generation_by_type",
        "entsoe_cross_border_physical_flows",
    ]
    area: str | None = None
    in_area: str | None = None
    out_area: str | None = None
    psr_type: str | None = Field(default=None, max_length=20)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_collection(self) -> "CollectEntsoeArgs":
        _iso_range(self.start_date, self.end_date, max_days=31)
        if self.dataset == "entsoe_cross_border_physical_flows":
            if not self.in_area or not self.out_area:
                raise ValueError("Cross-border flow requires in_area and out_area.")
        elif not self.area:
            raise ValueError("This ENTSO-E dataset requires area.")
        for area in (self.area, self.in_area, self.out_area):
            if area and area.upper() not in ENTSOE_BIDDING_ZONES:
                raise ValueError(f"Unknown ENTSO-E bidding zone: {area}")
        return self


class CollectElexonArgs(StrictModel):
    dataset: Literal[
        "elexon_system_prices",
        "elexon_initial_demand_outturn",
        "elexon_generation_by_fuel_half_hourly",
        "elexon_wind_generation_forecast",
        "elexon_interconnector_flows",
    ]
    start_date: date
    end_date: date
    filter_name: str | None = Field(default=None, max_length=80)
    filter_value: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_collection(self) -> "CollectElexonArgs":
        _iso_range(self.start_date, self.end_date, max_days=31)
        if bool(self.filter_name) != bool(self.filter_value):
            raise ValueError("filter_name and filter_value must be provided together.")
        if self.filter_name:
            config = get_elexon_request_config(self.dataset)
            time_fields = {
                "from",
                "to",
                "publishDateTimeFrom",
                "publishDateTimeTo",
                "settlementDateFrom",
                "settlementDateTo",
            }
            allowed = set(config.get("parameter_notes", {})) - time_fields
            if self.filter_name not in allowed:
                raise ValueError(
                    f"Unsupported filter for {self.dataset}: {self.filter_name}"
                )
        return self


class CollectGridStatusArgs(StrictModel):
    operation: Literal["refresh_catalog", "query"]
    dataset: str | None = Field(default=None, max_length=160)
    location: str | None = Field(default=None, max_length=160)
    start_date: date | None = None
    end_date: date | None = None
    limit: int = Field(default=1000, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_collection(self) -> "CollectGridStatusArgs":
        if self.operation == "query":
            if not self.dataset or not self.start_date or not self.end_date:
                raise ValueError("GridStatus query requires dataset and date range.")
            _iso_range(self.start_date, self.end_date, max_days=7)
        return self


class CollectGzpecArgs(StrictModel):
    pass


def data_overview(args: DataOverviewArgs, _context: ToolContext) -> dict[str, Any]:
    credentials = {
        "elecheck": bool(get_credential("elecheck_authorization")),
        "entsoe": bool(get_credential("entsoe_security_token")),
        "elexon": bool(get_credential("elexon_api_key")),
        "gridstatus": bool(get_credential("gridstatus_api_key")),
        "gzpec": True,
    }
    with connect_database() as connection:
        source_queries = {
            "elecheck": """
                SELECT
                    (SELECT COUNT(*) FROM elecheck_clear_price_records)
                    + (SELECT COUNT(*) FROM elecheck_purchasing_records)
                    + (SELECT COUNT(*) FROM elecheck_mechanism_electricity_price_records) AS n,
                    (SELECT MIN(start_date) FROM elecheck_clear_price_records) AS earliest,
                    (SELECT MAX(start_date) FROM elecheck_clear_price_records) AS latest,
                    MAX(collected) AS collected
                FROM (
                    SELECT MAX(collected_at) AS collected FROM elecheck_clear_price_records
                    UNION ALL SELECT MAX(collected_at) FROM elecheck_purchasing_records
                    UNION ALL SELECT MAX(collected_at)
                    FROM elecheck_mechanism_electricity_price_records
                )
            """,
            "entsoe": """
                SELECT
                    (SELECT COUNT(*) FROM entsoe_records)
                    + (SELECT COUNT(*) FROM market_records
                       WHERE source='ENTSO-E Transparency Platform') AS n,
                    MIN(earliest) AS earliest, MAX(latest) AS latest,
                    MAX(collected) AS collected
                FROM (
                    SELECT MIN(substr(interval_start_utc,1,10)) AS earliest,
                           MAX(substr(interval_start_utc,1,10)) AS latest,
                           MAX(collected_at) AS collected FROM entsoe_records
                    UNION ALL
                    SELECT MIN(trade_date), MAX(trade_date), MAX(collected_at)
                    FROM market_records WHERE source='ENTSO-E Transparency Platform'
                )
            """,
            "elexon": """
                SELECT COUNT(*) AS n,
                       MIN(COALESCE(settlement_date, substr(start_time_utc,1,10),
                                    substr(publish_time_utc,1,10))) AS earliest,
                       MAX(COALESCE(settlement_date, substr(start_time_utc,1,10),
                                    substr(publish_time_utc,1,10))) AS latest,
                       MAX(collected_at) AS collected
                FROM elexon_records
            """,
            "gridstatus": """
                SELECT
                    (SELECT COUNT(*) FROM gridstatus_records)
                    + (SELECT COUNT(*) FROM gridstatus_dataset_metadata) AS n,
                    MIN(substr(COALESCE(interval_start_utc,record_time_utc),1,10)) AS earliest,
                    MAX(substr(COALESCE(interval_start_utc,record_time_utc),1,10)) AS latest,
                    MAX(collected_at) AS collected
                FROM gridstatus_records
            """,
            "gzpec": """
                SELECT COUNT(*) AS n, MIN(publish_date) AS earliest,
                       MAX(publish_date) AS latest, MAX(collected_at) AS collected
                FROM gzpec_news_records
            """,
        }
        requested = [args.source] if args.source else list(SOURCE_LABELS)
        sources = []
        facts = []
        for source in requested:
            row = connection.execute(source_queries[source]).fetchone()
            item = {
                "source": source,
                "display_name": SOURCE_LABELS[source],
                "record_count": int(row["n"] or 0),
                "earliest_business_date": row["earliest"],
                "latest_business_date": row["latest"],
                "last_collected_at": row["collected"],
                "credential_configured": credentials[source],
            }
            sources.append(item)
            facts.append(
                GroundedFact(
                    fact_id=f"{source}_record_count",
                    label=f"{SOURCE_LABELS[source]}本地记录数",
                    value=item["record_count"],
                    unit="条",
                    source=SOURCE_LABELS[source],
                    dataset="source_overview",
                    time_basis="本地数据库状态",
                    calculation="相关业务表 COUNT(*) 合计",
                    coverage=(
                        f"{item['earliest_business_date']} 至 {item['latest_business_date']}"
                        if item["earliest_business_date"]
                        else "暂无业务日期"
                    ),
                ).model_dump(mode="json")
            )
    return {
        "sources": sources,
        "facts": facts,
        "warnings": ["凭据状态只返回是否已配置，不读取或展示凭据内容。"],
        "data_sources": [item["display_name"] for item in sources],
    }


def list_datasets(args: ListDatasetsArgs, _context: ToolContext) -> dict[str, Any]:
    search = (args.search or "").strip().lower()
    rows = []
    with connect_database() as connection:
        for dataset, config in DATASET_CATALOG.items():
            if args.source and config["source"] != args.source:
                continue
            haystack = f"{dataset} {config['title']} {config['metric']}".lower()
            if search and search not in haystack:
                continue
            rows.append(
                {
                    "dataset": dataset,
                    **config,
                    **_dataset_local_coverage(
                        connection,
                        dataset,
                        str(config["source"]),
                    ),
                    "dynamic": False,
                }
            )
        if args.source in {None, "gridstatus"}:
            metadata = connection.execute(
                """
                SELECT dataset_id,name,description_chinese,source,status,
                       earliest_available_time_utc,latest_available_time_utc,
                       data_frequency,is_published,all_columns_json
                FROM gridstatus_dataset_metadata
                ORDER BY COALESCE(popularity_rank,999999),dataset_id
                LIMIT ?
                """,
                (args.limit,),
            ).fetchall()
            for row in metadata:
                haystack = " ".join(
                    str(row[key] or "")
                    for key in ("dataset_id", "name", "description_chinese", "source")
                ).lower()
                if search and search not in haystack:
                    continue
                rows.append(
                    {
                        "dataset": row["dataset_id"],
                        "source": "gridstatus",
                        "title": row["name"] or row["dataset_id"],
                        "metric": None,
                        "canonical_metric": f"gridstatus:{row['dataset_id']}",
                        "unit": None,
                        "currency": None,
                        "time_basis": "GridStatus UTC 时段",
                        "status": row["status"],
                        "is_published": bool(row["is_published"]),
                        "data_frequency": row["data_frequency"],
                        "earliest": row["earliest_available_time_utc"],
                        "latest": row["latest_available_time_utc"],
                        **_dataset_local_coverage(
                            connection,
                            row["dataset_id"],
                            "gridstatus",
                        ),
                        "columns": json.loads(row["all_columns_json"] or "[]"),
                        "dynamic": True,
                    }
                )
    rows = rows[: args.limit]
    source_names = [args.source] if args.source else list(SOURCE_LABELS)
    catalog_summaries = []
    for source in source_names:
        returned_count = sum(1 for row in rows if row["source"] == source)
        agent_supported_count = sum(
            1 for config in DATASET_CATALOG.values() if config["source"] == source
        )
        project_catalog_count = None
        lookup_hint = None
        if source == "elexon":
            project_catalog_count = len(load_elexon_request_configs())
            scope_note = (
                f"多数据源 Agent 当前支持 {agent_supported_count} 个 Elexon 数据集；"
                f"项目完整 Elexon 接口目录共 {project_catalog_count} 项。"
            )
            lookup_hint = (
                "可在“Elexon 英国”页签查看完整接口目录，或运行 "
                "powertrade elexon-datasets。"
            )
        else:
            scope_note = (
                f"多数据源 Agent 当前支持 {agent_supported_count} 个"
                f"{SOURCE_LABELS[source]}受控数据集。"
            )
        catalog_summaries.append(
            {
                "source": source,
                "display_name": SOURCE_LABELS[source],
                "returned_count": returned_count,
                "agent_supported_count": agent_supported_count,
                "project_catalog_count": project_catalog_count,
                "scope_note": scope_note,
                "lookup_hint": lookup_hint,
            }
        )
    return {
        "datasets": rows,
        "count": len(rows),
        "catalog_summaries": catalog_summaries,
        "data_sources": sorted({SOURCE_LABELS[row["source"]] for row in rows}),
        "warnings": [],
    }


def _dataset_local_coverage(
    connection: sqlite3.Connection,
    dataset: str,
    source: str,
) -> dict[str, Any]:
    if dataset == "elecheck_spot":
        query = (
            "SELECT COUNT(*) n,MIN(start_date) earliest,MAX(start_date) latest "
            "FROM elecheck_clear_price_records"
        )
        params: tuple[Any, ...] = ()
    elif dataset == "elecheck_purchasing":
        query = (
            "SELECT COUNT(*) n,MIN(data_month) earliest,MAX(data_month) latest "
            "FROM elecheck_purchasing_records"
        )
        params = ()
    elif dataset == "elecheck_mechanism":
        query = (
            "SELECT COUNT(*) n,MIN(collected_at) earliest,MAX(collected_at) latest "
            "FROM elecheck_mechanism_electricity_price_records"
        )
        params = ()
    elif source == "entsoe" and dataset == "entsoe_day_ahead_prices":
        query = """
            SELECT SUM(n) n,MIN(earliest) earliest,MAX(latest) latest FROM (
                SELECT COUNT(*) n,MIN(substr(interval_start_utc,1,10)) earliest,
                       MAX(substr(interval_start_utc,1,10)) latest
                FROM entsoe_records WHERE dataset=?
                UNION ALL
                SELECT COUNT(*),MIN(trade_date),MAX(trade_date)
                FROM market_records
                WHERE source='ENTSO-E Transparency Platform' AND market='day_ahead'
            )
        """
        params = (dataset,)
    elif source == "entsoe":
        query = """
            SELECT COUNT(*) n,MIN(substr(interval_start_utc,1,10)) earliest,
                   MAX(substr(interval_start_utc,1,10)) latest
            FROM entsoe_records WHERE dataset=?
        """
        params = (dataset,)
    elif source == "elexon":
        query = """
            SELECT COUNT(*) n,
                   MIN(COALESCE(settlement_date,substr(start_time_utc,1,10),
                                substr(publish_time_utc,1,10))) earliest,
                   MAX(COALESCE(settlement_date,substr(start_time_utc,1,10),
                                substr(publish_time_utc,1,10))) latest
            FROM elexon_records WHERE dataset=?
        """
        params = (dataset,)
    elif source == "gridstatus":
        query = """
            SELECT COUNT(*) n,
                   MIN(substr(COALESCE(interval_start_utc,record_time_utc),1,10)) earliest,
                   MAX(substr(COALESCE(interval_start_utc,record_time_utc),1,10)) latest
            FROM gridstatus_records WHERE dataset=?
        """
        params = (dataset,)
    elif source == "gzpec":
        query = (
            "SELECT COUNT(*) n,MIN(publish_date) earliest,MAX(publish_date) latest "
            "FROM gzpec_news_records"
        )
        params = ()
    else:
        return {
            "local_record_count": 0,
            "local_earliest": None,
            "local_latest": None,
        }
    row = connection.execute(query, params).fetchone()
    return {
        "local_record_count": int(row["n"] or 0),
        "local_earliest": row["earliest"],
        "local_latest": row["latest"],
    }


def _resolved_window(
    connection: sqlite3.Connection,
    table: str,
    expression: str,
    start: date | None,
    end: date | None,
) -> tuple[str | None, str | None]:
    end_text = end.isoformat() if end else None
    if end_text is None:
        row = connection.execute(f"SELECT MAX({expression}) AS value FROM {table}").fetchone()
        end_text = row["value"] if row else None
    start_text = start.isoformat() if start else None
    if start_text is None and end_text:
        start_text = (date.fromisoformat(str(end_text)[:10]) - timedelta(days=29)).isoformat()
    return start_text, end_text


def _query_elecheck(
    connection: sqlite3.Connection,
    args: QuerySeriesArgs,
) -> SeriesResult:
    config = DATASET_CATALOG[args.dataset]
    if args.dataset == "elecheck_mechanism":
        raise ValueError("增量机制快照请使用 market_analyze_elecheck_mechanism。")
    if args.dataset == "elecheck_spot":
        start, end = _resolved_window(
            connection,
            "elecheck_clear_price_records",
            "start_date",
            args.start_date,
            args.end_date,
        )
        metric = args.metric or config["metric"]
        params: list[Any] = [metric]
        filters = ["r.value IS NOT NULL", "r.metric=?"]
        if start:
            filters.append("r.start_date>=?")
            params.append(start)
        if end:
            filters.append("r.start_date<=?")
            params.append(end)
        if args.area:
            filters.append("(a.area_name=? OR r.area_code=?)")
            params.extend([args.area, args.area])
        rows = connection.execute(
            f"""
            SELECT
                CASE WHEN r.time96 IS NULL THEN CAST(r.start_date AS TEXT)
                     ELSE CAST(r.start_date AS TEXT)||'T'||r.time96 END AS period,
                r.value, COALESCE(a.area_name,r.area_code) AS area,
                CASE
                    WHEN ?='area' THEN COALESCE(a.area_name,r.area_code)
                    WHEN ?='metric' THEN r.metric
                    ELSE NULL
                END AS "group",
                r.unit,r.currency
            FROM elecheck_clear_price_records r
            LEFT JOIN elecheck_area_records a ON a.area_code=r.area_code
            WHERE {" AND ".join(filters)}
            ORDER BY r.start_date,r.time96,r.area_code
            """,
            [args.group_by, args.group_by, *params],
        ).fetchall()
        points, truncated = _aggregate_rows(
            [dict(row) for row in rows],
            aggregation=args.aggregation,
            statistic=args.statistic,
            group_by=args.group_by,
            limit=args.limit,
            equal_area_weight=args.area is None,
        )
        unit = _canonical_unit(rows[0]["unit"] if rows else None, config["unit"])
        currency = rows[0]["currency"] if rows else config["currency"]
    else:
        metric = args.metric or config["metric"]
        params = [metric]
        filters = ["value IS NOT NULL", "metric=?"]
        if args.start_date:
            filters.append("data_month>=?")
            params.append(args.start_date.strftime("%Y-%m"))
        if args.end_date:
            filters.append("data_month<=?")
            params.append(args.end_date.strftime("%Y-%m"))
        if args.area:
            filters.append("province_name=?")
            params.append(args.area)
        rows = connection.execute(
            f"""
            SELECT data_month AS period,value,province_name AS area,
                   CASE
                       WHEN ?='area' THEN province_name
                       WHEN ?='metric' THEN metric
                       ELSE NULL
                   END AS "group",
                   unit,NULL AS currency
            FROM elecheck_purchasing_records
            WHERE {" AND ".join(filters)}
            ORDER BY data_month,province_name
            """,
            [args.group_by, args.group_by, *params],
        ).fetchall()
        points, truncated = _aggregate_rows(
            [dict(row) for row in rows],
            aggregation="monthly",
            statistic=args.statistic,
            group_by=args.group_by,
            limit=args.limit,
            equal_area_weight=args.area is None,
        )
        unit = _canonical_unit(rows[0]["unit"] if rows else None, config["unit"])
        currency = config["currency"]
    warnings = []
    if truncated:
        warnings.append(f"结果超过 {args.limit} 点，已截断。")
    series = SeriesResult(
        source=SOURCE_LABELS["elecheck"],
        dataset=args.dataset,
        metric=metric,
        canonical_metric=config["canonical_metric"],
        area=args.area,
        time_basis=config["time_basis"],
        aggregation="monthly" if args.dataset == "elecheck_purchasing" else args.aggregation,
        unit=unit,
        currency=currency,
        points=points,
        warnings=warnings,
    )
    series.facts = _facts_for_series(series)
    return series


def _query_entsoe(
    connection: sqlite3.Connection,
    args: QuerySeriesArgs,
) -> SeriesResult:
    config = DATASET_CATALOG[args.dataset]
    metric = args.metric or config["metric"]
    start, end = _resolved_window(
        connection,
        "entsoe_records",
        "substr(interval_start_utc,1,10)",
        args.start_date,
        args.end_date,
    )
    params: list[Any] = [args.dataset]
    filters = ["dataset=?", "value IS NOT NULL"]
    if metric:
        filters.append("(value_field=? OR psr_type=?)")
        params.extend([metric, metric])
    if start:
        filters.append("substr(interval_start_utc,1,10)>=?")
        params.append(start)
    if end:
        filters.append("substr(interval_start_utc,1,10)<=?")
        params.append(end)
    if args.area:
        filters.append("(area=? OR in_domain=? OR out_domain=?)")
        params.extend([args.area, args.area, args.area])
    modern = [
        dict(row)
        for row in connection.execute(
            f"""
            SELECT interval_start_utc AS period,value,
                   COALESCE(area,
                       CASE WHEN in_domain IS NOT NULL OR out_domain IS NOT NULL
                            THEN COALESCE(out_domain,'')||'→'||COALESCE(in_domain,'')
                       END) AS area,
                   CASE
                       WHEN ?='area' THEN COALESCE(area,
                           COALESCE(out_domain,'')||'→'||COALESCE(in_domain,''))
                       WHEN ?='fuel_type' THEN psr_type
                       WHEN ?='metric' THEN COALESCE(psr_type,value_field)
                       ELSE NULL
                   END AS "group",
                   unit,currency,position
            FROM entsoe_records
            WHERE {" AND ".join(filters)}
            ORDER BY interval_start_utc,position
            """,
            [args.group_by, args.group_by, args.group_by, *params],
        ).fetchall()
    ]
    rows = modern
    if args.dataset == "entsoe_day_ahead_prices":
        legacy_params: list[Any] = []
        legacy_filters = [
            "source='ENTSO-E Transparency Platform'",
            "market='day_ahead'",
        ]
        if start:
            legacy_filters.append("trade_date>=?")
            legacy_params.append(start)
        if end:
            legacy_filters.append("trade_date<=?")
            legacy_params.append(end)
        if args.area:
            legacy_filters.append("region=?")
            legacy_params.append(args.area)
        modern_keys = {
            (str(row["period"])[:10], row["area"], row.get("position"))
            for row in modern
        }
        legacy = connection.execute(
            f"""
            SELECT CAST(trade_date AS TEXT)||'#'||metric AS period,value,
                   region AS area,
                   CASE WHEN ?='area' THEN region
                        WHEN ?='metric' THEN metric ELSE NULL END AS "group",
                   unit,currency,
                   CAST(substr(metric,-3) AS INTEGER) AS position
            FROM market_records
            WHERE {" AND ".join(legacy_filters)}
            ORDER BY trade_date,metric
            """,
            [args.group_by, args.group_by, *legacy_params],
        ).fetchall()
        for item in legacy:
            row = dict(item)
            key = (str(row["period"])[:10], row["area"], row["position"])
            if key not in modern_keys:
                rows.append(row)
    points, truncated = _aggregate_rows(
        rows,
        aggregation=args.aggregation,
        statistic=args.statistic,
        group_by=args.group_by,
        limit=args.limit,
    )
    unit = _canonical_unit(
        next((row["unit"] for row in rows if row.get("unit")), None),
        config["unit"],
    )
    currency = next(
        (row["currency"] for row in rows if row.get("currency")),
        config["currency"],
    )
    warnings = []
    if truncated:
        warnings.append(f"结果超过 {args.limit} 点，已截断。")
    if args.dataset == "entsoe_day_ahead_prices" and any(
        "#" in str(row["period"]) for row in rows
    ):
        warnings.append("结果兼容读取历史 market_records 日前价格记录。")
    series = SeriesResult(
        source=SOURCE_LABELS["entsoe"],
        dataset=args.dataset,
        metric=metric,
        canonical_metric=config["canonical_metric"],
        area=args.area,
        time_basis=config["time_basis"],
        aggregation=args.aggregation,
        unit=unit,
        currency=currency,
        points=points,
        warnings=warnings,
    )
    series.facts = _facts_for_series(series)
    return series


def _query_elexon(
    connection: sqlite3.Connection,
    args: QuerySeriesArgs,
) -> SeriesResult:
    config = DATASET_CATALOG[args.dataset]
    metric = args.metric or config["metric"]
    expression = (
        "COALESCE(settlement_date,substr(start_time_utc,1,10),"
        "substr(publish_time_utc,1,10))"
    )
    start, end = _resolved_window(
        connection,
        "elexon_records",
        expression,
        args.start_date,
        args.end_date,
    )
    params: list[Any] = [args.dataset, metric]
    filters = ["dataset=?", "metric=?", "value IS NOT NULL"]
    if start:
        filters.append(f"{expression}>=?")
        params.append(start)
    if end:
        filters.append(f"{expression}<=?")
        params.append(end)
    if args.area:
        filters.append("(area=? OR fuel_type=? OR bm_unit=?)")
        params.extend([args.area, args.area, args.area])
    rows = [
        dict(row)
        for row in connection.execute(
            f"""
            SELECT
                CASE
                    WHEN settlement_date IS NOT NULL THEN
                        settlement_date||COALESCE('#'||printf('%02d',settlement_period),'')
                    ELSE COALESCE(start_time_utc,publish_time_utc)
                END AS period,
                value,area,
                CASE
                    WHEN ?='area' THEN area
                    WHEN ?='fuel_type' THEN fuel_type
                    WHEN ?='metric' THEN metric
                    ELSE NULL
                END AS "group",
                unit,currency
            FROM elexon_records
            WHERE {" AND ".join(filters)}
            ORDER BY COALESCE(settlement_date,start_time_utc,publish_time_utc),
                     settlement_period,fuel_type
            """,
            [args.group_by, args.group_by, args.group_by, *params],
        ).fetchall()
    ]
    points, truncated = _aggregate_rows(
        rows,
        aggregation=args.aggregation,
        statistic=args.statistic,
        group_by=args.group_by,
        limit=args.limit,
    )
    warnings = [f"结果超过 {args.limit} 点，已截断。"] if truncated else []
    series = SeriesResult(
        source=SOURCE_LABELS["elexon"],
        dataset=args.dataset,
        metric=metric,
        canonical_metric=config["canonical_metric"],
        area=args.area,
        time_basis=config["time_basis"],
        aggregation=args.aggregation,
        unit=_canonical_unit(
            next((row["unit"] for row in rows if row.get("unit")), None),
            config["unit"],
        ),
        currency=next(
            (row["currency"] for row in rows if row.get("currency")),
            config["currency"],
        ),
        points=points,
        warnings=warnings,
    )
    series.facts = _facts_for_series(series)
    return series


def _gridstatus_column(
    connection: sqlite3.Connection,
    dataset: str,
    metric: str | None,
) -> tuple[str, str | None]:
    row = connection.execute(
        """
        SELECT status,is_published,all_columns_json
        FROM gridstatus_dataset_metadata WHERE dataset_id=?
        """,
        (dataset,),
    ).fetchone()
    if row is None:
        raise ValueError("Unknown GridStatus dataset; refresh the catalog first.")
    columns = json.loads(row["all_columns_json"] or "[]")
    numeric = [
        str(item.get("name") or item.get("column_name"))
        for item in columns
        if isinstance(item, dict)
        and (
            item.get("is_numeric") is True
            or str(item.get("data_type") or item.get("type") or "").lower()
            in {"number", "float", "double", "integer", "numeric", "decimal"}
        )
    ]
    selected = metric or (numeric[0] if numeric else None)
    if not selected or selected not in numeric:
        raise ValueError(
            "GridStatus metric must be a numeric column from local dataset metadata."
        )
    return selected, row["status"]


def _query_gridstatus(
    connection: sqlite3.Connection,
    args: QuerySeriesArgs,
) -> SeriesResult:
    metric, status = _gridstatus_column(connection, args.dataset, args.metric)
    start, end = _resolved_window(
        connection,
        "gridstatus_records",
        "substr(COALESCE(interval_start_utc,record_time_utc),1,10)",
        args.start_date,
        args.end_date,
    )
    params: list[Any] = [args.dataset]
    filters = ["dataset=?"]
    if start:
        filters.append("substr(COALESCE(interval_start_utc,record_time_utc),1,10)>=?")
        params.append(start)
    if end:
        filters.append("substr(COALESCE(interval_start_utc,record_time_utc),1,10)<=?")
        params.append(end)
    if args.area:
        filters.append("location=?")
        params.append(args.area)
    raw_rows = connection.execute(
        f"""
        SELECT COALESCE(interval_start_utc,record_time_utc) AS period,
               location,raw_json
        FROM gridstatus_records
        WHERE {" AND ".join(filters)}
        ORDER BY COALESCE(interval_start_utc,record_time_utc)
        """,
        params,
    ).fetchall()
    rows = []
    for row in raw_rows:
        raw = json.loads(row["raw_json"] or "{}")
        value = _safe_number(raw.get(metric))
        if value is None:
            continue
        rows.append(
            {
                "period": row["period"],
                "value": value,
                "area": row["location"],
                "group": (
                    row["location"]
                    if args.group_by == "area"
                    else metric if args.group_by == "metric" else None
                ),
            }
        )
    points, truncated = _aggregate_rows(
        rows,
        aggregation=args.aggregation,
        statistic=args.statistic,
        group_by=args.group_by,
        limit=args.limit,
    )
    warnings = ["GridStatus 字段单位以数据集元数据和原始接口说明为准。"]
    if status and status != "active":
        warnings.append(f"数据集当前状态为 {status}。")
    if truncated:
        warnings.append(f"结果超过 {args.limit} 点，已截断。")
    series = SeriesResult(
        source=SOURCE_LABELS["gridstatus"],
        dataset=args.dataset,
        metric=metric,
        canonical_metric=f"gridstatus:{args.dataset}:{metric}",
        area=args.area,
        time_basis="GridStatus UTC 时段",
        aggregation=args.aggregation,
        unit=None,
        currency=None,
        points=points,
        warnings=warnings,
    )
    series.facts = _facts_for_series(series)
    return series


def query_series_result(args: QuerySeriesArgs) -> SeriesResult:
    with connect_database() as connection:
        if args.source == "elecheck":
            return _query_elecheck(connection, args)
        if args.source == "entsoe":
            return _query_entsoe(connection, args)
        if args.source == "elexon":
            return _query_elexon(connection, args)
        if args.source == "gridstatus":
            return _query_gridstatus(connection, args)
    raise ValueError(f"Unsupported source: {args.source}")


def query_series(args: QuerySeriesArgs, _context: ToolContext) -> dict[str, Any]:
    series = query_series_result(args)
    return {
        "series": series.model_dump(mode="json"),
        "facts": [fact.model_dump(mode="json") for fact in series.facts],
        "completeness": _series_completeness(args, series),
        "warnings": series.warnings,
        "data_sources": [series.source],
    }


def _series_completeness(
    args: QuerySeriesArgs,
    series: SeriesResult,
) -> list[dict[str, Any]]:
    actual = len({point.period for point in series.points})
    expected = None
    ratio = None
    description = "来源粒度或市场日历不足，未虚构期望点数。"
    if (
        args.source == "elecheck"
        and args.dataset == "elecheck_spot"
        and args.aggregation == "daily"
        and args.group_by == "none"
        and args.start_date
        and args.end_date
    ):
        expected = (args.end_date - args.start_date).days + 1
        ratio = actual / expected if expected else None
        description = "按查询业务日期的自然日数量计算；缺失日期不补零。"
    return [
        {
            "name": f"{series.dataset} 结果覆盖",
            "actual": actual,
            "expected": expected,
            "ratio": ratio,
            "description": description,
        }
    ]


def compare_series(args: CompareSeriesArgs, _context: ToolContext) -> dict[str, Any]:
    series = [query_series_result(item) for item in args.series]
    first = series[0]
    attributes = {
        "canonical_metric": [item.canonical_metric for item in series],
        "unit": [item.unit for item in series],
        "currency": [item.currency for item in series],
        "aggregation": [item.aggregation for item in series],
        "time_basis": [item.time_basis for item in series],
    }
    reasons = [
        f"{name} 不一致：{', '.join(str(value) for value in values)}"
        for name, values in attributes.items()
        if len(set(values)) > 1
    ]
    comparable = not reasons
    differences: list[BusinessMetric] = []
    ranking: list[str] = []
    if comparable:
        averages = []
        for item in series:
            values = [
                float(point.value)
                for point in item.points
                if isinstance(point.value, (int, float))
            ]
            if values:
                averages.append((item, fmean(values)))
        if len(averages) != len(series):
            comparable = False
            reasons.append("至少一个序列没有有效数值。")
        else:
            base_value = averages[0][1]
            differences = [
                BusinessMetric(
                    name=f"{item.dataset} 相对 {first.dataset} 的均值差",
                    value=value - base_value,
                    unit=item.unit,
                    description="仅在严格口径一致时计算",
                )
                for item, value in averages[1:]
            ]
            ranking = [
                item.dataset
                for item, _value in sorted(
                    averages,
                    key=lambda pair: pair[1],
                    reverse=True,
                )
            ]
    result = ComparisonResult(
        comparable=comparable,
        status="comparable" if comparable else "side_by_side_only",
        comparison_basis=[
            f"{name}={values[0]}"
            for name, values in attributes.items()
            if len(set(values)) == 1
        ],
        blocking_reasons=reasons,
        series=series,
        differences=differences,
        ranking=ranking,
    )
    facts = [fact for item in series for fact in item.facts]
    warnings = [warning for item in series for warning in item.warnings]
    if not comparable:
        warnings.append("口径不一致，未计算跨来源差额或排名，仅并列展示。")
    return {
        "comparison": result.model_dump(mode="json"),
        "facts": [fact.model_dump(mode="json") for fact in facts],
        "completeness": [
            item
            for spec, result in zip(args.series, series, strict=True)
            for item in _series_completeness(spec, result)
        ],
        "warnings": warnings,
        "data_sources": sorted({item.source for item in series}),
        "comparison_status": result.status,
        "comparison_basis": result.comparison_basis,
    }


def analyze_spot(args: AnalyzeSpotArgs, _context: ToolContext) -> dict[str, Any]:
    metric_map = {
        "day_ahead": "avg_day_ahead_price",
        "real_time": "avg_real_time_price",
    }
    if args.metric != "spread":
        return query_series(
            QuerySeriesArgs(
                source="elecheck",
                dataset="elecheck_spot",
                metric=metric_map[args.metric],
                area=args.area,
                start_date=args.start_date,
                end_date=args.end_date,
                aggregation=args.aggregation,
                statistic="average",
                group_by="none",
            ),
            _context,
        )
    day_ahead = query_series_result(
        QuerySeriesArgs(
            source="elecheck",
            dataset="elecheck_spot",
            metric=metric_map["day_ahead"],
            area=args.area,
            start_date=args.start_date,
            end_date=args.end_date,
            aggregation=args.aggregation,
        )
    )
    real_time = query_series_result(
        QuerySeriesArgs(
            source="elecheck",
            dataset="elecheck_spot",
            metric=metric_map["real_time"],
            area=args.area,
            start_date=args.start_date,
            end_date=args.end_date,
            aggregation=args.aggregation,
        )
    )
    day_values = {point.period: point.value for point in day_ahead.points}
    points = [
        SeriesPoint(
            period=point.period,
            value=(
                float(point.value) - float(day_values[point.period])
                if point.value is not None
                and day_values.get(point.period) is not None
                else None
            ),
        )
        for point in real_time.points
        if point.period in day_values
    ]
    result = SeriesResult(
        source=SOURCE_LABELS["elecheck"],
        dataset="elecheck_spot",
        metric="real_time_minus_day_ahead",
        canonical_metric="spot_price_spread",
        area=args.area,
        time_basis=DATASET_CATALOG["elecheck_spot"]["time_basis"],
        aggregation=args.aggregation,
        unit=day_ahead.unit,
        currency=day_ahead.currency,
        points=points,
        warnings=["价差口径为实时均价减日前均价，只保留两者同时存在的日期。"],
    )
    result.facts = _facts_for_series(result)
    completeness_args = QuerySeriesArgs(
        source="elecheck",
        dataset="elecheck_spot",
        metric="avg_day_ahead_price",
        area=args.area,
        start_date=args.start_date,
        end_date=args.end_date,
        aggregation=args.aggregation,
    )
    return {
        "series": result.model_dump(mode="json"),
        "facts": [fact.model_dump(mode="json") for fact in result.facts],
        "completeness": _series_completeness(completeness_args, result),
        "warnings": result.warnings,
        "data_sources": [result.source],
    }


def analyze_purchasing(
    args: AnalyzePurchasingArgs,
    context: ToolContext,
) -> dict[str, Any]:
    start = date.fromisoformat(f"{args.start_month}-01") if args.start_month else None
    end = date.fromisoformat(f"{args.end_month}-01") if args.end_month else None
    return query_series(
        QuerySeriesArgs(
            source="elecheck",
            dataset="elecheck_purchasing",
            metric=args.metric,
            area=args.province,
            start_date=start,
            end_date=end,
            aggregation="monthly",
            statistic="average",
            group_by="none" if args.province else "area",
        ),
        context,
    )


def analyze_mechanism(
    args: AnalyzeMechanismArgs,
    _context: ToolContext,
) -> dict[str, Any]:
    params: list[Any] = []
    where = ["price IS NOT NULL"]
    if args.region:
        where.append("(region_name=? OR region=?)")
        params.extend([args.region, args.region])
    with connect_database() as connection:
        rows = connection.execute(
            f"""
            SELECT region_name,region,category,price,clear_price,unit,collected_at
            FROM elecheck_mechanism_electricity_price_records
            WHERE {" AND ".join(where)}
            ORDER BY region_name,category LIMIT ?
            """,
            [*params, args.limit],
        ).fetchall()
    results = []
    facts = []
    for index, row in enumerate(rows):
        item = dict(row)
        if item["price"] is not None and item["clear_price"] is not None:
            item["difference"] = item["price"] - item["clear_price"]
        results.append(item)
        facts.append(
            GroundedFact(
                fact_id=f"elecheck_mechanism_{index}",
                label=f"{row['region_name']} {row['category']}机制电价",
                value=row["price"],
                unit=row["unit"],
                source=SOURCE_LABELS["elecheck"],
                dataset="elecheck_mechanism",
                time_basis="数据库最新快照",
                calculation="直接读取最新快照记录",
                coverage=str(row["collected_at"]),
            ).model_dump(mode="json")
        )
    return {
        "records": results,
        "facts": facts,
        "warnings": ["增量机制只分析数据库中的当前快照，不推断历史趋势。"],
        "data_sources": [SOURCE_LABELS["elecheck"]],
    }


def search_gzpec_news(args: SearchNewsArgs, _context: ToolContext) -> dict[str, Any]:
    filters = ["1=1"]
    params: list[Any] = []
    if args.keyword:
        filters.append("(title LIKE ? OR content_json LIKE ?)")
        pattern = f"%{args.keyword}%"
        params.extend([pattern, pattern])
    if args.category:
        filters.append("category=?")
        params.append(args.category)
    if args.news_type:
        filters.append("news_type=?")
        params.append(args.news_type)
    if args.start_date:
        filters.append("publish_date>=?")
        params.append(args.start_date.isoformat())
    if args.end_date:
        filters.append("publish_date<=?")
        params.append(args.end_date.isoformat())
    with connect_database() as connection:
        rows = connection.execute(
            f"""
            SELECT title,url,publish_date,category,news_type,collected_at
            FROM gzpec_news_records WHERE {" AND ".join(filters)}
            ORDER BY publish_date DESC,id DESC LIMIT ?
            """,
            [*params, args.limit],
        ).fetchall()
    records = [dict(row) for row in rows]
    facts = [
        GroundedFact(
            fact_id="gzpec_search_count",
            label="匹配公开信息数量",
            value=len(records),
            unit="篇",
            source=SOURCE_LABELS["gzpec"],
            dataset="gzpec_news",
            time_basis="页面发布日期",
            calculation="受控条件筛选后的返回记录数",
            coverage=(
                f"{records[-1]['publish_date']} 至 {records[0]['publish_date']}"
                if records
                else "无匹配记录"
            ),
        ).model_dump(mode="json")
    ]
    return {
        "records": records,
        "facts": facts,
        "warnings": [],
        "data_sources": [SOURCE_LABELS["gzpec"]],
    }


def get_gzpec_article(args: GetNewsArticleArgs, _context: ToolContext) -> dict[str, Any]:
    with connect_database() as connection:
        row = connection.execute(
            """
            SELECT title,url,publish_date,category,news_type,content_json,collected_at
            FROM gzpec_news_records WHERE url=?
            """,
            (args.url,),
        ).fetchone()
    if row is None:
        raise ValueError("本地数据库中不存在该 GZPEC 文章 URL。")
    content = json.loads(row["content_json"] or "[]")
    return {
        "article": {
            "title": row["title"],
            "url": row["url"],
            "publish_date": row["publish_date"],
            "category": row["category"],
            "news_type": row["news_type"],
            "content_blocks": content,
            "collected_at": row["collected_at"],
        },
        "facts": [],
        "warnings": ["正文来自本地已采集页面，不代表网站当前版本。"],
        "data_sources": [SOURCE_LABELS["gzpec"]],
    }


def export_result(args: ExportResultArgs, context: ToolContext) -> dict[str, Any]:
    output_dir = project_root() / "exports" / "market_agent" / context.session_id
    output_dir.mkdir(parents=True, exist_ok=True)
    results = [query_series_result(item) for item in args.series]
    stem = f"market_result_{context.run_id[:8]}_{context.tool_call_id[-8:]}"
    generated: list[str] = []
    if "csv" in args.formats:
        path = output_dir / f"{stem}.csv"
        with path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(
                [
                    "source",
                    "dataset",
                    "metric",
                    "area",
                    "period",
                    "group",
                    "value",
                    "unit",
                    "currency",
                    "time_basis",
                ]
            )
            for result in results:
                for point in result.points:
                    writer.writerow(
                        [
                            result.source,
                            result.dataset,
                            result.metric,
                            result.area or "",
                            point.period,
                            point.group or "",
                            point.value,
                            result.unit or "",
                            result.currency or "",
                            result.time_basis,
                        ]
                    )
        generated.append(str(path.resolve()))
    if "png" in args.formats:
        import matplotlib

        matplotlib.use("Agg")
        from matplotlib import pyplot as plt

        figure, axis = plt.subplots(figsize=(10, 5))
        plotted = False
        for result in results:
            numeric = [
                (point.period, point.value)
                for point in result.points
                if isinstance(point.value, (int, float))
            ]
            if not numeric:
                continue
            axis.plot(
                [period for period, _value in numeric],
                [value for _period, value in numeric],
                marker="o",
                linewidth=1.5,
                label=f"{result.source} · {result.dataset}",
            )
            plotted = True
        if not plotted:
            plt.close(figure)
            raise ValueError("没有可绘制的数值时间序列。")
        axis.set_title("多数据源电力市场分析")
        axis.grid(alpha=0.25)
        axis.legend()
        axis.tick_params(axis="x", rotation=45)
        figure.tight_layout()
        path = output_dir / f"{stem}.png"
        figure.savefig(path, dpi=160)
        plt.close(figure)
        generated.append(str(path.resolve()))
    return {
        "generated_files": generated,
        "executed_actions": ["market_export_result"],
        "facts": [
            fact.model_dump(mode="json")
            for result in results
            for fact in result.facts
        ],
        "warnings": [],
        "data_sources": sorted({result.source for result in results}),
    }


def _collection_result(
    *,
    source: str,
    dataset: str,
    written: int,
    produced: int,
    request_count: int,
) -> dict[str, Any]:
    return {
        "source": SOURCE_LABELS[source],
        "dataset": dataset,
        "request_count": request_count,
        "records_produced": produced,
        "records_written": written,
        "records_skipped": max(produced - written, 0),
        "failed_requests": 0,
        "facts": [
            GroundedFact(
                fact_id=f"{source}_{dataset}_written",
                label="实际写入记录数",
                value=written,
                unit="条",
                source=SOURCE_LABELS[source],
                dataset=dataset,
                time_basis="本次采集运行",
                calculation="upsert 返回写入数量",
            ).model_dump(mode="json")
        ],
        "warnings": [],
        "data_sources": [SOURCE_LABELS[source]],
        "executed_actions": [f"collect:{source}:{dataset}"],
    }


def collection_preview(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    preview = {
        "source": tool_name.removeprefix("market_collect_"),
        "dataset": arguments.get("dataset") or arguments.get("category") or "news",
        "area": (
            arguments.get("area")
            or arguments.get("location")
            or arguments.get("in_area")
        ),
        "start": arguments.get("start_date") or arguments.get("start_month"),
        "end": arguments.get("end_date") or arguments.get("end_month"),
        "estimated_requests": 1,
    }
    if tool_name == "market_collect_elecheck":
        if arguments.get("category") == "spot":
            start = date.fromisoformat(str(arguments["start_date"]))
            end = date.fromisoformat(str(arguments["end_date"]))
            preview["estimated_requests"] = ((end - start).days + 1) * 2
        elif arguments.get("category") == "purchasing":
            preview["estimated_requests"] = (
                2
                if arguments.get("area")
                else _month_count(
                    str(arguments["start_month"]),
                    str(arguments["end_month"]),
                )
            )
    elif tool_name == "market_collect_elexon":
        start = date.fromisoformat(str(arguments["start_date"]))
        end = date.fromisoformat(str(arguments["end_date"]))
        days = (end - start).days + 1
        config = get_elexon_request_config(str(arguments["dataset"]))
        if config["time_mode"] == "settlement_date_path":
            preview["estimated_requests"] = days
        else:
            preview["estimated_requests"] = math.ceil(
                days / int(config.get("chunk_days", days))
            )
    elif tool_name == "market_collect_gzpec":
        preview["estimated_requests"] = "至少20次索引页请求，并逐篇读取详情"
    return preview


def collect_elecheck(args: CollectElecheckArgs, context: ToolContext) -> dict[str, Any]:
    context.report_progress(
        {"source": "elecheck", "category": args.category, "stage": "starting"}
    )
    if args.category == "spot":
        spider = "elecheck_clear_price"
        kwargs = {
            "area_code": resolve_elecheck_area_code(args.area or ""),
            "start_date": args.start_date.isoformat() if args.start_date else None,
            "end_date": args.end_date.isoformat() if args.end_date else None,
            "daily": True,
        }
        requests = ((args.end_date - args.start_date).days + 1) * 2
    elif args.category == "purchasing":
        if args.area:
            spider = "elecheck_purchasing_province_month"
            kwargs = {
                "province_name": args.area,
                "start_month": args.start_month,
                "end_month": args.end_month,
            }
        else:
            spider = "elecheck_purchasing_national_range"
            kwargs = {
                "start_month": args.start_month,
                "end_month": args.end_month,
            }
        requests = (
            2
            if args.area
            else _month_count(args.start_month or "", args.end_month or "")
        )
    else:
        spider = "elecheck_mechanism_electricity_price"
        kwargs = {}
        requests = 1
    written, produced = run_spider_and_upsert(spider, kwargs)
    context.report_progress(
        {
            "source": "elecheck",
            "category": args.category,
            "stage": "completed",
            "records_written": written,
        }
    )
    return _collection_result(
        source="elecheck",
        dataset=args.category,
        written=written,
        produced=produced,
        request_count=requests,
    )


def collect_entsoe(args: CollectEntsoeArgs, context: ToolContext) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "start_date": args.start_date.isoformat(),
        "end_date": (args.end_date + timedelta(days=1)).isoformat(),
    }
    if args.dataset == "entsoe_cross_border_physical_flows":
        kwargs.update(
            {
                "in_area": args.in_area.upper() if args.in_area else None,
                "out_area": args.out_area.upper() if args.out_area else None,
            }
        )
    else:
        kwargs["area"] = args.area.upper() if args.area else None
    if args.psr_type:
        kwargs["psr_type"] = args.psr_type
    context.report_progress(
        {"source": "entsoe", "dataset": args.dataset, "stage": "starting"}
    )
    written, produced = run_spider_and_upsert(args.dataset, kwargs)
    return _collection_result(
        source="entsoe",
        dataset=args.dataset,
        written=written,
        produced=produced,
        request_count=1,
    )


def collect_elexon(args: CollectElexonArgs, context: ToolContext) -> dict[str, Any]:
    extra = (
        {args.filter_name: args.filter_value}
        if args.filter_name and args.filter_value
        else {}
    )
    context.report_progress(
        {"source": "elexon", "dataset": args.dataset, "stage": "starting"}
    )
    written, produced = run_spider_and_upsert(
        args.dataset,
        {
            "start_date": args.start_date.isoformat(),
            "end_date": (args.end_date + timedelta(days=1)).isoformat(),
            "extra_params": extra,
        },
    )
    days = (args.end_date - args.start_date).days + 1
    config = get_elexon_request_config(args.dataset)
    requests = (
        days
        if config["time_mode"] == "settlement_date_path"
        else math.ceil(days / int(config.get("chunk_days", days)))
    )
    return _collection_result(
        source="elexon",
        dataset=args.dataset,
        written=written,
        produced=produced,
        request_count=requests,
    )


def _gridstatus_dataset_is_allowed(dataset: str) -> None:
    with connect_database() as connection:
        row = connection.execute(
            """
            SELECT status,is_published FROM gridstatus_dataset_metadata
            WHERE dataset_id=?
            """,
            (dataset,),
        ).fetchone()
    if row is None:
        raise ValueError("Unknown GridStatus dataset; refresh catalog first.")
    if row["status"] not in {None, "active"} or not bool(row["is_published"]):
        raise ValueError("GridStatus dataset must be active and published.")


def collect_gridstatus(
    args: CollectGridStatusArgs,
    context: ToolContext,
) -> dict[str, Any]:
    if args.operation == "refresh_catalog":
        config = {
            "name": "market_agent_gridstatus_catalog",
            "type": "datasets",
            "params": {"limit": 1000},
        }
        dataset = "dataset_catalog"
    else:
        _gridstatus_dataset_is_allowed(args.dataset or "")
        params = {
            "start_time": f"{args.start_date.isoformat()}T00:00Z",
            "end_time": f"{(args.end_date + timedelta(days=1)).isoformat()}T00:00Z",
            "return_format": "csv",
            "limit": args.limit,
        }
        config = {
            "name": (
                f"market_agent_gridstatus_{args.dataset}_"
                f"{args.location or 'all'}"
            ),
            "type": "dataset_location_query" if args.location else "dataset_query",
            "dataset": args.dataset,
            "params": params,
        }
        if args.location:
            config["location"] = args.location
        dataset = args.dataset or ""
    context.report_progress(
        {"source": "gridstatus", "dataset": dataset, "stage": "starting"}
    )
    spider = GridStatusSpider(config)
    try:
        records = list(spider.crawl())
    finally:
        spider.close()
    if args.operation == "refresh_catalog":
        written = upsert_gridstatus_dataset_metadata_records(records)
    else:
        written = upsert_gridstatus_records(records)
    return _collection_result(
        source="gridstatus",
        dataset=dataset,
        written=written,
        produced=len(records),
        request_count=1,
    )


def collect_gzpec(_args: CollectGzpecArgs, context: ToolContext) -> dict[str, Any]:
    context.report_progress({"source": "gzpec", "stage": "starting"})
    written, produced = run_spider_and_upsert("gzpec-news-combined", {})
    return _collection_result(
        source="gzpec",
        dataset="gzpec_news",
        written=written,
        produced=produced,
        request_count=20 + produced,
    )


def build_market_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for definition in (
        ToolDefinition(
            name="market_get_data_overview",
            description="查看五类来源的本地数据量、业务日期范围、采集时间和凭据状态。",
            args_model=DataOverviewArgs,
            handler=data_overview,
        ),
        ToolDefinition(
            name="market_list_datasets",
            description="列出受控数据集目录和 GridStatus 本地元数据。",
            args_model=ListDatasetsArgs,
            handler=list_datasets,
        ),
        ToolDefinition(
            name="market_query_series",
            description="按来源、数据集、指标、地区和日期查询受控数值序列。",
            args_model=QuerySeriesArgs,
            handler=query_series,
        ),
        ToolDefinition(
            name="market_compare_series",
            description="严格检查口径后比较二至四组序列；不兼容时只并列展示。",
            args_model=CompareSeriesArgs,
            handler=compare_series,
        ),
        ToolDefinition(
            name="market_analyze_elecheck_spot",
            description="分析 Elecheck 日前价、实时价或实时减日前价差。",
            args_model=AnalyzeSpotArgs,
            handler=analyze_spot,
        ),
        ToolDefinition(
            name="market_analyze_elecheck_purchasing",
            description="分析 Elecheck 代理购电价格和费用构成。",
            args_model=AnalyzePurchasingArgs,
            handler=analyze_purchasing,
        ),
        ToolDefinition(
            name="market_analyze_elecheck_mechanism",
            description="读取 Elecheck 增量机制电价最新快照。",
            args_model=AnalyzeMechanismArgs,
            handler=analyze_mechanism,
        ),
        ToolDefinition(
            name="market_search_gzpec_news",
            description="按关键词、类别、类型和日期检索广州电力交易中心公开信息。",
            args_model=SearchNewsArgs,
            handler=search_gzpec_news,
        ),
        ToolDefinition(
            name="market_get_gzpec_article",
            description="按本地已收录 URL 读取广州电力交易中心文章正文。",
            args_model=GetNewsArticleArgs,
            handler=get_gzpec_article,
        ),
        ToolDefinition(
            name="market_export_result",
            description="把受控查询或比较结果导出到预设目录的 CSV/PNG。",
            args_model=ExportResultArgs,
            handler=export_result,
            side_effect="只在 exports/market_agent/<session_id>/ 创建导出文件。",
        ),
        ToolDefinition(
            name="market_collect_elecheck",
            description="采集受控范围内的 Elecheck 现货、代理购电或增量机制数据。",
            args_model=CollectElecheckArgs,
            handler=collect_elecheck,
            risk_level=RiskLevel.APPROVAL,
            side_effect="向 Elecheck 业务表写入或更新采集结果。",
        ),
        ToolDefinition(
            name="market_collect_entsoe",
            description="采集四个精选 ENTSO-E 数据集，结束日期按业务日期包含。",
            args_model=CollectEntsoeArgs,
            handler=collect_entsoe,
            risk_level=RiskLevel.APPROVAL,
            side_effect="向 ENTSO-E 业务表写入或更新采集结果。",
        ),
        ToolDefinition(
            name="market_collect_elexon",
            description="采集五个精选 Elexon 数据集，结束日期按业务日期包含。",
            args_model=CollectElexonArgs,
            handler=collect_elexon,
            risk_level=RiskLevel.APPROVAL,
            side_effect="向 Elexon 业务表写入或更新采集结果。",
        ),
        ToolDefinition(
            name="market_collect_gridstatus",
            description="刷新 GridStatus 目录或采集最长七天、最多一千行的数据。",
            args_model=CollectGridStatusArgs,
            handler=collect_gridstatus,
            risk_level=RiskLevel.APPROVAL,
            side_effect="向 GridStatus 元数据或业务表写入或更新采集结果。",
        ),
        ToolDefinition(
            name="market_collect_gzpec",
            description="运行固定的广州电力交易中心组合新闻采集。",
            args_model=CollectGzpecArgs,
            handler=collect_gzpec,
            risk_level=RiskLevel.APPROVAL,
            side_effect="向广州电力交易中心公开信息表写入或更新采集结果。",
        ),
    ):
        registry.register(definition)
    return registry
