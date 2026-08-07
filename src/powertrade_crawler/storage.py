import csv
import re
from datetime import date
from pathlib import Path

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from powertrade_crawler.config import get_settings
from powertrade_crawler.models import (
    ElecheckAreaRecord,
    ElecheckClearPriceRecord,
    ElecheckMechanismElectricityPriceRecord,
    ElecheckPurchasingProvinceRecord,
    ElecheckPurchasingRecord,
    ElexonRecord,
    EntsoeRecord,
    GridStatusDatasetMetadataRecord,
    GridStatusRecord,
    GzpecNewsRecord,
    MarketRecord,
)


class Base(DeclarativeBase):
    pass


class MarketRecordRow(Base):
    __tablename__ = "market_records"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "market",
            "region",
            "trade_date",
            "metric",
            "unit",
            name="uq_market_record_natural_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(120), index=True)
    market: Mapped[str] = mapped_column(String(80), index=True)
    region: Mapped[str] = mapped_column(String(120), index=True)
    trade_date: Mapped[Date] = mapped_column(Date, index=True)
    metric: Mapped[str] = mapped_column(String(120), index=True)
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(40))
    currency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    raw_json: Mapped[str] = mapped_column(Text)
    collected_at: Mapped[DateTime] = mapped_column(DateTime)


class GzpecNewsRecordRow(Base):
    __tablename__ = "gzpec_news_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(120), index=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(1000), unique=True, index=True)
    publish_date: Mapped[Date | None] = mapped_column(Date, nullable=True, index=True)
    index_url: Mapped[str] = mapped_column(String(1000), index=True)
    news_type: Mapped[str] = mapped_column(String(80), index=True)
    content_json: Mapped[str] = mapped_column(Text)
    collected_at: Mapped[DateTime] = mapped_column(DateTime)


