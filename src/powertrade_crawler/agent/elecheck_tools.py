from __future__ import annotations

import calendar
import re
import sqlite3
import time
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from powertrade_crawler.agent.schemas import RiskLevel
from powertrade_crawler.agent.security import redact_text, redact_value
from powertrade_crawler.agent.tools import ToolContext, ToolDefinition, ToolRegistry
from powertrade_crawler.config import get_settings
from powertrade_crawler.credentials import get_credential, get_project_root
from powertrade_crawler.elecheck_business_dashboard import (
    ElecheckMechanismDashboardRepository,
    ElecheckPurchasingDashboardRepository,
    draw_mechanism_figure,
    draw_purchasing_figure,
    export_mechanism_dashboard_csv,
    export_purchasing_dashboard_csv,
)
from powertrade_crawler.elecheck_collection import (
    build_clear_price_full_coverage_targets,
    clear_price_latest_daily_dates_by_area,
    collect_clear_price_targets,
)
from powertrade_crawler.elecheck_dashboard import (
    ElecheckAreaOption,
    ElecheckPriceDashboardRepository,
    draw_elecheck_price_figure,
    export_elecheck_intraday_csv,
)
from powertrade_crawler.scheduler import (
    create_scheduled_job,
    get_scheduled_job,
    install_windows_task,
    list_recent_job_runs,
    list_scheduled_jobs,
    parse_params_json,
    run_scheduled_job,
    set_scheduled_job_enabled,
    uninstall_windows_task,
)
from powertrade_crawler.scheduler import run_spider_and_upsert
from powertrade_crawler.storage import resolve_elecheck_area_code


ELECHECK_AGENT_SPIDERS = {
    "elecheck_clear_price",
    "elecheck_purchasing_national_range",
    "elecheck_mechanism_electricity_price",
}
WINDOW_LABELS = {
    12: "近 12 个月",
    24: "近 24 个月",
    0: "全部月份",
}
ELECHECK_SQL_TABLES: dict[str, dict[str, str]] = {
    "elecheck_area_records": {
        "id": "本地记录 ID",
        "area_name": "地区名称",
        "area_code": "地区代码",
        "detail_point_count": "日内预期点数",
        "detail_granularity": "明细粒度",
        "earliest_clear_price_date": "最早现货日期",
        "source": "数据来源",
        "collected_at": "采集时间",
    },
    "elecheck_clear_price_records": {
        "id": "本地记录 ID",
        "source": "数据来源",
        "endpoint": "statistics（日统计）或 detail（日内明细）",
        "area_code": "地区代码，可与 elecheck_area_records.area_code 关联",
        "start_date": "业务开始日期",
        "end_date": "业务结束日期",
        "time96": "日内时点；明细查询应排除 NULL",
        "metric": (
            "detail 使用 avg_day_ahead_price/avg_real_time_price；"
            "statistics 使用 day_ahead_avg_price/real_time_avg_price 等统计指标"
        ),
        "value": "价格数值",
        "unit": "计量单位",
        "currency": "币种",
        "collected_at": "采集时间",
    },
    "elecheck_purchasing_records": {
        "id": "本地记录 ID",
        "source": "数据来源",
        "endpoint": "接口类型",
        "data_kind": "数据类型",
        "data_month": "业务月份 YYYY-MM",
        "province_name": "省份名称",
        "metric": "价格或费用指标",
        "value": "指标数值",
        "unit": "计量单位",
        "diff_value": "差值",
        "statistic": "统计口径",
        "related_province_name": "关联省份",
        "collected_at": "采集时间",
    },
    "elecheck_purchasing_area_records": {
        "id": "本地记录 ID",
        "source": "数据来源",
        "province_name": "省份名称",
        "collected_at": "采集时间",
    },
    "elecheck_mechanism_electricity_price_records": {
        "id": "本地记录 ID",
        "source": "数据来源",
        "region_name": "地区名称",
        "region": "地区代码或原始地区字段",
        "category": "电源类型",
        "price": "燃煤基准价",
        "clear_price": "增量机制电价",
        "unit": "计量单位",
        "collected_at": "采集时间",
    },
}
ELECHECK_SQL_FUNCTIONS = {
    "abs",
    "avg",
    "coalesce",
    "count",
    "date",
    "datetime",
    "ifnull",
    "length",
    "lower",
    "max",
    "min",
    "nullif",
    "round",
    "strftime",
    "substr",
    "substring",
    "sum",
    "upper",
}


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyArgs(ToolArgs):
    pass


class AreaArgs(ToolArgs):
    area: str = Field(min_length=1, max_length=120)


class PurchasingOptionsArgs(ToolArgs):
    province: str | None = Field(default=None, max_length=120)


class SpotAnalysisArgs(AreaArgs):
    selected_date: date
    detail_level: Literal["summary", "full"] = "summary"


class SpotMonthlyExtremaArgs(ToolArgs):
    month: str
    price_type: Literal["day_ahead", "real_time"] = "real_time"
    direction: Literal["highest", "lowest"] = "highest"
    area: str | None = Field(default=None, max_length=120)
    limit: int = Field(default=5, ge=1, le=10)

    @field_validator("month")
    @classmethod
    def validate_month_value(cls, value: str) -> str:
        validate_month(value)
        return value


class PurchasingAnalysisArgs(ToolArgs):
    province: str = Field(min_length=1, max_length=120)
    end_month: str
    window_months: Literal[0, 12, 24] = 12

    @field_validator("end_month")
    @classmethod
    def validate_end_month(cls, value: str) -> str:
        validate_month(value)
        return value


class MechanismAnalysisArgs(ToolArgs):
    region: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=80)


class SpotExportArgs(AreaArgs):
    selected_date: date
    file_format: Literal["csv", "png"]
    series: Literal["all", "day_ahead", "real_time", "spread"] = "all"


class PurchasingExportArgs(PurchasingAnalysisArgs):
    file_format: Literal["csv", "png"]


class MechanismExportArgs(MechanismAnalysisArgs):
    file_format: Literal["csv", "png"]


