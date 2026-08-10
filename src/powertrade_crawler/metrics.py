from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, text

from powertrade_crawler.storage import DashboardDailyMetricRow, get_engine, get_session


@dataclass(frozen=True)
class MetricPoint:
    metric_date: date
    source: str
    dataset: str
    category: str
    region: str
    dimension: str
    metric_name: str
    unit: str | None
    value: float
    latest_raw_time: str | None


@dataclass
class MetricAccumulator:
    count: int = 0
    total: float = 0.0
    minimum: float | None = None
    maximum: float | None = None
    latest_raw_time: str | None = None

    def add(self, point: MetricPoint) -> None:
        self.count += 1
        self.total += point.value
        self.minimum = point.value if self.minimum is None else min(self.minimum, point.value)
        self.maximum = point.value if self.maximum is None else max(self.maximum, point.value)
        if point.latest_raw_time and (
            self.latest_raw_time is None or point.latest_raw_time > self.latest_raw_time
        ):
            self.latest_raw_time = point.latest_raw_time

    @property
    def average(self) -> float | None:
        if self.count == 0:
            return None
        return self.total / self.count

    @property
    def spread(self) -> float | None:
        if self.minimum is None or self.maximum is None:
            return None
        return self.maximum - self.minimum


def rebuild_dashboard_daily_metrics(start_date: date, end_date: date) -> int:
    """Rebuild dashboard metrics for [start_date, end_date)."""
    if end_date <= start_date:
        raise ValueError("end_date must be later than start_date.")

    points = list(iter_metric_points(start_date, end_date))
    grouped: dict[tuple[Any, ...], MetricAccumulator] = {}
    for point in points:
        key = (
            point.metric_date,
            point.source,
            point.dataset,
            point.category,
            point.region,
            point.dimension,
            point.metric_name,
            point.unit,
        )
        grouped.setdefault(key, MetricAccumulator()).add(point)

    rebuilt_at = utc_now_naive()
    with get_session() as session:
        session.execute(
            delete(DashboardDailyMetricRow).where(
                DashboardDailyMetricRow.metric_date >= start_date,
                DashboardDailyMetricRow.metric_date < end_date,
            )
        )
        for key, accumulator in grouped.items():
            (
                metric_date,
                source,
                dataset,
                category,
                region,
                dimension,
                metric_name,
                unit,
            ) = key
            session.add(
                DashboardDailyMetricRow(
                    metric_date=metric_date,
                    source=source,
                    dataset=dataset,
                    category=category,
                    region=region,
                    dimension=dimension,
                    metric_name=metric_name,
                    unit=unit,
                    record_count=accumulator.count,
                    avg_value=accumulator.average,
                    min_value=accumulator.minimum,
                    max_value=accumulator.maximum,
                    sum_value=accumulator.total,
                    spread_value=accumulator.spread,
                    latest_raw_time=accumulator.latest_raw_time,
                    rebuilt_at=rebuilt_at,
                )
            )
        session.commit()
    return len(grouped)


def iter_metric_points(start_date: date, end_date: date):
    yield from iter_market_points(start_date, end_date)
    yield from iter_entsoe_points(start_date, end_date)
    yield from iter_elexon_points(start_date, end_date)
    yield from iter_gridstatus_points(start_date, end_date)
    yield from iter_elecheck_clear_price_points(start_date, end_date)


def iter_market_points(start_date: date, end_date: date):
    query = """
        SELECT source, market, region, trade_date, metric, value, unit, collected_at
        FROM market_records
        WHERE trade_date >= :start_date AND trade_date < :end_date
    """
    for row in fetch_rows_if_table_exists("market_records", query, start_date, end_date):
        value = parse_float(row["value"])
        metric_date = parse_date(row["trade_date"])
        if value is None or metric_date is None:
            continue
        source = str(row["source"] or "Market")
        dataset = resolve_market_dataset(source, str(row["market"] or "market_records"))
        metric_name = str(row["metric"] or "value")
        yield MetricPoint(
            metric_date=metric_date,
            source=source,
            dataset=dataset,
            category=resolve_metric_category(metric_name, str(row["market"] or "")),
            region=str(row["region"] or "unknown"),
            dimension=metric_name,
            metric_name="value",
            unit=optional_str(row["unit"]),
            value=value,
            latest_raw_time=optional_str(row["collected_at"]),
        )