class GridStatusRecordRow(Base):
    __tablename__ = "gridstatus_records"
    __table_args__ = (
        UniqueConstraint(
            "request_name",
            "row_key",
            name="uq_gridstatus_request_row_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    request_name: Mapped[str] = mapped_column(String(160), index=True)
    request_type: Mapped[str] = mapped_column(String(80), index=True)
    dataset: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    location: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    row_key: Mapped[str] = mapped_column(String(120), index=True)
    interval_start_utc: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    interval_end_utc: Mapped[str | None] = mapped_column(String(40), nullable=True)
    record_time_utc: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    raw_json: Mapped[str] = mapped_column(Text)
    collected_at: Mapped[DateTime] = mapped_column(DateTime)


class EntsoeRecordRow(Base):
    __tablename__ = "entsoe_records"
    __table_args__ = (
        UniqueConstraint(
            "dataset",
            "row_key",
            name="uq_entsoe_dataset_row_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    dataset: Mapped[str] = mapped_column(String(160), index=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    title_en: Mapped[str] = mapped_column(String(240))
    title_zh: Mapped[str] = mapped_column(String(240))
    row_key: Mapped[str] = mapped_column(String(64), index=True)
    document_type: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    process_type: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    business_type: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    area: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    in_domain: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    out_domain: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    time_series_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    psr_type: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    interval_start_utc: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    interval_end_utc: Mapped[str | None] = mapped_column(String(40), nullable=True)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(20), nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_field: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    unit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    raw_json: Mapped[str] = mapped_column(Text)
    collected_at: Mapped[DateTime] = mapped_column(DateTime)


class ElexonRecordRow(Base):
    __tablename__ = "elexon_records"
    __table_args__ = (
        UniqueConstraint(
            "dataset",
            "row_key",
            name="uq_elexon_dataset_row_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    dataset: Mapped[str] = mapped_column(String(160), index=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    title_en: Mapped[str] = mapped_column(String(240))
    title_zh: Mapped[str] = mapped_column(String(240))
    endpoint: Mapped[str] = mapped_column(String(240), index=True)
    row_key: Mapped[str] = mapped_column(String(64), index=True)
    area: Mapped[str] = mapped_column(String(20), index=True)
    settlement_date: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    settlement_period: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    publish_time_utc: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    start_time_utc: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    end_time_utc: Mapped[str | None] = mapped_column(String(40), nullable=True)
    fuel_type: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    bm_unit: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    national_grid_bm_unit: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
        index=True,
    )
    metric: Mapped[str] = mapped_column(String(160), index=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_field: Mapped[str] = mapped_column(String(120), index=True)
    unit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    raw_json: Mapped[str] = mapped_column(Text)
    collected_at: Mapped[DateTime] = mapped_column(DateTime)


class GridStatusDatasetMetadataRow(Base):
    __tablename__ = "gridstatus_dataset_metadata"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    dataset_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_chinese: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    earliest_available_time_utc: Mapped[str | None] = mapped_column(String(40), nullable=True)
    latest_available_time_utc: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_checked_time_utc: Mapped[str | None] = mapped_column(String(40), nullable=True)
    time_index_column: Mapped[str | None] = mapped_column(String(120), nullable=True)
    publish_time_column: Mapped[str | None] = mapped_column(String(120), nullable=True)
    subseries_index_column: Mapped[str | None] = mapped_column(String(120), nullable=True)
    primary_key_columns_json: Mapped[str] = mapped_column(Text)
    all_columns_json: Mapped[str] = mapped_column(Text)
    number_of_rows_approximate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    table_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    data_frequency: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    publication_frequency: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_in_snowflake: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_published: Mapped[bool | None] = mapped_column(Boolean, nullable=True, index=True)
    created_at_utc: Mapped[str | None] = mapped_column(String(40), nullable=True)
    popularity_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_json: Mapped[str] = mapped_column(Text)
    collected_at: Mapped[DateTime] = mapped_column(DateTime)


class ElecheckClearPriceRecordRow(Base):
    __tablename__ = "elecheck_clear_price_records"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "endpoint",
            "area_code",
            "start_date",
            "end_date",
            "time96",
            "metric",
            "unit",
            name="uq_elecheck_clear_price_natural_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(120), index=True)
    endpoint: Mapped[str] = mapped_column(String(40), index=True)
    area_code: Mapped[str] = mapped_column(String(40), index=True)
    start_date: Mapped[Date] = mapped_column(Date, index=True)
    end_date: Mapped[Date] = mapped_column(Date, index=True)
    time96: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    metric: Mapped[str] = mapped_column(String(120), index=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(40))
    currency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    raw_json: Mapped[str] = mapped_column(Text)
    collected_at: Mapped[DateTime] = mapped_column(DateTime)


class ElecheckAreaRecordRow(Base):
    __tablename__ = "elecheck_area_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    area_name: Mapped[str] = mapped_column(String(120), index=True)
    area_code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    detail_point_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail_granularity: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    earliest_clear_price_date: Mapped[Date | None] = mapped_column(Date, nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(120), index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_json: Mapped[str] = mapped_column(Text)
    collected_at: Mapped[DateTime] = mapped_column(DateTime)


class ElecheckPurchasingRecordRow(Base):
    __tablename__ = "elecheck_purchasing_records"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "endpoint",
            "data_kind",
            "data_month",
            "province_name",
            "metric",
            "statistic",
            "related_province_name",
            name="uq_elecheck_purchasing_natural_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(120), index=True)
    endpoint: Mapped[str] = mapped_column(String(80), index=True)
    data_kind: Mapped[str] = mapped_column(String(80), index=True)
    data_month: Mapped[str] = mapped_column(String(20), index=True)
    province_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    metric: Mapped[str] = mapped_column(String(120), index=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(40))
    diff_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    statistic: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    related_province_name: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
        index=True,
    )
    raw_json: Mapped[str] = mapped_column(Text)
    collected_at: Mapped[DateTime] = mapped_column(DateTime)


class ElecheckPurchasingProvinceRecordRow(Base):
    __tablename__ = "elecheck_purchasing_area_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(120), index=True)
    province_name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    raw_json: Mapped[str] = mapped_column(Text)
    collected_at: Mapped[DateTime] = mapped_column(DateTime)


class ElecheckMechanismElectricityPriceRecordRow(Base):
    __tablename__ = "elecheck_mechanism_electricity_price_records"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "region_name",
            "category",
            name="uq_elecheck_mechanism_electricity_price_natural_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(120), index=True)
    region_name: Mapped[str] = mapped_column(String(120), index=True)
    region: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    clear_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(40))
    raw_json: Mapped[str] = mapped_column(Text)
    collected_at: Mapped[DateTime] = mapped_column(DateTime)


class DashboardDailyMetricRow(Base):
    __tablename__ = "dashboard_daily_metrics"
    __table_args__ = (
        UniqueConstraint(
            "metric_date",
            "source",
            "dataset",
            "category",
            "region",
            "dimension",
            "metric_name",
            "unit",
            name="uq_dashboard_daily_metric",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    metric_date: Mapped[Date] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String(120), index=True)
    dataset: Mapped[str] = mapped_column(String(160), index=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    region: Mapped[str] = mapped_column(String(160), index=True)
    dimension: Mapped[str] = mapped_column(String(160), index=True)
    metric_name: Mapped[str] = mapped_column(String(160), index=True)
    unit: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    record_count: Mapped[int] = mapped_column(Integer)
    avg_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    sum_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    spread_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    latest_raw_time: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rebuilt_at: Mapped[DateTime] = mapped_column(DateTime)


class ScheduledJobRow(Base):
    __tablename__ = "scheduled_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    job_type: Mapped[str] = mapped_column(String(40), index=True)
    spider_name: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    schedule_kind: Mapped[str] = mapped_column(String(40), index=True)
    schedule_time: Mapped[str] = mapped_column(String(10))
    date_mode: Mapped[str] = mapped_column(String(40), index=True)
    start_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    params_json: Mapped[str] = mapped_column(Text)
    windows_task_name: Mapped[str | None] = mapped_column(String(260), nullable=True, index=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime)
    updated_at: Mapped[DateTime] = mapped_column(DateTime)


class ScheduledJobRunRow(Base):
    __tablename__ = "scheduled_job_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(Integer, index=True)
    started_at: Mapped[DateTime] = mapped_column(DateTime, index=True)
    finished_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    message: Mapped[str] = mapped_column(Text)
    records_written: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_json: Mapped[str] = mapped_column(Text)


class AgentSettingRow(Base):
    __tablename__ = "agent_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    endpoint: Mapped[str] = mapped_column(String(500))
    model_id: Mapped[str] = mapped_column(String(240))
    advanced_model_id: Mapped[str | None] = mapped_column(String(240), nullable=True)
    model_profile: Mapped[str] = mapped_column(String(40), default="free")
    reasoning_effort: Mapped[str | None] = mapped_column(String(40), nullable=True)
    protocol: Mapped[str] = mapped_column(String(40), default="auto")
    detected_protocol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime)


class AgentSessionRow(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[DateTime] = mapped_column(DateTime, index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, index=True)


class AgentMessageRow(Base):
    __tablename__ = "agent_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(40), index=True)
    content_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[DateTime] = mapped_column(DateTime, index=True)


class AgentRunRow(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    protocol: Mapped[str] = mapped_column(String(40))
    model_id: Mapped[str] = mapped_column(String(240))
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    pending_context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_json: Mapped[str] = mapped_column(Text, default="{}")
    router_provider: Mapped[str | None] = mapped_column(String(160), nullable=True)
    upstream_model: Mapped[str | None] = mapped_column(String(240), nullable=True)
    router_alert_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stop_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime, index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, index=True)


class AgentToolCallRow(Base):
    __tablename__ = "agent_tool_calls"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    tool_name: Mapped[str] = mapped_column(String(160), index=True)
    risk_level: Mapped[str] = mapped_column(String(40), index=True)
    arguments_json: Mapped[str] = mapped_column(Text)
    arguments_hash: Mapped[str] = mapped_column(String(64))
    approval_status: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, index=True)


class AgentEventRow(Base):
    __tablename__ = "agent_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    stage: Mapped[str] = mapped_column(String(60), index=True)
    detail_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[DateTime] = mapped_column(DateTime, index=True)


class MarketAgentSettingRow(Base):
    __tablename__ = "market_agent_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    endpoint: Mapped[str] = mapped_column(String(500))
    model_id: Mapped[str] = mapped_column(String(240))
    advanced_model_id: Mapped[str | None] = mapped_column(String(240), nullable=True)
    model_profile: Mapped[str] = mapped_column(String(40), default="free")
    reasoning_effort: Mapped[str | None] = mapped_column(String(40), nullable=True)
    protocol: Mapped[str] = mapped_column(String(40), default="auto")
    detected_protocol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime)


class MarketAgentSessionRow(Base):
    __tablename__ = "market_agent_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[DateTime] = mapped_column(DateTime, index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, index=True)


class MarketAgentMessageRow(Base):
    __tablename__ = "market_agent_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(40), index=True)
    content_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[DateTime] = mapped_column(DateTime, index=True)


class MarketAgentRunRow(Base):
    __tablename__ = "market_agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    protocol: Mapped[str] = mapped_column(String(40))
    model_id: Mapped[str] = mapped_column(String(240))
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    pending_context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_json: Mapped[str] = mapped_column(Text, default="{}")
    router_provider: Mapped[str | None] = mapped_column(String(160), nullable=True)
    upstream_model: Mapped[str | None] = mapped_column(String(240), nullable=True)
    router_alert_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stop_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime, index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, index=True)


class MarketAgentToolCallRow(Base):
    __tablename__ = "market_agent_tool_calls"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    tool_name: Mapped[str] = mapped_column(String(160), index=True)
    risk_level: Mapped[str] = mapped_column(String(40), index=True)
    arguments_json: Mapped[str] = mapped_column(Text)
    arguments_hash: Mapped[str] = mapped_column(String(64))
    approval_status: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, index=True)


class MarketAgentEventRow(Base):
    __tablename__ = "market_agent_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    stage: Mapped[str] = mapped_column(String(60), index=True)
    detail_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[DateTime] = mapped_column(DateTime, index=True)


DEFAULT_ELECHECK_AREA_RECORDS = [
    ElecheckAreaRecord(
        area_name="山西",
        area_code="140000000000",
        detail_point_count=96,
        detail_granularity="15min",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="山东",
        area_code="370000000000",
        detail_point_count=24,
        detail_granularity="hourly",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="蒙西",
        area_code="150000000000",
        detail_point_count=24,
        detail_granularity="hourly",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="河北南网",
        area_code="130000000001",
        detail_point_count=96,
        detail_granularity="15min",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="蒙东",
        area_code="150000000001",
        detail_point_count=96,
        detail_granularity="15min",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="辽宁",
        area_code="210000000000",
        detail_point_count=96,
        detail_granularity="15min",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="吉林",
        area_code="220000000000",
        detail_point_count=96,
        detail_granularity="15min",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="黑龙江",
        area_code="230000000000",
        detail_point_count=96,
        detail_granularity="15min",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="安徽",
        area_code="340000000000",
        detail_point_count=96,
        detail_granularity="15min",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="江苏",
        area_code="320000000000",
        detail_point_count=96,
        detail_granularity="15min",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="浙江",
        area_code="330000000000",
        detail_point_count=48,
        detail_granularity="30min",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="福建",
        area_code="350000000000",
        detail_point_count=96,
        detail_granularity="15min",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="上海",
        area_code="310000000000",
        detail_point_count=96,
        detail_granularity="15min",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="湖北",
        area_code="420000000000",
        detail_point_count=24,
        detail_granularity="hourly",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="湖南",
        area_code="430000000000",
        detail_point_count=24,
        detail_granularity="hourly",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="江西",
        area_code="360000000000",
        detail_point_count=288,
        detail_granularity="5min",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="河南",
        area_code="410000000000",
        detail_point_count=24,
        detail_granularity="hourly",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="新疆",
        area_code="650000000000",
        detail_point_count=96,
        detail_granularity="15min",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="陕西",
        area_code="610000000000",
        detail_point_count=96,
        detail_granularity="15min",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="宁夏",
        area_code="640000000000",
        detail_point_count=24,
        detail_granularity="hourly",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="甘肃",
        area_code="620000000000",
        detail_point_count=96,
        detail_granularity="15min",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="青海",
        area_code="630000000000",
        detail_point_count=96,
        detail_granularity="15min",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="四川",
        area_code="510000000000",
        detail_point_count=96,
        detail_granularity="15min",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="重庆",
        area_code="500000000000",
        detail_point_count=96,
        detail_granularity="15min",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="广东",
        area_code="440000000000",
        detail_point_count=24,
        detail_granularity="hourly",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="广西",
        area_code="450000000000",
        detail_point_count=24,
        detail_granularity="hourly",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="云南",
        area_code="530000000000",
        detail_point_count=24,
        detail_granularity="hourly",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="贵州",
        area_code="520000000000",
        detail_point_count=24,
        detail_granularity="hourly",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
    ElecheckAreaRecord(
        area_name="海南",
        area_code="460000000000",
        detail_point_count=24,
        detail_granularity="hourly",
        note="Observed from half-year clearPrice/detail HAR.",
    ),
]


def get_engine():
    settings = get_settings()
    if settings.database_url.startswith("sqlite:///"):
        db_path = Path(settings.database_url.replace("sqlite:///", "", 1))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            settings.database_url,
            future=True,
            connect_args={"timeout": 60},
        )

        @event.listens_for(engine, "connect")
        def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA busy_timeout = 60000")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.close()

        return engine
    return create_engine(settings.database_url, future=True)


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    drop_redundant_gzpec_tables_if_exists(engine)
    drop_gzpec_info_source_column_if_exists(engine)
    add_gridstatus_description_chinese_column_if_missing(engine)
    add_elecheck_area_earliest_clear_price_date_column_if_missing(engine)
    add_elecheck_clear_price_chart_index_if_missing(engine)
    add_agent_router_metadata_columns_if_missing(engine)
    upsert_elecheck_area_records(DEFAULT_ELECHECK_AREA_RECORDS)


def add_agent_router_metadata_columns_if_missing(engine) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    definitions = {
        "router_provider": "VARCHAR(160)",
        "upstream_model": "VARCHAR(240)",
        "router_alert_count": "INTEGER",
    }
    with engine.begin() as connection:
        for table_name in ("agent_runs", "market_agent_runs"):
            if table_name not in existing_tables:
                continue
            existing_columns = {
                column["name"] for column in inspector.get_columns(table_name)
            }
            for column_name, sql_type in definitions.items():
                if column_name not in existing_columns:
                    connection.execute(
                        text(
                            f"ALTER TABLE {table_name} "
                            f"ADD COLUMN {column_name} {sql_type}"
                        )
                    )


def drop_redundant_gzpec_tables_if_exists(engine) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    redundant_tables = {"news_index_records", "news_detail_records"}
    with engine.begin() as connection:
        for table_name in sorted(redundant_tables & existing_tables):
            connection.execute(text(f"DROP TABLE IF EXISTS {table_name}"))


def drop_gzpec_info_source_column_if_exists(engine) -> None:
    inspector = inspect(engine)
    if "gzpec_news_records" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("gzpec_news_records")}
    if "info_source" not in columns:
        return

    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE gzpec_news_records DROP COLUMN info_source"))


def add_gridstatus_description_chinese_column_if_missing(engine) -> None:
    inspector = inspect(engine)
    if "gridstatus_dataset_metadata" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("gridstatus_dataset_metadata")}
    if "description_chinese" in columns:
        return

    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE gridstatus_dataset_metadata ADD COLUMN description_chinese TEXT"))


def add_elecheck_area_earliest_clear_price_date_column_if_missing(engine) -> None:
    inspector = inspect(engine)
    if "elecheck_area_records" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("elecheck_area_records")}
    if "earliest_clear_price_date" in columns:
        return

    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE elecheck_area_records ADD COLUMN earliest_clear_price_date DATE")
        )