class CollectSpotArgs(AreaArgs):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_range(self) -> "CollectSpotArgs":
        if self.area.strip() in {"全部", "all", "ALL", "*"}:
            raise ValueError("Agent 不允许采集全部地区，必须指定一个 Elecheck 地区。")
        if self.end_date < self.start_date:
            raise ValueError("end_date 不能早于 start_date。")
        if (self.end_date - self.start_date).days + 1 > 31:
            raise ValueError("单次现货采集最多 31 个自然日。")
        return self


class CollectPurchasingArgs(ToolArgs):
    start_month: str
    end_month: str

    @model_validator(mode="after")
    def validate_range(self) -> "CollectPurchasingArgs":
        start = validate_month(self.start_month)
        end = validate_month(self.end_month)
        if end < start:
            raise ValueError("end_month 不能早于 start_month。")
        if month_count(self.start_month, self.end_month) > 24:
            raise ValueError("单次代理购电采集最多 24 个月。")
        return self


class UpdateAllSpotArgs(ToolArgs):
    end_date: date


class ReadonlySqlArgs(ToolArgs):
    query: str = Field(min_length=1, max_length=4000)
    max_rows: int = Field(default=100, ge=1, le=200)


class ScheduleIdArgs(ToolArgs):
    job_id: int = Field(gt=0)


class CreateScheduleArgs(ToolArgs):
    name: str = Field(min_length=1, max_length=160)
    spider_name: Literal[
        "elecheck_clear_price",
        "elecheck_purchasing_national_range",
        "elecheck_mechanism_electricity_price",
    ]
    schedule_kind: Literal["daily", "weekly", "monthly"] = "daily"
    schedule_time: str = "02:00"
    enabled: bool = False
    area: str | None = Field(default=None, max_length=120)
    date_mode: Literal["none", "yesterday", "last-7-days", "last-30-days", "custom"] = (
        "none"
    )
    start_date: date | None = None
    end_date: date | None = None
    start_month: str | None = None
    end_month: str | None = None


class SetScheduleEnabledArgs(ScheduleIdArgs):
    enabled: bool


def resolve_sqlite_path() -> Path:
    database_url = get_settings().database_url
    if not database_url.startswith("sqlite:///"):
        raise ValueError("Elecheck Agent MVP currently supports only the local SQLite database.")
    raw_path = database_url.replace("sqlite:///", "", 1)
    path = Path(raw_path)
    return path if path.is_absolute() else get_project_root() / path


def _sql_schema(_args: EmptyArgs, _context: ToolContext) -> dict[str, Any]:
    db_path = resolve_sqlite_path()
    if not db_path.exists():
        raise ValueError("Elecheck SQLite database does not exist.")
    tables: dict[str, Any] = {}
    with sqlite3.connect(db_path) as connection:
        for table_name, allowed_columns in ELECHECK_SQL_TABLES.items():
            rows = connection.execute(
                f'PRAGMA table_info("{table_name}")'
            ).fetchall()
            if not rows:
                continue
            existing = {str(row[1]): str(row[2]) for row in rows}
            columns = [
                {
                    "name": column,
                    "type": existing[column],
                    "description": description,
                }
                for column, description in allowed_columns.items()
                if column in existing
            ]
            tables[table_name] = {"columns": columns}
    return {
        "dialect": "sqlite",
        "tables": tables,
        "rules": [
            "只允许 SELECT/CTE 只读查询。",
            "禁止 SELECT *；只选择回答问题所需字段。",
            "现货日内明细使用 endpoint='detail' 且 time96 IS NOT NULL。",
            "默认限制结果数量；聚合查询优先在数据库内完成。",
        ],
    }


def _validate_readonly_sql(query: str) -> str:
    normalized = query.strip()
    if not re.match(r"(?is)^(select|with)\b", normalized):
        raise ValueError("Elecheck SQL tool only accepts SELECT or WITH queries.")
    without_trailing = normalized[:-1].rstrip() if normalized.endswith(";") else normalized
    if ";" in without_trailing:
        raise ValueError("Elecheck SQL tool accepts exactly one statement.")
    if "--" in normalized or "/*" in normalized or "*/" in normalized:
        raise ValueError("SQL comments are not allowed.")
    if (
        re.search(r"(?is)\bselect\s+(?:distinct\s+)?\*", normalized)
        or re.search(r"(?is),\s*\*\s+from\b", normalized)
        or re.search(r"(?i)\b[a-z_][a-z0-9_]*\.\*", normalized)
    ):
        raise ValueError("SELECT * is not allowed; select only the required columns.")
    return normalized


def _sql_authorizer(
    action: int,
    argument_one: str | None,
    argument_two: str | None,
    _database_name: str | None,
    _trigger_name: str | None,
) -> int:
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        table = argument_one or ""
        column = argument_two or ""
        allowed_columns = ELECHECK_SQL_TABLES.get(table)
        if allowed_columns is not None and (not column or column in allowed_columns):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION:
        function_name = (argument_two or argument_one or "").lower()
        return (
            sqlite3.SQLITE_OK
            if function_name in ELECHECK_SQL_FUNCTIONS
            else sqlite3.SQLITE_DENY
        )
    if action == getattr(sqlite3, "SQLITE_RECURSIVE", -1):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _unique_column_names(description) -> list[str]:
    used: dict[str, int] = {}
    names: list[str] = []
    for column in description or ():
        base = str(column[0])
        count = used.get(base, 0) + 1
        used[base] = count
        names.append(base if count == 1 else f"{base}_{count}")
    return names


def _readonly_sql(args: ReadonlySqlArgs, _context: ToolContext) -> dict[str, Any]:
    query = _validate_readonly_sql(args.query)
    db_path = resolve_sqlite_path()
    if not db_path.exists():
        raise ValueError("Elecheck SQLite database does not exist.")
    deadline = time.monotonic() + 2.0
    connection = sqlite3.connect(
        f"{db_path.resolve().as_uri()}?mode=ro",
        uri=True,
    )
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.set_authorizer(_sql_authorizer)
        connection.set_progress_handler(
            lambda: 1 if time.monotonic() > deadline else 0,
            1000,
        )
        cursor = connection.execute(query)
        columns = _unique_column_names(cursor.description)
        raw_rows = cursor.fetchmany(args.max_rows + 1)
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"Elecheck read-only SQL was rejected: {exc}") from exc
    finally:
        connection.close()
    truncated = len(raw_rows) > args.max_rows
    rows = [
        {
            column: value.isoformat() if isinstance(value, (date, datetime)) else value
            for column, value in zip(columns, row, strict=True)
        }
        for row in raw_rows[: args.max_rows]
    ]
    return {
        "dialect": "sqlite",
        "query": query,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "max_rows": args.max_rows,
        "source": "Elecheck 易能电易查本地 SQLite（只读）",
    }