def iter_entsoe_points(start_date: date, end_date: date):
    query = """
        SELECT dataset, category, area, in_domain, out_domain, psr_type,
               interval_start_utc, value, value_field, unit, collected_at
        FROM entsoe_records
        WHERE interval_start_utc >= :start_text AND interval_start_utc < :end_text
              AND value IS NOT NULL
    """
    for row in fetch_rows_if_table_exists("entsoe_records", query, start_date, end_date):
        value = parse_float(row["value"])
        metric_date = parse_date_from_text(row["interval_start_utc"])
        if value is None or metric_date is None:
            continue
        region = optional_str(row["area"]) or border_label(row["in_domain"], row["out_domain"])
        value_field = optional_str(row["value_field"]) or "value"
        yield MetricPoint(
            metric_date=metric_date,
            source="ENTSO-E",
            dataset=str(row["dataset"]),
            category=str(row["category"] or "entsoe"),
            region=region,
            dimension=optional_str(row["psr_type"]) or value_field,
            metric_name=value_field,
            unit=optional_str(row["unit"]),
            value=value,
            latest_raw_time=optional_str(row["interval_start_utc"]),
        )


def iter_elexon_points(start_date: date, end_date: date):
    query = """
        SELECT dataset, category, area, settlement_date, start_time_utc, fuel_type,
               bm_unit, national_grid_bm_unit, metric, value, unit, collected_at
        FROM elexon_records
        WHERE (
            (start_time_utc >= :start_text AND start_time_utc < :end_text)
            OR (start_time_utc IS NULL AND settlement_date >= :start_date AND settlement_date < :end_date)
        )
        AND value IS NOT NULL
    """
    for row in fetch_rows_if_table_exists("elexon_records", query, start_date, end_date):
        value = parse_float(row["value"])
        metric_date = parse_date_from_text(row["start_time_utc"]) or parse_date(
            row["settlement_date"]
        )
        if value is None or metric_date is None:
            continue
        yield MetricPoint(
            metric_date=metric_date,
            source="Elexon",
            dataset=str(row["dataset"]),
            category=str(row["category"] or "elexon"),
            region=str(row["area"] or "GB"),
            dimension=first_text(
                row["fuel_type"],
                row["bm_unit"],
                row["national_grid_bm_unit"],
                row["metric"],
            ),
            metric_name=str(row["metric"] or "value"),
            unit=optional_str(row["unit"]),
            value=value,
            latest_raw_time=optional_str(row["start_time_utc"])
            or optional_str(row["settlement_date"]),
        )


def iter_gridstatus_points(start_date: date, end_date: date):
    query = """
        SELECT request_name, request_type, dataset, location, interval_start_utc,
               record_time_utc, raw_json, collected_at
        FROM gridstatus_records
        WHERE (
            (interval_start_utc >= :start_text AND interval_start_utc < :end_text)
            OR (interval_start_utc IS NULL AND record_time_utc >= :start_text AND record_time_utc < :end_text)
        )
    """
    for row in fetch_rows_if_table_exists("gridstatus_records", query, start_date, end_date):
        time_text = optional_str(row["interval_start_utc"]) or optional_str(row["record_time_utc"])
        metric_date = parse_date_from_text(time_text)
        if metric_date is None:
            continue
        raw = parse_json_dict(row["raw_json"])
        for metric_name, value in iter_numeric_raw_fields(raw):
            yield MetricPoint(
                metric_date=metric_date,
                source="GridStatus",
                dataset=optional_str(row["dataset"]) or str(row["request_name"]),
                category=str(row["request_type"] or "gridstatus"),
                region=optional_str(row["location"]) or "all",
                dimension=metric_name,
                metric_name=metric_name,
                unit=None,
                value=value,
                latest_raw_time=time_text,
            )


