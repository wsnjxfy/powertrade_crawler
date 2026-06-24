from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class NewsContentBlock(BaseModel):
    sequence: int
    block_type: str = Field(description="Either text or image.")
    text: str | None = None
    url: str | None = None
    alt: str | None = None


class GzpecNewsRecord(BaseModel):
    source: str = Field(description="Source website or organization name.")
    category: str = Field(description="News category, for example 市场研究.")
    title: str
    url: str
    publish_date: date | None = None
    index_url: str = Field(description="The index page where this news link was found.")
    news_type: str = Field(description="green_certificate, spot_market, or ordinary.")
    content_blocks: list[NewsContentBlock] = Field(default_factory=list)
    collected_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def natural_key(self) -> str:
        return self.url


class GridStatusRecord(BaseModel):
    request_name: str
    request_type: str
    dataset: str | None = None
    location: str | None = None
    row_key: str
    interval_start_utc: str | None = None
    interval_end_utc: str | None = None
    record_time_utc: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    collected_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def natural_key(self) -> str:
        return "|".join([self.request_name, self.row_key])


class EntsoeRecord(BaseModel):
    dataset: str
    category: str
    title_en: str
    title_zh: str
    row_key: str
    document_type: str | None = None
    process_type: str | None = None
    business_type: str | None = None
    area: str | None = None
    in_domain: str | None = None
    out_domain: str | None = None
    time_series_id: str | None = None
    psr_type: str | None = None
    interval_start_utc: str | None = None
    interval_end_utc: str | None = None
    position: int | None = None
    resolution: str | None = None
    value: float | None = None
    value_field: str | None = None
    unit: str | None = None
    currency: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    collected_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def natural_key(self) -> str:
        return "|".join([self.dataset, self.row_key])


class GridStatusDatasetMetadataRecord(BaseModel):
    # GridStatus 数据集的唯一编号，例如 caiso_fuel_mix 或 spp_load_forecast_by_baa。
    # 后续查询具体数据时，会把它放进 /v1/datasets/{dataset_id}/query。
    dataset_id: str
    # 数据集的英文展示名称，便于人工识别。
    name: str | None = None
    # 数据集说明，通常会解释数据含义、采样频率、来源口径或特殊处理方式。
    description: str | None = None
    # 数据集说明的中文翻译，供业务人员检索和理解；由人工检查后写入。
    description_chinese: str | None = None
    # 数据来源或市场主体，例如 caiso、ercot、pjm、spp、aeso。
    source: str | None = None
    # 数据集状态，例如 active 表示当前可用，后续可用于过滤停用数据集。
    status: str | None = None
    # 该数据集最早可查询到的数据时间，UTC。
    earliest_available_time_utc: str | None = None
    # 该数据集最新可查询到的数据时间，UTC。
    latest_available_time_utc: str | None = None
    # GridStatus 最近一次检查该数据源的时间，UTC。
    last_checked_time_utc: str | None = None
    # 该数据集代表“运行时段”的主时间字段，例如 interval_start_utc 或 time_utc。
    time_index_column: str | None = None
    # 该数据集代表“发布时间/版本时间”的字段，预测类数据常见，例如 publish_time_utc。
    publish_time_column: str | None = None
    # 子序列字段，用于区分同一数据集内的不同区域、节点或分组，例如 baa、location。
    subseries_index_column: str | None = None
    # GridStatus 标记的主键字段组合，用于唯一确定一条数据。
    primary_key_columns: list[str] = Field(default_factory=list)
    # 数据集包含的全部字段说明，包括字段名、字段类型、是否数值、是否日期时间等。
    all_columns: list[dict[str, Any]] = Field(default_factory=list)
    # 估算行数，用于判断数据集规模，决定是否需要按时间范围分批抓取。
    number_of_rows_approximate: int | None = None
    # 表类型，通常是 table，保留该字段便于兼容其他数据形态。
    table_type: str | None = None
    # 数据频率，例如 5_MINUTES、1_HOUR、1_DAY。
    data_frequency: str | None = None
    # GridStatus 标注的原始数据来源地址，便于追溯到 ISO/RTO 官方来源。
    source_url: str | None = None
    # 数据发布频率；部分数据集为空，表示 GridStatus 暂未提供明确发布周期。
    publication_frequency: str | None = None
    # 是否已经进入 GridStatus 后端 Snowflake 数据仓库。
    is_in_snowflake: bool | None = None
    # 是否对当前 API 用户发布可用。
    is_published: bool | None = None
    # 该数据集在 GridStatus 中创建的时间，UTC。
    created_at_utc: str | None = None
    # 热度排名；为空时表示暂无排名或未统计。
    popularity_rank: int | None = None
    # GridStatus 返回的完整原始元数据，保留用于追溯和兼容未来新增字段。
    raw: dict[str, Any] = Field(default_factory=dict)
    # 本项目采集这条元数据的时间。
    collected_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def natural_key(self) -> str:
        return self.dataset_id