def validate_month(value: str) -> tuple[int, int]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise ValueError(f"月份必须使用 YYYY-MM 格式：{value}")
    year, month = (int(part) for part in value.split("-"))
    return year, month


def month_count(start_month: str, end_month: str) -> int:
    start_year, start_value = validate_month(start_month)
    end_year, end_value = validate_month(end_month)
    return (end_year - start_year) * 12 + end_value - start_value + 1


def _spot_area(repository: ElecheckPriceDashboardRepository, area: str) -> ElecheckAreaOption:
    normalized = area.strip()
    option = next(
        (
            row
            for row in repository.list_areas()
            if normalized in {row.area_name, row.area_code}
        ),
        None,
    )
    if option is None:
        raise ValueError(f"地区 {area!r} 没有可分析的 Elecheck 现货明细数据。")
    return option


def _analyze_spot(args: SpotAnalysisArgs, _context: ToolContext) -> dict[str, Any]:
    repository = ElecheckPriceDashboardRepository(resolve_sqlite_path())
    area = _spot_area(repository, args.area)
    data = repository.load_dashboard_data(area=area, selected_date=args.selected_date)
    if not data.day_ahead and not data.real_time:
        raise ValueError(f"{area.area_name} 在 {args.selected_date} 没有现货明细数据。")
    return _spot_payload(data, detail_level=args.detail_level)


def _analyze_spot_monthly_extrema(
    args: SpotMonthlyExtremaArgs,
    _context: ToolContext,
) -> dict[str, Any]:
    year, month = validate_month(args.month)
    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])
    expected_end = min(month_end, date.today())
    expected_days = max(0, (expected_end - month_start).days + 1)
    metric = {
        "day_ahead": "avg_day_ahead_price",
        "real_time": "avg_real_time_price",
    }[args.price_type]
    area_option: ElecheckAreaOption | None = None
    if args.area:
        area_option = _spot_area(
            ElecheckPriceDashboardRepository(resolve_sqlite_path()),
            args.area,
        )

    filters = [
        "endpoint = 'detail'",
        "start_date = end_date",
        "start_date >= ?",
        "start_date <= ?",
        "metric = ?",
        "time96 IS NOT NULL",
        "value IS NOT NULL",
    ]
    parameters: list[Any] = [
        month_start.isoformat(),
        month_end.isoformat(),
        metric,
    ]
    if area_option is not None:
        filters.append("area_code = ?")
        parameters.append(area_option.area_code)
    order = "DESC" if args.direction == "highest" else "ASC"
    db_path = resolve_sqlite_path()
    if not db_path.exists():
        raise ValueError("Elecheck SQLite database does not exist.")
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"""
            WITH per_area_day AS (
                SELECT start_date AS business_date,
                       area_code,
                       AVG(value) AS daily_average,
                       COUNT(DISTINCT time96) AS point_count
                FROM elecheck_clear_price_records
                WHERE {" AND ".join(filters)}
                GROUP BY start_date, area_code
            ),
            per_day AS (
                SELECT business_date,
                       AVG(daily_average) AS average_price,
                       COUNT(*) AS area_count,
                       SUM(point_count) AS point_count
                FROM per_area_day
                GROUP BY business_date
            )
            SELECT business_date, average_price, area_count, point_count
            FROM per_day
            ORDER BY average_price {order}, business_date ASC
            LIMIT ?
            """,
            [*parameters, args.limit],
        ).fetchall()
        coverage_row = connection.execute(
            f"""
            WITH per_area_day AS (
                SELECT start_date AS business_date, area_code
                FROM elecheck_clear_price_records
                WHERE {" AND ".join(filters)}
                GROUP BY start_date, area_code
            )
            SELECT MIN(business_date) AS first_date,
                   MAX(business_date) AS last_date,
                   COUNT(DISTINCT business_date) AS available_days,
                   MAX(area_count) AS maximum_area_count
            FROM (
                SELECT business_date, COUNT(*) AS area_count
                FROM per_area_day
                GROUP BY business_date
            )
            """,
            parameters,
        ).fetchone()
    if not rows:
        scope = area_option.area_name if area_option else "全部可用地区"
        raise ValueError(f"{scope} 在 {args.month} 没有可分析的现货明细数据。")

    maximum_area_count = int(coverage_row["maximum_area_count"] or 0)
    ranking = []
    for rank, row in enumerate(rows, start=1):
        area_count = int(row["area_count"])
        ranking.append(
            {
                "rank": rank,
                "date": str(row["business_date"]),
                "average_price": float(row["average_price"]),
                "unit": "CNY/MWh",
                "area_count": area_count,
                "area_coverage_ratio": (
                    area_count / maximum_area_count
                    if maximum_area_count
                    else None
                ),
                "intraday_point_count": int(row["point_count"]),
            }
        )
    available_days = int(coverage_row["available_days"] or 0)
    warnings = [
        "日均价先按每个地区的日内明细独立求平均；不插值、不补零。"
    ]
    if area_option is None:
        warnings.append(
            "未指定地区，结果为各可用地区日均价的等权平均；地区覆盖不足的日期会保留并标明覆盖率。"
        )
    if available_days < expected_days:
        warnings.append(
            f"{args.month} 截至 {expected_end.isoformat()} 应覆盖 {expected_days} 天，"
            f"本地仅有 {available_days} 天。"
        )
    return {
        "analysis": "spot_monthly_daily_extrema",
        "month": args.month,
        "price_type": args.price_type,
        "direction": args.direction,
        "scope": (
            {
                "type": "single_area",
                "area_name": area_option.area_name,
                "area_code": area_option.area_code,
            }
            if area_option is not None
            else {
                "type": "all_available_areas_equal_weight",
                "maximum_area_count": maximum_area_count,
            }
        ),
        "extreme": ranking[0],
        "ranking": ranking,
        "available_date_range": {
            "first_date": str(coverage_row["first_date"]),
            "last_date": str(coverage_row["last_date"]),
            "available_days": available_days,
            "expected_days_through_as_of": expected_days,
            "as_of_date": date.today().isoformat(),
        },
        "calculation": (
            "先对每个地区当日所有可用时点求日均价，再对当日各地区日均价等权平均；"
            "不插值、不补零。"
        ),
        "warnings": warnings,
        "source": "Elecheck 易能电易查本地现货明细",
    }