def iter_elecheck_clear_price_points(start_date: date, end_date: date):
    query = """
        SELECT source, endpoint, area_code, start_date, time96, metric, value, unit, collected_at
        FROM elecheck_clear_price_records
        WHERE start_date >= :start_date AND start_date < :end_date
              AND value IS NOT NULL
    """
    for row in fetch_rows_if_table_exists(
        "elecheck_clear_price_records",
        query,
        start_date,
        end_date,
    ):
        value = parse_float(row["value"])
        metric_date = parse_date(row["start_date"])
        if value is None or metric_date is None:
            continue
        yield MetricPoint(
            metric_date=metric_date,
            source=str(row["source"] or "易能电易查"),
            dataset="elecheck_clear_price",
            category="price",
            region=str(row["area_code"] or "unknown"),
            dimension=optional_str(row["time96"]) or str(row["endpoint"] or "detail"),
            metric_name=str(row["metric"] or "value"),
            unit=optional_str(row["unit"]),
            value=value,
            latest_raw_time=optional_str(row["collected_at"]),
        )


def fetch_rows_if_table_exists(table_name: str, query: str, start_date: date, end_date: date):
    engine = get_engine()
    with engine.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :table_name"),
            {"table_name": table_name},
        ).first()
        if not exists:
            return []
        return list(
            connection.execute(
                text(query),
                {
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "start_text": f"{start_date.isoformat()}T00:00",
                    "end_text": f"{end_date.isoformat()}T00:00",
                },
            ).mappings()
        )


def list_dashboard_overview() -> list[dict[str, Any]]:
    raw_sources = [
        ("ENTSO-E", "entsoe_records", "collected_at"),
        ("Elexon", "elexon_records", "collected_at"),
        ("GridStatus", "gridstatus_records", "collected_at"),
        ("Elecheck 现货价格", "elecheck_clear_price_records", "collected_at"),
        ("Elecheck 代理购电", "elecheck_purchasing_records", "collected_at"),
        ("Elecheck 机制电价", "elecheck_mechanism_electricity_price_records", "collected_at"),
        ("GZPEC 新闻", "gzpec_news_records", "collected_at"),
    ]
    rows = []
    engine = get_engine()
    with engine.connect() as connection:
        existing_tables = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'table'")
            )
        }
        for source, table_name, time_column in raw_sources:
            if table_name not in existing_tables:
                rows.append({"source": source, "table": table_name, "record_count": 0, "latest": ""})
                continue
            result = connection.execute(
                text(
                    f"SELECT COUNT(*) AS record_count, MAX({time_column}) AS latest FROM {table_name}"
                )
            ).mappings().first()
            rows.append(
                {
                    "source": source,
                    "table": table_name,
                    "record_count": int(result["record_count"] or 0),
                    "latest": result["latest"] or "",
                }
            )

        metric_result = connection.execute(
            text(
                "SELECT COUNT(*) AS record_count, MAX(rebuilt_at) AS latest "
                "FROM dashboard_daily_metrics"
            )
        ).mappings().first()
        rows.append(
            {
                "source": "指标汇总",
                "table": "dashboard_daily_metrics",
                "record_count": int(metric_result["record_count"] or 0),
                "latest": metric_result["latest"] or "",
            }
        )
    return rows


def list_dashboard_series(
    *,
    topic: str,
    start_date: date,
    end_date: date,
    limit: int = 8,
) -> list[dict[str, Any]]:
    rows = list_dashboard_metric_rows(topic=topic, start_date=start_date, end_date=end_date)
    labels_by_weight: dict[str, int] = {}
    for row in rows:
        label = dashboard_label(row)
        labels_by_weight[label] = labels_by_weight.get(label, 0) + int(row["record_count"] or 0)
    selected_labels = {
        label
        for label, _weight in sorted(
            labels_by_weight.items(),
            key=lambda item: (-item[1], item[0]),
        )[:limit]
    }
    return [
        {
            "metric_date": row["metric_date"],
            "label": dashboard_label(row),
            "avg_value": row["avg_value"],
            "record_count": row["record_count"],
            "unit": row["unit"],
            "source": row["source"],
            "category": row["category"],
        }
        for row in rows
        if dashboard_label(row) in selected_labels
    ]