class ElecheckClearPriceRecord(BaseModel):
    source: str = "易能电易查"
    endpoint: str = Field(description="detail or statistics.")
    area_code: str
    start_date: date
    end_date: date
    time96: str | None = None
    metric: str
    value: float | None = None
    unit: str
    currency: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    collected_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def natural_key(self) -> str:
        return "|".join(
            [
                self.source,
                self.endpoint,
                self.area_code,
                self.start_date.isoformat(),
                self.end_date.isoformat(),
                self.time96 or "",
                self.metric,
                self.unit,
            ]
        )


class ElecheckAreaRecord(BaseModel):
    area_name: str
    area_code: str
    detail_point_count: int | None = None
    detail_granularity: str | None = None
    earliest_clear_price_date: date | None = None
    source: str = "易能电易查"
    note: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    collected_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def natural_key(self) -> str:
        return self.area_code


class ElecheckPurchasingRecord(BaseModel):
    source: str = "易能电易查"
    endpoint: str = Field(description="provinceMonthList, list, or chartData.")
    data_kind: str = Field(
        description="national_table, national_top, province_summary, or province_trend.",
    )
    data_month: str
    province_name: str | None = None
    metric: str
    value: float | None = None
    unit: str = "CNY/kWh"
    diff_value: float | None = None
    statistic: str | None = None
    related_province_name: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    collected_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def natural_key(self) -> str:
        return "|".join(
            [
                self.source,
                self.endpoint,
                self.data_kind,
                self.data_month,
                self.province_name or "",
                self.metric,
                self.statistic or "",
                self.related_province_name or "",
            ]
        )


class ElecheckPurchasingProvinceRecord(BaseModel):
    source: str = "易能电易查"
    province_name: str
    raw: dict[str, Any] = Field(default_factory=dict)
    collected_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def natural_key(self) -> str:
        return self.province_name


class ElecheckMechanismElectricityPriceRecord(BaseModel):
    source: str = "易能电易查"
    region_name: str
    region: str | None = None
    category: str
    price: float | None = None
    clear_price: float | None = None
    unit: str = "CNY/kWh"
    raw: dict[str, Any] = Field(default_factory=dict)
    collected_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def natural_key(self) -> str:
        return "|".join([self.source, self.region_name, self.category])


class MarketRecord(BaseModel):
    source: str = Field(description="Source website or exchange name.")
    market: str = Field(description="Market type, for example spot, forward, ancillary.")
    region: str = Field(description="Region, province, country, ISO, or market node.")
    trade_date: date
    metric: str = Field(description="Business metric, for example clearing_price or volume.")
    value: float
    unit: str
    currency: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    collected_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def natural_key(self) -> str:
        return "|".join(
            [
                self.source,
                self.market,
                self.region,
                self.trade_date.isoformat(),
                self.metric,
                self.unit,
            ]
        )