def _series_extrema(points) -> dict[str, Any] | None:
    if not points:
        return None
    minimum = min(points, key=lambda row: (row.value, row.minute))
    maximum = max(points, key=lambda row: (row.value, -row.minute))
    return {
        "minimum": {"time": minimum.time_label, "value": minimum.value},
        "maximum": {"time": maximum.time_label, "value": maximum.value},
    }


def _spot_payload(data, *, detail_level: str = "summary") -> dict[str, Any]:
    day_count = len(data.day_ahead)
    real_count = len(data.real_time)
    common_count = len(data.spread)
    payload = {
        "analysis": "spot_price",
        "area": {"name": data.area.area_name, "code": data.area.area_code},
        "selected_date": data.selected_date.isoformat(),
        "unit": "CNY/MWh",
        "day_ahead_average": data.day_ahead_average,
        "real_time_average": data.real_time_average,
        "average_spread_real_time_minus_day_ahead": data.spread_average,
        "point_counts": {
            "day_ahead": day_count,
            "real_time": real_count,
            "common_spread": common_count,
        },
        "completeness": {
            "day_ahead": completeness(day_count, data.expected_day_ahead_points),
            "real_time": completeness(real_count, data.expected_real_time_points),
            "spread_only_matches_same_time": True,
        },
        "extrema": {
            "day_ahead": _series_extrema(data.day_ahead),
            "real_time": _series_extrema(data.real_time),
            "spread": _series_extrema(data.spread),
        },
        "warnings": [
            "不插值、不补零；价差只在日前与实时都存在的相同时点计算。"
        ],
        "source": "Elecheck 易能电易查本地明细",
    }
    if detail_level == "full":
        payload["intraday"] = {
            "day_ahead": [
                {"time": row.time_label, "value": row.value} for row in data.day_ahead
            ],
            "real_time": [
                {"time": row.time_label, "value": row.value} for row in data.real_time
            ],
            "spread": [
                {"time": row.time_label, "value": row.value} for row in data.spread
            ],
        }
        payload["last_30_calendar_days"] = [
            {
                "date": row.metric_date.isoformat(),
                "metric": row.metric,
                "average": row.value,
                "point_count": row.point_count,
            }
            for row in data.daily_trend
        ]
    return payload


def completeness(actual: int, expected: int | None) -> dict[str, Any]:
    return {
        "actual": actual,
        "expected": expected,
        "ratio": actual / expected if expected else None,
    }


def _analyze_purchasing(
    args: PurchasingAnalysisArgs,
    _context: ToolContext,
) -> dict[str, Any]:
    repository = ElecheckPurchasingDashboardRepository(resolve_sqlite_path())
    if args.province not in repository.list_provinces():
        raise ValueError(f"省份 {args.province!r} 没有代理购电分析数据。")
    window_label = WINDOW_LABELS[args.window_months]
    data = repository.load_dashboard_data(
        province_name=args.province,
        end_month=args.end_month,
        window_label=window_label,
    )
    current = data.current
    if current is None or all(
        value is None
        for value in (
            current.purchasing_price,
            current.operating_cost,
            current.line_loss_cost,
            current.total,
        )
    ):
        raise ValueError(f"{args.province} 在 {args.end_month} 没有代理购电价格数据。")
    return {
        "analysis": "purchasing_price",
        "province": data.province_name,
        "end_month": data.end_month,
        "window": data.window_label,
        "unit": "CNY/kWh",
        "current": {
            "purchasing_price": current.purchasing_price,
            "system_operating_cost_discount": current.operating_cost,
            "line_loss_discount": current.line_loss_cost,
            "total": current.total,
            "month_over_month_absolute": data.month_over_month,
        },
        "rank_position_high_to_low": data.rank_position,
        "province_count": len(data.ranking),
        "covered_months": data.covered_months,
        "timeline_months": len(data.timeline),
        "timeline": [
            {
                "month": row.data_month,
                "purchasing_price": row.purchasing_price,
                "system_operating_cost_discount": row.operating_cost,
                "line_loss_discount": row.line_loss_cost,
                "total": row.total,
            }
            for row in data.timeline
        ],
        "ranking": [
            {"province": row.province_name, "total": row.total}
            for row in sorted(
                data.ranking,
                key=lambda item: (-item.total, item.province_name),
            )
        ],
        "warnings": ["缺失月份保留断点，不补零。"],
        "source": "Elecheck 易能电易查代理购电 national_table",
    }


def _analyze_mechanism(
    args: MechanismAnalysisArgs,
    _context: ToolContext,
) -> dict[str, Any]:
    repository = ElecheckMechanismDashboardRepository(resolve_sqlite_path())
    data = repository.load_dashboard_data(region_name=args.region, category=args.category)
    if data.selected is None:
        raise ValueError(f"{args.region}/{args.category} 没有增量机制电价快照。")
    row = data.selected
    return {
        "analysis": "mechanism_price",
        "region": row.region_name,
        "category": row.category,
        "unit": "CNY/kWh",
        "coal_benchmark_price": row.price,
        "mechanism_price_2026": row.clear_price,
        "gap": row.gap,
        "relative_gap_percent": row.relative_gap,
        "collected_at": row.collected_at,
        "category_ranking": [
            {
                "region": item.region_name,
                "coal_benchmark_price": item.price,
                "mechanism_price_2026": item.clear_price,
                "gap": item.gap,
                "relative_gap_percent": item.relative_gap,
            }
            for item in sorted(
                data.category_rows,
                key=lambda item: (
                    item.gap is None,
                    -(item.gap or 0),
                    item.region_name,
                ),
            )
        ],
        "region_categories": [
            {
                "category": item.category,
                "coal_benchmark_price": item.price,
                "mechanism_price_2026": item.clear_price,
                "gap": item.gap,
                "relative_gap_percent": item.relative_gap,
            }
            for item in data.region_rows
        ],
        "warnings": ["只分析数据库中的最新快照，不推断或虚构历史趋势。"],
        "source": "Elecheck 易能电易查增量机制电价最新快照",
    }