def list_dashboard_metric_rows(
    *,
    topic: str,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    filters = ["metric_date >= :start_date", "metric_date < :end_date"]
    params = {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()}
    if topic == "price":
        filters.append(
            "(category IN ('price', 'market') OR metric_name LIKE '%price%' "
            "OR unit LIKE '%EUR%' OR unit LIKE '%GBP%' OR unit LIKE '%CNY%')"
        )
        filters.append("metric_name NOT LIKE '%count%'")
    elif topic == "load":
        filters.append(
            "(category = 'load' OR dataset LIKE '%load%' OR metric_name LIKE '%demand%')"
        )
    elif topic == "generation":
        filters.append(
            "(category = 'generation' OR dataset LIKE '%generation%' "
            "OR metric_name LIKE '%generation%')"
        )
    elif topic == "interconnector":
        filters.append(
            "(category IN ('transmission', 'interconnector') OR dataset LIKE '%flow%' "
            "OR dataset LIKE '%interconnector%')"
        )

    query = f"""
        SELECT metric_date, source, dataset, category, region, dimension, metric_name, unit,
               record_count, avg_value, min_value, max_value, sum_value, spread_value,
               latest_raw_time, rebuilt_at
        FROM dashboard_daily_metrics
        WHERE {' AND '.join(filters)}
        ORDER BY metric_date, source, dataset, region, metric_name
    """
    with get_engine().connect() as connection:
        return [dict(row) for row in connection.execute(text(query), params).mappings()]


def export_dashboard_metric_rows(
    *,
    output_path: Path,
    topic: str,
    start_date: date,
    end_date: date,
) -> int:
    rows = list_dashboard_metric_rows(topic=topic, start_date=start_date, end_date=end_date)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "metric_date",
        "source",
        "dataset",
        "category",
        "region",
        "dimension",
        "metric_name",
        "unit",
        "record_count",
        "avg_value",
        "min_value",
        "max_value",
        "sum_value",
        "spread_value",
        "latest_raw_time",
        "rebuilt_at",
    ]
    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def dashboard_label(row: dict[str, Any]) -> str:
    dimension = str(row.get("dimension") or "")
    metric_name = str(row.get("metric_name") or "")
    detail = (
        metric_name
        if metric_name and dimension.lower() in {"", "metric", "statistics", "summary"}
        else dimension or metric_name
    )
    parts = [
        str(row.get("source") or ""),
        str(row.get("dataset") or ""),
        str(row.get("region") or ""),
        detail,
    ]
    return " | ".join(part for part in parts if part)


def run_database_maintenance(*, analyze: bool, vacuum: bool) -> list[str]:
    if not analyze and not vacuum:
        raise ValueError("At least one of analyze or vacuum must be enabled.")
    statements = []
    engine = get_engine()
    with engine.connect() as connection:
        if analyze:
            connection.execute(text("ANALYZE"))
            statements.append("ANALYZE")
        if vacuum:
            raw_connection = connection.connection
            isolation_level = getattr(raw_connection, "isolation_level", None)
            raw_connection.isolation_level = None
            raw_connection.execute("VACUUM")
            raw_connection.isolation_level = isolation_level
            statements.append("VACUUM")
    return statements


def default_metric_window(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    return today - timedelta(days=30), today


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def resolve_market_dataset(source: str, market: str) -> str:
    if source == "ENTSO-E Transparency Platform" and market == "day_ahead":
        return "entsoe_day_ahead_prices"
    return market or "market_records"


def resolve_metric_category(metric_name: str, fallback: str) -> str:
    lowered = f"{metric_name} {fallback}".lower()
    if "price" in lowered:
        return "price"
    if "load" in lowered or "demand" in lowered:
        return "load"
    if "generation" in lowered or "fuel" in lowered:
        return "generation"
    return fallback or "market"


def iter_numeric_raw_fields(raw: dict[str, Any]):
    excluded_fragments = (
        "time",
        "date",
        "interval",
        "name",
        "id",
        "location",
        "market",
        "zone",
        "type",
    )
    for key, value in raw.items():
        if any(fragment in key.lower() for fragment in excluded_fragments):
            continue
        parsed = parse_float(value)
        if parsed is not None:
            yield key, parsed


def parse_json_dict(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def parse_date_from_text(value: Any) -> date | None:
    text_value = optional_str(value)
    if not text_value:
        return None
    return parse_date(text_value[:10])


def optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def first_text(*values: Any) -> str:
    for value in values:
        text_value = optional_str(value)
        if text_value:
            return text_value
    return "value"


def border_label(in_domain: Any, out_domain: Any) -> str:
    return f"{optional_str(in_domain) or '?'}->{optional_str(out_domain) or '?'}"