def add_elecheck_clear_price_chart_index_if_missing(engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    if "elecheck_clear_price_records" not in inspector.get_table_names():
        return
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_elecheck_clear_price_chart_lookup "
                "ON elecheck_clear_price_records "
                "(area_code, endpoint, start_date, end_date, metric, time96)"
            )
        )


def get_session() -> Session:
    factory = sessionmaker(bind=get_engine(), future=True)
    return factory()


def list_tables() -> list[str]:
    inspector = inspect(get_engine())
    return sorted(inspector.get_table_names())


def list_elecheck_area_names() -> list[str]:
    with get_session() as session:
        rows = session.query(ElecheckAreaRecordRow).order_by(ElecheckAreaRecordRow.area_name).all()
    return [row.area_name for row in rows]


def resolve_elecheck_area_code(area_name: str) -> str:
    normalized_area_name = area_name.strip()
    if not normalized_area_name:
        raise ValueError("Elecheck area name cannot be empty.")

    with get_session() as session:
        row = (
            session.query(ElecheckAreaRecordRow)
            .filter_by(area_name=normalized_area_name)
            .one_or_none()
        )

    if row is None:
        available = ", ".join(list_elecheck_area_names())
        raise ValueError(
            f"Unknown Elecheck area: {area_name}. Available areas: {available}"
        )
    return row.area_code