def _list_areas(_args: EmptyArgs, _context: ToolContext) -> dict[str, Any]:
    repository = ElecheckPriceDashboardRepository(resolve_sqlite_path())
    return {
        "areas": [
            {"name": row.area_name, "code": row.area_code}
            for row in repository.list_areas()
        ]
    }


def _list_dates(args: AreaArgs, _context: ToolContext) -> dict[str, Any]:
    repository = ElecheckPriceDashboardRepository(resolve_sqlite_path())
    area = _spot_area(repository, args.area)
    dates = repository.list_dates(area.area_code)
    return {
        "area": {"name": area.area_name, "code": area.area_code},
        "available_date_count": len(dates),
        "recent_dates": [value.isoformat() for value in dates[-60:]],
        "first_date": dates[0].isoformat() if dates else None,
        "last_date": dates[-1].isoformat() if dates else None,
    }


def _freshness(_args: EmptyArgs, _context: ToolContext) -> dict[str, Any]:
    db_path = resolve_sqlite_path()
    tables = {
        "spot_price": ("elecheck_clear_price_records", "end_date"),
        "purchasing_price": ("elecheck_purchasing_records", "data_month"),
        "mechanism_price": (
            "elecheck_mechanism_electricity_price_records",
            None,
        ),
    }
    result: dict[str, Any] = {}
    if not db_path.exists():
        return {
            "database": str(db_path),
            "as_of_date": date.today().isoformat(),
            "current_month": date.today().strftime("%Y-%m"),
            "tables": result,
        }
    with sqlite3.connect(db_path) as connection:
        existing = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for label, (table_name, business_period_column) in tables.items():
            if table_name not in existing:
                result[label] = {
                    "records": 0,
                    "latest_collected_at": None,
                    "latest_business_period": None,
                }
                continue
            columns = {
                row[1]
                for row in connection.execute(f"PRAGMA table_info({table_name})")
            }
            period_expression = (
                f", MAX({business_period_column})"
                if business_period_column in columns
                else ", NULL"
            )
            row = connection.execute(
                f"SELECT COUNT(*), MAX(collected_at){period_expression} "
                f"FROM {table_name}"
            ).fetchone()
            result[label] = {
                "records": int(row[0]),
                "latest_collected_at": str(row[1]) if row[1] else None,
                "latest_business_period": str(row[2]) if row[2] else None,
            }
        spot_table = tables["spot_price"][0]
        if spot_table in existing:
            spot_columns = {
                row[1]
                for row in connection.execute(f"PRAGMA table_info({spot_table})")
            }
            if {"area_code", "end_date"} <= spot_columns:
                area_row = connection.execute(
                    "SELECT COUNT(*), MIN(latest_date), MAX(latest_date) "
                    "FROM ("
                    "SELECT area_code, MAX(end_date) AS latest_date "
                    "FROM elecheck_clear_price_records GROUP BY area_code"
                    ")"
                ).fetchone()
                result["spot_price"].update(
                    {
                        "area_count": int(area_row[0]),
                        "oldest_area_latest_business_date": (
                            str(area_row[1]) if area_row[1] else None
                        ),
                        "newest_area_latest_business_date": (
                            str(area_row[2]) if area_row[2] else None
                        ),
                    }
                )
    today = date.today()
    return {
        "database": "local SQLite",
        "as_of_date": today.isoformat(),
        "current_month": today.strftime("%Y-%m"),
        "tables": result,
        "collection_rules": {
            "spot_price": (
                "可指定单一地区采集，或调用更新全部现货工具，按各地区最新日期更新。"
            ),
            "purchasing_price": "可采集全国指定月份范围；单次最多 24 个月。",
            "mechanism_price": "只更新最新快照。",
        },
    }


def _purchasing_options(
    args: PurchasingOptionsArgs,
    _context: ToolContext,
) -> dict[str, Any]:
    repository = ElecheckPurchasingDashboardRepository(resolve_sqlite_path())
    provinces = repository.list_provinces()
    if args.province is None:
        return {"provinces": provinces}
    if args.province not in provinces:
        raise ValueError(f"省份 {args.province!r} 没有代理购电分析数据。")
    months = repository.list_months(args.province)
    return {
        "province": args.province,
        "months": months,
        "first_month": months[0] if months else None,
        "last_month": months[-1] if months else None,
    }


def _mechanism_options(_args: EmptyArgs, _context: ToolContext) -> dict[str, Any]:
    repository = ElecheckMechanismDashboardRepository(resolve_sqlite_path())
    return {
        "regions": repository.list_regions(),
        "categories": repository.list_categories(),
    }


def _credential_status(_args: EmptyArgs, _context: ToolContext) -> dict[str, Any]:
    return {"elecheck_authorization": "configured" if get_credential("elecheck_authorization") else "missing"}


def _schedule_payload(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "spider_name": row.spider_name,
        "schedule_kind": row.schedule_kind,
        "schedule_time": row.schedule_time,
        "date_mode": row.date_mode,
        "start_date": row.start_date.isoformat() if row.start_date else None,
        "end_date": row.end_date.isoformat() if row.end_date else None,
        "enabled": row.enabled,
        "params": redact_value(parse_params_json(row.params_json)),
        "windows_task_name": row.windows_task_name,
    }


def _list_schedules(_args: EmptyArgs, _context: ToolContext) -> dict[str, Any]:
    rows = [
        row
        for row in list_scheduled_jobs()
        if row.spider_name in ELECHECK_AGENT_SPIDERS
    ]
    return {"jobs": [_schedule_payload(row) for row in rows]}


