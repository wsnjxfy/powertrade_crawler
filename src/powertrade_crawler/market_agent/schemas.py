from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentProtocol(StrEnum):
    AUTO = "auto"
    NATIVE = "native"
    JSON = "json"


class RiskLevel(StrEnum):
    AUTO = "auto"
    APPROVAL = "approval"


class RunStatus(StrEnum):
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"
    REJECTED = "rejected"


class GroundedFact(StrictModel):
    fact_id: str
    label: str
    value: float | int | str | None = None
    unit: str | None = None
    source: str
    dataset: str
    time_basis: str
    calculation: str
    coverage: str | None = None


class BusinessMetric(StrictModel):
    name: str
    value: float | int | str | None = None
    unit: str | None = None
    description: str | None = None
    fact_id: str | None = None


class CompletenessMetric(StrictModel):
    name: str
    actual: int | None = None
    expected: int | None = None
    ratio: float | None = None
    description: str | None = None


class SeriesPoint(StrictModel):
    period: str
    value: float | int | None = None
    group: str | None = None


class SeriesResult(StrictModel):
    source: str
    dataset: str
    metric: str
    canonical_metric: str
    area: str | None = None
    time_basis: str
    aggregation: str
    unit: str | None = None
    currency: str | None = None
    points: list[SeriesPoint] = Field(default_factory=list)
    facts: list[GroundedFact] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ComparisonResult(StrictModel):
    comparable: bool
    status: Literal["comparable", "side_by_side_only"]
    comparison_basis: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    series: list[SeriesResult] = Field(default_factory=list)
    differences: list[BusinessMetric] = Field(default_factory=list)
    ranking: list[str] = Field(default_factory=list)


class DatasetCatalogItem(StrictModel):
    dataset: str
    source: str
    title: str
    metric: str | None = None
    canonical_metric: str | None = None
    unit: str | None = None
    currency: str | None = None
    time_basis: str | None = None
    status: str | None = None
    is_published: bool | None = None
    data_frequency: str | None = None
    earliest: str | None = None
    latest: str | None = None
    local_record_count: int = 0
    local_earliest: str | None = None
    local_latest: str | None = None
    dynamic: bool = False


class DatasetCatalogSummary(StrictModel):
    source: str
    display_name: str
    returned_count: int
    agent_supported_count: int | None = None
    project_catalog_count: int | None = None
    scope_note: str
    lookup_hint: str | None = None


class ReferenceItem(StrictModel):
    kind: Literal["news", "article", "metadata"]
    title: str
    url: str | None = None
    publish_date: str | None = None
    category: str | None = None
    news_type: str | None = None
    summary: str | None = None
    collected_at: str | None = None


class MarketAgentAnswer(StrictModel):
    conclusion: str = Field(description="直接、准确的中文结论。")
    data_range: list[str] = Field(default_factory=list)
    business_metrics: list[BusinessMetric] = Field(default_factory=list)
    units: list[str] = Field(default_factory=list)
    completeness: list[CompletenessMetric] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_files: list[str] = Field(default_factory=list)
    executed_actions: list[str] = Field(default_factory=list)
    data_sources: list[str] = Field(default_factory=list)
    comparison_status: Literal["none", "comparable", "side_by_side_only"] = "none"
    comparison_basis: list[str] = Field(default_factory=list)
    grounded_facts: list[GroundedFact] = Field(default_factory=list)
    datasets: list[DatasetCatalogItem] = Field(default_factory=list)
    dataset_catalog_summaries: list[DatasetCatalogSummary] = Field(
        default_factory=list
    )
    reference_items: list[ReferenceItem] = Field(default_factory=list)


class AgentError(StrictModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False


class ProviderToolCall(StrictModel):
    id: str
    name: str
    arguments: dict[str, Any]


class ProviderResponse(StrictModel):
    content: str | None = None
    tool_calls: list[ProviderToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int | float] = Field(default_factory=dict)
    raw_protocol: Literal["native", "json"] = "native"
    router_provider: str | None = None
    upstream_model: str | None = None
    router_alert_count: int | None = None


class JsonToolAction(StrictModel):
    type: Literal["tool_call"]
    tool_name: str
    arguments: dict[str, Any]
    tool_call_id: str | None = None


class JsonFinalAction(StrictModel):
    type: Literal["final"]
    answer: MarketAgentAnswer


class ToolExecutionResult(StrictModel):
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: AgentError | None = None


class PendingApproval(StrictModel):
    tool_call_id: str
    run_id: str
    tool_name: str
    risk_level: RiskLevel
    arguments: dict[str, Any]
    arguments_hash: str
    side_effect: str


class AgentRunResult(StrictModel):
    ok: bool
    status: RunStatus
    session_id: str
    run_id: str
    answer: MarketAgentAnswer | None = None
    pending_approval: PendingApproval | None = None
    error: AgentError | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, int | float] = Field(default_factory=dict)