def export_table_to_csv(table_name: str, output_path: Path) -> int:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name):
        raise ValueError(f"Invalid table name: {table_name}")

    available_tables = set(list_tables())
    if table_name not in available_tables:
        available = ", ".join(sorted(available_tables))
        raise ValueError(f"Unknown table: {table_name}. Available tables: {available}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    engine = get_engine()
    with engine.connect() as connection, output_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        result = connection.execute(text(f'SELECT * FROM "{table_name}"'))
        writer = csv.writer(file)
        writer.writerow(list(result.keys()))
        for row in result:
            writer.writerow(row)
            row_count += 1
    return row_count


def export_gridstatus_translation_tasks(
    output_path: Path,
    limit: int | None = None,
    include_done: bool = False,
) -> int:
    engine = get_engine()
    add_gridstatus_description_chinese_column_if_missing(engine)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with get_session() as session:
        query = session.query(GridStatusDatasetMetadataRow).filter(
            GridStatusDatasetMetadataRow.description.is_not(None),
        )
        if not include_done:
            query = query.filter(GridStatusDatasetMetadataRow.description_chinese.is_(None))
        query = query.order_by(
            GridStatusDatasetMetadataRow.source,
            GridStatusDatasetMetadataRow.dataset_id,
        )
        if limit is not None:
            query = query.limit(limit)
        rows = query.all()

    fieldnames = [
        "id",
        "dataset_id",
        "description",
        "description_chinese",
        "translation_status",
        "translation_error",
    ]
    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            description = row.description or ""
            writer.writerow(
                {
                    "id": row.id,
                    "dataset_id": row.dataset_id,
                    "description": description,
                    "description_chinese": row.description_chinese or "",
                    "translation_status": "done" if row.description_chinese else "pending",
                    "translation_error": "",
                }
            )
    return len(rows)


def preview_gridstatus_translation_import(input_path: Path) -> dict[str, int]:
    return import_gridstatus_translations(input_path=input_path, dry_run=True)


def import_gridstatus_translations(input_path: Path, dry_run: bool = False) -> dict[str, int]:
    if not input_path.exists():
        raise ValueError(f"Translation CSV does not exist: {input_path}")

    engine = get_engine()
    add_gridstatus_description_chinese_column_if_missing(engine)

    stats = {
        "total_rows": 0,
        "updated": 0,
        "skipped_empty_translation": 0,
        "skipped_missing_dataset_id": 0,
        "skipped_not_found": 0,
        "skipped_error_status": 0,
    }

    with input_path.open(newline="", encoding="utf-8-sig") as file, get_session() as session:
        reader = csv.DictReader(file)
        required_columns = {"dataset_id", "description_chinese"}
        missing_columns = required_columns - set(reader.fieldnames or [])
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise ValueError(f"Missing required columns: {missing}")

        for csv_row in reader:
            stats["total_rows"] += 1
            dataset_id = (csv_row.get("dataset_id") or "").strip()
            description_chinese = (csv_row.get("description_chinese") or "").strip()
            translation_status = (csv_row.get("translation_status") or "").strip().lower()

            if not dataset_id:
                stats["skipped_missing_dataset_id"] += 1
                continue
            if not description_chinese:
                stats["skipped_empty_translation"] += 1
                continue
            if translation_status == "error":
                stats["skipped_error_status"] += 1
                continue

            row = (
                session.query(GridStatusDatasetMetadataRow)
                .filter_by(dataset_id=dataset_id)
                .one_or_none()
            )
            if row is None:
                stats["skipped_not_found"] += 1
                continue

            if not dry_run:
                row.description_chinese = description_chinese
            stats["updated"] += 1

        if not dry_run:
            session.commit()

    return stats


def upsert_records(records: list[MarketRecord]) -> int:
    import json

    if not records:
        return 0

    written = 0
    with get_session() as session:
        for record in records:
            row = (
                session.query(MarketRecordRow)
                .filter_by(
                    source=record.source,
                    market=record.market,
                    region=record.region,
                    trade_date=record.trade_date,
                    metric=record.metric,
                    unit=record.unit,
                )
                .one_or_none()
            )
            if row is None:
                row = MarketRecordRow(
                    source=record.source,
                    market=record.market,
                    region=record.region,
                    trade_date=record.trade_date,
                    metric=record.metric,
                    unit=record.unit,
                    value=record.value,
                    currency=record.currency,
                    raw_json=json.dumps(record.raw, ensure_ascii=False),
                    collected_at=record.collected_at,
                )
                session.add(row)
                written += 1
            else:
                row.value = record.value
                row.currency = record.currency
                row.raw_json = json.dumps(record.raw, ensure_ascii=False)
                row.collected_at = record.collected_at
                written += 1
        session.commit()
    return written


def upsert_entsoe_records(records: list[EntsoeRecord]) -> int:
    import json

    if not records:
        return 0

    written = 0
    with get_session() as session:
        for record in records:
            row = (
                session.query(EntsoeRecordRow)
                .filter_by(dataset=record.dataset, row_key=record.row_key)
                .one_or_none()
            )
            values = record.model_dump(exclude={"raw"})
            values["raw_json"] = json.dumps(record.raw, ensure_ascii=False)
            if row is None:
                session.add(EntsoeRecordRow(**values))
            else:
                for field, value in values.items():
                    setattr(row, field, value)
            written += 1
        session.commit()
    return written


def upsert_elexon_records(records: list[ElexonRecord]) -> int:
    import json

    if not records:
        return 0

    written = 0
    with get_session() as session:
        for record in records:
            row = (
                session.query(ElexonRecordRow)
                .filter_by(dataset=record.dataset, row_key=record.row_key)
                .one_or_none()
            )
            values = record.model_dump(exclude={"raw"})
            values["raw_json"] = json.dumps(record.raw, ensure_ascii=False)
            if row is None:
                session.add(ElexonRecordRow(**values))
            else:
                for field, value in values.items():
                    setattr(row, field, value)
            written += 1
        session.commit()
    return written


def upsert_gzpec_news_records(records: list[GzpecNewsRecord]) -> int:
    import json

    if not records:
        return 0

    written = 0
    with get_session() as session:
        for record in records:
            row = session.query(GzpecNewsRecordRow).filter_by(url=record.url).one_or_none()
            content_json = json.dumps(
                [block.model_dump() for block in record.content_blocks],
                ensure_ascii=False,
            )
            if row is None:
                row = GzpecNewsRecordRow(
                    source=record.source,
                    category=record.category,
                    title=record.title,
                    url=record.url,
                    publish_date=record.publish_date,
                    index_url=record.index_url,
                    news_type=record.news_type,
                    content_json=content_json,
                    collected_at=record.collected_at,
                )
                session.add(row)
            else:
                row.source = record.source
                row.category = record.category
                row.title = record.title
                row.publish_date = record.publish_date
                row.index_url = record.index_url
                row.news_type = record.news_type
                row.content_json = content_json
                row.collected_at = record.collected_at
            written += 1
        session.commit()
    return written


def upsert_gridstatus_records(records: list[GridStatusRecord]) -> int:
    import json

    if not records:
        return 0

    written = 0
    with get_session() as session:
        for record in records:
            row = (
                session.query(GridStatusRecordRow)
                .filter_by(request_name=record.request_name, row_key=record.row_key)
                .one_or_none()
            )
            raw_json = json.dumps(record.raw, ensure_ascii=False)
            if row is None:
                row = GridStatusRecordRow(
                    request_name=record.request_name,
                    request_type=record.request_type,
                    dataset=record.dataset,
                    location=record.location,
                    row_key=record.row_key,
                    interval_start_utc=record.interval_start_utc,
                    interval_end_utc=record.interval_end_utc,
                    record_time_utc=record.record_time_utc,
                    raw_json=raw_json,
                    collected_at=record.collected_at,
                )
                session.add(row)
            else:
                row.request_type = record.request_type
                row.dataset = record.dataset
                row.location = record.location
                row.interval_start_utc = record.interval_start_utc
                row.interval_end_utc = record.interval_end_utc
                row.record_time_utc = record.record_time_utc
                row.raw_json = raw_json
                row.collected_at = record.collected_at
            written += 1
        session.commit()
    return written


def upsert_gridstatus_dataset_metadata_records(
    records: list[GridStatusDatasetMetadataRecord],
) -> int:
    import json

    if not records:
        return 0

    written = 0
    with get_session() as session:
        for record in records:
            row = (
                session.query(GridStatusDatasetMetadataRow)
                .filter_by(dataset_id=record.dataset_id)
                .one_or_none()
            )
            primary_key_columns_json = json.dumps(record.primary_key_columns, ensure_ascii=False)
            all_columns_json = json.dumps(record.all_columns, ensure_ascii=False)
            raw_json = json.dumps(record.raw, ensure_ascii=False)
            if row is None:
                row = GridStatusDatasetMetadataRow(
                    dataset_id=record.dataset_id,
                    name=record.name,
                    description=record.description,
                    description_chinese=record.description_chinese,
                    source=record.source,
                    status=record.status,
                    earliest_available_time_utc=record.earliest_available_time_utc,
                    latest_available_time_utc=record.latest_available_time_utc,
                    last_checked_time_utc=record.last_checked_time_utc,
                    time_index_column=record.time_index_column,
                    publish_time_column=record.publish_time_column,
                    subseries_index_column=record.subseries_index_column,
                    primary_key_columns_json=primary_key_columns_json,
                    all_columns_json=all_columns_json,
                    number_of_rows_approximate=record.number_of_rows_approximate,
                    table_type=record.table_type,
                    data_frequency=record.data_frequency,
                    source_url=record.source_url,
                    publication_frequency=record.publication_frequency,
                    is_in_snowflake=record.is_in_snowflake,
                    is_published=record.is_published,
                    created_at_utc=record.created_at_utc,
                    popularity_rank=record.popularity_rank,
                    raw_json=raw_json,
                    collected_at=record.collected_at,
                )
                session.add(row)
            else:
                row.name = record.name
                row.description = record.description
                if record.description_chinese:
                    row.description_chinese = record.description_chinese
                row.source = record.source
                row.status = record.status
                row.earliest_available_time_utc = record.earliest_available_time_utc
                row.latest_available_time_utc = record.latest_available_time_utc
                row.last_checked_time_utc = record.last_checked_time_utc
                row.time_index_column = record.time_index_column
                row.publish_time_column = record.publish_time_column
                row.subseries_index_column = record.subseries_index_column
                row.primary_key_columns_json = primary_key_columns_json
                row.all_columns_json = all_columns_json
                row.number_of_rows_approximate = record.number_of_rows_approximate
                row.table_type = record.table_type
                row.data_frequency = record.data_frequency
                row.source_url = record.source_url
                row.publication_frequency = record.publication_frequency
                row.is_in_snowflake = record.is_in_snowflake
                row.is_published = record.is_published
                row.created_at_utc = record.created_at_utc
                row.popularity_rank = record.popularity_rank
                row.raw_json = raw_json
                row.collected_at = record.collected_at
            written += 1
        session.commit()
    return written


def upsert_elecheck_clear_price_records(records: list[ElecheckClearPriceRecord]) -> int:
    import json

    if not records:
        return 0

    def natural_key(record_or_row) -> tuple:
        return (
            record_or_row.source,
            record_or_row.endpoint,
            record_or_row.area_code,
            record_or_row.start_date,
            record_or_row.end_date,
            record_or_row.time96,
            record_or_row.metric,
            record_or_row.unit,
        )

    sources = {record.source for record in records}
    endpoints = {record.endpoint for record in records}
    area_codes = {record.area_code for record in records}
    start_dates = [record.start_date for record in records]
    end_dates = [record.end_date for record in records]

    written = 0
    with get_session() as session:
        existing_rows = (
            session.query(ElecheckClearPriceRecordRow)
            .filter(
                ElecheckClearPriceRecordRow.source.in_(sources),
                ElecheckClearPriceRecordRow.endpoint.in_(endpoints),
                ElecheckClearPriceRecordRow.area_code.in_(area_codes),
                ElecheckClearPriceRecordRow.start_date >= min(start_dates),
                ElecheckClearPriceRecordRow.start_date <= max(start_dates),
                ElecheckClearPriceRecordRow.end_date >= min(end_dates),
                ElecheckClearPriceRecordRow.end_date <= max(end_dates),
            )
            .all()
        )
        rows_by_key = {natural_key(row): row for row in existing_rows}

        for record in records:
            key = natural_key(record)
            row = rows_by_key.get(key)
            raw_json = json.dumps(record.raw, ensure_ascii=False)
            if row is None:
                row = ElecheckClearPriceRecordRow(
                    source=record.source,
                    endpoint=record.endpoint,
                    area_code=record.area_code,
                    start_date=record.start_date,
                    end_date=record.end_date,
                    time96=record.time96,
                    metric=record.metric,
                    value=record.value,
                    unit=record.unit,
                    currency=record.currency,
                    raw_json=raw_json,
                    collected_at=record.collected_at,
                )
                session.add(row)
                rows_by_key[key] = row
            else:
                row.value = record.value
                row.currency = record.currency
                row.raw_json = raw_json
                row.collected_at = record.collected_at
            written += 1
        session.commit()
    return written


def upsert_elecheck_area_records(records: list[ElecheckAreaRecord]) -> int:
    import json

    if not records:
        return 0

    written = 0
    with get_session() as session:
        for record in records:
            row = (
                session.query(ElecheckAreaRecordRow)
                .filter_by(area_code=record.area_code)
                .one_or_none()
            )
            raw_json = json.dumps(record.raw, ensure_ascii=False)
            if row is None:
                row = ElecheckAreaRecordRow(
                    area_name=record.area_name,
                    area_code=record.area_code,
                    detail_point_count=record.detail_point_count,
                    detail_granularity=record.detail_granularity,
                    earliest_clear_price_date=record.earliest_clear_price_date,
                    source=record.source,
                    note=record.note,
                    raw_json=raw_json,
                    collected_at=record.collected_at,
                )
                session.add(row)
            else:
                row.area_name = record.area_name
                row.detail_point_count = record.detail_point_count
                row.detail_granularity = record.detail_granularity
                if record.earliest_clear_price_date is not None:
                    row.earliest_clear_price_date = record.earliest_clear_price_date
                row.source = record.source
                row.note = record.note
                row.raw_json = raw_json
                row.collected_at = record.collected_at
            written += 1
        session.commit()
    return written


def update_elecheck_area_earliest_clear_price_date(area_code: str, earliest_date: date) -> None:
    with get_session() as session:
        row = (
            session.query(ElecheckAreaRecordRow)
            .filter_by(area_code=area_code)
            .one_or_none()
        )
        if row is None:
            raise ValueError(f"Elecheck area code not found: {area_code}")
        row.earliest_clear_price_date = earliest_date
        session.commit()


def upsert_elecheck_purchasing_records(records: list[ElecheckPurchasingRecord]) -> int:
    import json

    if not records:
        return 0

    written = 0
    with get_session() as session:
        for record in records:
            row = (
                session.query(ElecheckPurchasingRecordRow)
                .filter_by(
                    source=record.source,
                    endpoint=record.endpoint,
                    data_kind=record.data_kind,
                    data_month=record.data_month,
                    province_name=record.province_name,
                    metric=record.metric,
                    statistic=record.statistic,
                    related_province_name=record.related_province_name,
                )
                .one_or_none()
            )
            raw_json = json.dumps(record.raw, ensure_ascii=False)
            if row is None:
                row = ElecheckPurchasingRecordRow(
                    source=record.source,
                    endpoint=record.endpoint,
                    data_kind=record.data_kind,
                    data_month=record.data_month,
                    province_name=record.province_name,
                    metric=record.metric,
                    value=record.value,
                    unit=record.unit,
                    diff_value=record.diff_value,
                    statistic=record.statistic,
                    related_province_name=record.related_province_name,
                    raw_json=raw_json,
                    collected_at=record.collected_at,
                )
                session.add(row)
            else:
                row.value = record.value
                row.unit = record.unit
                row.diff_value = record.diff_value
                row.raw_json = raw_json
                row.collected_at = record.collected_at
            written += 1
        session.commit()
    return written


def upsert_elecheck_purchasing_province_records(
    records: list[ElecheckPurchasingProvinceRecord],
) -> int:
    import json

    if not records:
        return 0

    written = 0
    with get_session() as session:
        for record in records:
            row = (
                session.query(ElecheckPurchasingProvinceRecordRow)
                .filter_by(province_name=record.province_name)
                .one_or_none()
            )
            raw_json = json.dumps(record.raw, ensure_ascii=False)
            if row is None:
                row = ElecheckPurchasingProvinceRecordRow(
                    source=record.source,
                    province_name=record.province_name,
                    raw_json=raw_json,
                    collected_at=record.collected_at,
                )
                session.add(row)
            else:
                row.source = record.source
                row.raw_json = raw_json
                row.collected_at = record.collected_at
            written += 1
        session.commit()
    return written


def upsert_elecheck_mechanism_electricity_price_records(
    records: list[ElecheckMechanismElectricityPriceRecord],
) -> int:
    import json

    if not records:
        return 0

    written = 0
    with get_session() as session:
        for record in records:
            row = (
                session.query(ElecheckMechanismElectricityPriceRecordRow)
                .filter_by(
                    source=record.source,
                    region_name=record.region_name,
                    category=record.category,
                )
                .one_or_none()
            )
            raw_json = json.dumps(record.raw, ensure_ascii=False)
            if row is None:
                row = ElecheckMechanismElectricityPriceRecordRow(
                    source=record.source,
                    region_name=record.region_name,
                    region=record.region,
                    category=record.category,
                    price=record.price,
                    clear_price=record.clear_price,
                    unit=record.unit,
                    raw_json=raw_json,
                    collected_at=record.collected_at,
                )
                session.add(row)
            else:
                row.region = record.region
                row.price = record.price
                row.clear_price = record.clear_price
                row.unit = record.unit
                row.raw_json = raw_json
                row.collected_at = record.collected_at
            written += 1
        session.commit()
    return written