def _list_schedule_runs(_args: EmptyArgs, _context: ToolContext) -> dict[str, Any]:
    elecheck_ids = {
        row.id
        for row in list_scheduled_jobs()
        if row.spider_name in ELECHECK_AGENT_SPIDERS
    }
    rows = [
        row for row in list_recent_job_runs(limit=100) if row.job_id in elecheck_ids
    ][:50]
    return {
        "runs": [
            {
                "id": row.id,
                "job_id": row.job_id,
                "status": row.status,
                "message": redact_text(row.message),
                "records_written": row.records_written,
                "started_at": row.started_at.isoformat(),
                "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            }
            for row in rows
        ]
    }


def _export_path(context: ToolContext, analysis: str, extension: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    base = get_project_root() / "exports" / "agent" / context.session_id
    path = (base / f"{timestamp}-{analysis}.{extension}").resolve()
    base_resolved = base.resolve()
    if base_resolved not in path.parents:
        raise ValueError("Invalid Agent export path.")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _export_spot(args: SpotExportArgs, context: ToolContext) -> dict[str, Any]:
    repository = ElecheckPriceDashboardRepository(resolve_sqlite_path())
    area = _spot_area(repository, args.area)
    data = repository.load_dashboard_data(area=area, selected_date=args.selected_date)
    if not data.day_ahead and not data.real_time:
        raise ValueError("没有可导出的现货分析数据。")
    analysis_name = (
        "spot-price" if args.series == "all" else f"spot-{args.series.replace('_', '-')}-price"
    )
    output = _export_path(context, analysis_name, args.file_format)
    if args.file_format == "csv":
        rows = export_elecheck_intraday_csv(output, data, series=args.series)
    else:
        from matplotlib.figure import Figure

        filtered_data = data
        if args.series != "all":
            metric = {
                "day_ahead": "avg_day_ahead_price",
                "real_time": "avg_real_time_price",
                "spread": None,
            }[args.series]
            filtered_data = replace(
                data,
                day_ahead=data.day_ahead if args.series == "day_ahead" else (),
                real_time=data.real_time if args.series == "real_time" else (),
                spread=data.spread if args.series == "spread" else (),
                daily_trend=(
                    tuple(row for row in data.daily_trend if row.metric == metric)
                    if metric
                    else ()
                ),
            )
        figure = Figure(figsize=(13, 8), dpi=110)
        draw_elecheck_price_figure(figure, filtered_data)
        figure.savefig(output, dpi=160, bbox_inches="tight")
        rows = 1
    return {
        "file": str(output),
        "format": args.file_format,
        "series": args.series,
        "rows_or_figures": rows,
    }


def _export_purchasing(
    args: PurchasingExportArgs,
    context: ToolContext,
) -> dict[str, Any]:
    repository = ElecheckPurchasingDashboardRepository(resolve_sqlite_path())
    data = repository.load_dashboard_data(
        province_name=args.province,
        end_month=args.end_month,
        window_label=WINDOW_LABELS[args.window_months],
    )
    if data.current is None:
        raise ValueError("没有可导出的代理购电分析数据。")
    output = _export_path(context, "purchasing-price", args.file_format)
    if args.file_format == "csv":
        rows = export_purchasing_dashboard_csv(output, data)
    else:
        from matplotlib.figure import Figure

        figure = Figure(figsize=(13, 8), dpi=110)
        draw_purchasing_figure(figure, data)
        figure.savefig(output, dpi=160, bbox_inches="tight")
        rows = 1
    return {"file": str(output), "format": args.file_format, "rows_or_figures": rows}


def _export_mechanism(
    args: MechanismExportArgs,
    context: ToolContext,
) -> dict[str, Any]:
    repository = ElecheckMechanismDashboardRepository(resolve_sqlite_path())
    data = repository.load_dashboard_data(region_name=args.region, category=args.category)
    if data.selected is None:
        raise ValueError("没有可导出的增量机制电价分析数据。")
    output = _export_path(context, "mechanism-price", args.file_format)
    if args.file_format == "csv":
        rows = export_mechanism_dashboard_csv(output, data)
    else:
        from matplotlib.figure import Figure

        figure = Figure(figsize=(13, 8), dpi=110)
        draw_mechanism_figure(figure, data)
        figure.savefig(output, dpi=160, bbox_inches="tight")
        rows = 1
    return {"file": str(output), "format": args.file_format, "rows_or_figures": rows}


def _collect_spot(args: CollectSpotArgs, _context: ToolContext) -> dict[str, Any]:
    area_code = resolve_elecheck_area_code(args.area)
    written, produced = run_spider_and_upsert(
        "elecheck_clear_price",
        {
            "area_code": area_code,
            "start_date": args.start_date.isoformat(),
            "end_date": args.end_date.isoformat(),
            "daily": True,
        },
    )
    return {
        "spider_name": "elecheck_clear_price",
        "area": args.area,
        "area_code": area_code,
        "start_date": args.start_date.isoformat(),
        "end_date": args.end_date.isoformat(),
        "records_produced": produced,
        "records_upserted": written,
    }


def _update_all_spot(
    args: UpdateAllSpotArgs,
    context: ToolContext,
) -> dict[str, Any]:
    db_path = resolve_sqlite_path()
    latest_dates = clear_price_latest_daily_dates_by_area(db_path)
    targets = build_clear_price_full_coverage_targets(
        db_path,
        args.end_date.isoformat(),
        latest_dates=latest_dates,
    )
    collection = collect_clear_price_targets(
        targets,
        is_cancelled=context.is_cancelled,
        progress_callback=context.report_progress,
    )
    return {
        "spider_name": "elecheck_clear_price",
        "mode": "update_all" if latest_dates else "full_coverage",
        "end_date": args.end_date.isoformat(),
        "earliest_target_start_date": min(
            target["start_date"] for target in targets
        ),
        "latest_target_start_date": max(target["start_date"] for target in targets),
        **collection,
    }


def _collect_purchasing(
    args: CollectPurchasingArgs,
    _context: ToolContext,
) -> dict[str, Any]:
    written, produced = run_spider_and_upsert(
        "elecheck_purchasing_national_range",
        {"start_month": args.start_month, "end_month": args.end_month},
    )
    return {
        "spider_name": "elecheck_purchasing_national_range",
        "start_month": args.start_month,
        "end_month": args.end_month,
        "records_produced": produced,
        "records_upserted": written,
    }


def _update_mechanism(_args: EmptyArgs, _context: ToolContext) -> dict[str, Any]:
    written, produced = run_spider_and_upsert(
        "elecheck_mechanism_electricity_price",
        {},
    )
    return {
        "spider_name": "elecheck_mechanism_electricity_price",
        "records_produced": produced,
        "records_upserted": written,
    }


def _create_schedule(args: CreateScheduleArgs, _context: ToolContext) -> dict[str, Any]:
    params: dict[str, Any] = {}
    date_mode = args.date_mode
    start_date = args.start_date
    end_date = args.end_date
    if args.spider_name == "elecheck_clear_price":
        if not args.area or args.area.strip() in {"全部", "all", "ALL", "*"}:
            raise ValueError("Elecheck 现货定时任务必须指定单一地区。")
        params["area_code"] = resolve_elecheck_area_code(args.area)
        if date_mode == "last-30-days":
            raise ValueError("Agent 定时现货采集最多允许最近 7 天。")
        if date_mode == "custom":
            if start_date is None or end_date is None:
                raise ValueError("custom 日期模式必须提供 start_date 和 end_date。")
            if end_date <= start_date or (end_date - start_date).days > 31:
                raise ValueError("现货定时任务的 custom 窗口必须为 1 至 31 天。")
        if args.start_month is not None or args.end_month is not None:
            raise ValueError("现货定时任务不接受月份参数。")
    else:
        if date_mode != "none" or start_date is not None or end_date is not None:
            raise ValueError("该 Elecheck spider 不支持日期窗口，date_mode 必须为 none。")
        if args.area is not None:
            raise ValueError("该 Elecheck spider 不接受地区参数。")
    if args.spider_name == "elecheck_purchasing_national_range":
        if not args.start_month or not args.end_month:
            raise ValueError("代理购电定时任务需要 start_month 和 end_month。")
        CollectPurchasingArgs(
            start_month=args.start_month,
            end_month=args.end_month,
        )
        params.update(start_month=args.start_month, end_month=args.end_month)
    elif args.start_month is not None or args.end_month is not None:
        raise ValueError("该 Elecheck spider 不接受月份参数。")
    row = create_scheduled_job(
        name=args.name,
        job_type="crawl",
        spider_name=args.spider_name,
        schedule_kind=args.schedule_kind,
        schedule_time=args.schedule_time,
        date_mode=date_mode,
        start_date=start_date,
        end_date=end_date,
        enabled=args.enabled,
        params=params,
    )
    return {"job": _schedule_payload(row)}


def _elecheck_job(job_id: int):
    row = get_scheduled_job(job_id)
    if row.spider_name not in ELECHECK_AGENT_SPIDERS:
        raise ValueError("Agent 只能操作 Elecheck allowlist 中的定时任务。")
    params = parse_params_json(row.params_json)
    if row.spider_name == "elecheck_purchasing_national_range":
        start_month = params.get("start_month")
        end_month = params.get("end_month")
        if not isinstance(start_month, str) or not isinstance(end_month, str):
            raise ValueError("代理购电定时任务缺少明确月份范围，Agent 拒绝执行。")
        if month_count(start_month, end_month) > 24:
            raise ValueError("代理购电定时任务超过 Agent 允许的 24 个月范围。")
    if (
        row.spider_name == "elecheck_clear_price"
        and row.date_mode == "custom"
        and row.start_date
        and row.end_date
        and (row.end_date - row.start_date).days > 31
    ):
        raise ValueError("现货定时任务超过 Agent 允许的 31 天范围。")
    return row


def _run_schedule(args: ScheduleIdArgs, _context: ToolContext) -> dict[str, Any]:
    _elecheck_job(args.job_id)
    return run_scheduled_job(args.job_id, force=True)


def _set_schedule_enabled(
    args: SetScheduleEnabledArgs,
    _context: ToolContext,
) -> dict[str, Any]:
    _elecheck_job(args.job_id)
    row = set_scheduled_job_enabled(args.job_id, args.enabled)
    return {"job": _schedule_payload(row)}


def _install_windows(args: ScheduleIdArgs, _context: ToolContext) -> dict[str, Any]:
    row = _elecheck_job(args.job_id)
    task_name = install_windows_task(args.job_id)
    return {"job_id": row.id, "task_name": task_name, "installed": True}


def _uninstall_windows(args: ScheduleIdArgs, _context: ToolContext) -> dict[str, Any]:
    row = _elecheck_job(args.job_id)
    task_name = uninstall_windows_task(args.job_id)
    return {"job_id": row.id, "task_name": task_name, "installed": False}


def build_elecheck_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()

    def add(
        name: str,
        description: str,
        args_model: type[BaseModel],
        handler,
        risk: RiskLevel = RiskLevel.AUTO,
        side_effect: str = "只读，不修改本地或远程数据。",
    ) -> None:
        registry.register(
            ToolDefinition(
                name=name,
                description=description,
                args_model=args_model,
                handler=handler,
                risk_level=risk,
                side_effect=side_effect,
            )
        )

    add("elecheck_list_areas", "列出有现货明细数据的 Elecheck 地区。", EmptyArgs, _list_areas)
    add("elecheck_list_dates", "查询单一地区已有现货数据日期。", AreaArgs, _list_dates)
    add(
        "elecheck_data_freshness",
        (
            "查询 Elecheck 三类业务表的数据量、业务日期/月、最近采集时间和采集约束。"
            "用户要求更新到今天时应先调用本工具。"
        ),
        EmptyArgs,
        _freshness,
    )
    add(
        "elecheck_purchasing_options",
        "列出代理购电可分析省份；指定省份时返回其可用月份。",
        PurchasingOptionsArgs,
        _purchasing_options,
    )
    add(
        "elecheck_mechanism_options",
        "列出增量机制电价快照中的可分析地区和电源类型。",
        EmptyArgs,
        _mechanism_options,
    )
    add(
        "elecheck_sql_schema",
        (
            "返回 Agent 可查询的 Elecheck SQLite 表、字段、类型和业务含义。"
            "只返回 allowlist，不包含 raw_json、Agent 会话、凭据或其他数据源表。"
        ),
        EmptyArgs,
        _sql_schema,
    )
    add(
        "elecheck_sql_query",
        (
            "执行一条受控的 Elecheck SQLite 只读 SELECT/CTE 查询，适合最高、最低、"
            "平均、计数、排名、筛选和聚合问题。执行前应先了解 schema。"
            "只允许 allowlist 表/字段/函数，最多返回200行，禁止注释、多语句和所有写操作。"
        ),
        ReadonlySqlArgs,
        _readonly_sql,
    )
    add(
        "elecheck_analyze_spot",
        (
            "分析单地区单日现货日前价、实时价、共同时间点价差和完整度。"
            "默认 summary 直接返回均值及最高/最低价格，避免传回大量时点；"
            "只有用户明确需要全部时点或30天趋势时才使用 full。"
        ),
        SpotAnalysisArgs,
        _analyze_spot,
    )
    add(
        "elecheck_analyze_spot_monthly_extrema",
        (
            "直接回答某月日前或实时日均价最高/最低是哪一天，并返回紧凑排名、"
            "日期覆盖和地区覆盖。可指定单一地区；未指定时先计算各地区日均价，"
            "再对可用地区等权平均。不插值、不补零。此类问题不要先查 schema 或 SQL。"
        ),
        SpotMonthlyExtremaArgs,
        _analyze_spot_monthly_extrema,
    )
    add("elecheck_analyze_purchasing", "分析单省代理购电费用构成、环比、月份断点和省际排名。", PurchasingAnalysisArgs, _analyze_purchasing)
    add("elecheck_analyze_mechanism", "分析增量机制最新快照的价格差额、差幅和地区/电源类型排名。", MechanismAnalysisArgs, _analyze_mechanism)
    add("elecheck_credential_status", "只查询 Elecheck Authorization 是否已配置，不读取凭据内容。", EmptyArgs, _credential_status)
    add("elecheck_list_schedules", "列出 Agent allowlist 内的 Elecheck 定时任务。", EmptyArgs, _list_schedules)
    add("elecheck_list_schedule_runs", "列出 Elecheck 定时任务最近运行结果和失败原因。", EmptyArgs, _list_schedule_runs)
    add(
        "elecheck_export_spot",
        (
            "把指定现货分析导出为预设目录下的 CSV 或 PNG。"
            "series 必须匹配用户要求：仅日前用 day_ahead，仅实时用 real_time，"
            "仅价差用 spread；只有明确要求全部指标时才用 all。"
        ),
        SpotExportArgs,
        _export_spot,
    )
    add("elecheck_export_purchasing", "把指定代理购电分析导出为预设目录下的 CSV 或 PNG。", PurchasingExportArgs, _export_purchasing)
    add("elecheck_export_mechanism", "把指定增量机制分析导出为预设目录下的 CSV 或 PNG。", MechanismExportArgs, _export_mechanism)
    add(
        "elecheck_collect_spot",
        "采集单一地区、最多31天的 Elecheck 现货数据并 upsert。",
        CollectSpotArgs,
        _collect_spot,
        RiskLevel.APPROVAL,
        "将访问 Elecheck 网络接口，并向本地现货表 upsert 指定地区和日期范围的数据。",
    )
    add(
        "elecheck_update_all_spot",
        (
            "调用 GUI“更新全部数据”的现货采集语义：从每个地区已采集的最新日"
            "往前一天开始，逐日更新全部地区到指定结束日期。"
        ),
        UpdateAllSpotArgs,
        _update_all_spot,
        RiskLevel.APPROVAL,
        (
            "将访问 Elecheck 网络接口，按本地地区目录逐日采集全部地区；"
            "每个地区从已采最新日期往前一天开始，并 upsert 到本地现货表。"
            "若本地尚无现货数据，则会从各地区最早可用日期开始全覆盖采集，可能耗时较长。"
        ),
    )
    add(
        "elecheck_collect_purchasing",
        "采集最多24个月的全国代理购电价格并 upsert。",
        CollectPurchasingArgs,
        _collect_purchasing,
        RiskLevel.APPROVAL,
        "将访问 Elecheck 网络接口，并向本地代理购电表 upsert 指定月份范围的数据。",
    )
    add(
        "elecheck_update_mechanism",
        "更新 Elecheck 增量机制电价最新快照。",
        EmptyArgs,
        _update_mechanism,
        RiskLevel.APPROVAL,
        "将访问 Elecheck 网络接口，并 upsert 当前全部地区和电源类型快照。",
    )
    add(
        "elecheck_create_schedule",
        "创建 allowlist 内的 Elecheck 本地定时任务，不支持删除。",
        CreateScheduleArgs,
        _create_schedule,
        RiskLevel.APPROVAL,
        "将在本地数据库创建一条 Elecheck 定时任务定义；不会自动安装 Windows 任务。",
    )
    add(
        "elecheck_run_schedule",
        "立即运行一条 allowlist 内的 Elecheck 定时任务。",
        ScheduleIdArgs,
        _run_schedule,
        RiskLevel.APPROVAL,
        "将立即访问对应数据源并写入本地数据库，运行结果写入任务日志。",
    )
    add(
        "elecheck_set_schedule_enabled",
        "启用或禁用一条 allowlist 内的 Elecheck 定时任务。",
        SetScheduleEnabledArgs,
        _set_schedule_enabled,
        RiskLevel.APPROVAL,
        "将修改本地 Elecheck 定时任务的 enabled 状态。",
    )
    add(
        "elecheck_install_windows_schedule",
        "把 Elecheck 本地任务安装到 Windows 任务计划程序。",
        ScheduleIdArgs,
        _install_windows,
        RiskLevel.STRONG_APPROVAL,
        "将调用 Windows schtasks 创建系统计划任务，需要针对任务 ID 和系统副作用二次确认。",
    )
    add(
        "elecheck_uninstall_windows_schedule",
        "从 Windows 任务计划程序卸载 Elecheck 任务，但保留本地任务定义。",
        ScheduleIdArgs,
        _uninstall_windows,
        RiskLevel.STRONG_APPROVAL,
        "将调用 Windows schtasks 删除对应系统计划任务，但不会删除本地任务定义。",
    )
    return registry
