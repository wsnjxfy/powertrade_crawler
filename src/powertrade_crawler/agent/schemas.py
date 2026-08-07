from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentProtocol(StrEnum):
    AUTO = "auto"
    NATIVE = "native"
    JSON = "json"


class RiskLevel(StrEnum):
    AUTO = "auto"
    APPROVAL = "approval"
    STRONG_APPROVAL = "strong_approval"


class RunStatus(StrEnum):
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"
    REJECTED = "rejected"


class BusinessMetric(StrictModel):
    name: str
    value: float | int | str | None = None
    unit: str | None = None
    description: str | None = None


class CompletenessMetric(StrictModel):
    name: str
    actual: int | None = None
    expected: int | None = None
    ratio: float | None = None
    description: str | None = None


class AgentAnswer(StrictModel):
    conclusion: str = Field(description="直接、准确的中文结论。")
    data_range: list[str] = Field(default_factory=list)
    business_metrics: list[BusinessMetric] = Field(default_factory=list)
    units: list[str] = Field(default_factory=list)
    completeness: list[CompletenessMetric] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_files: list[str] = Field(default_factory=list)
    executed_actions: list[str] = Field(default_factory=list)
    data_sources: list[str] = Field(default_factory=lambda: ["Elecheck 易能电易查"])

    @field_validator("data_sources")
    @classmethod
    def canonicalize_elecheck_sources(cls, _values: list[str]) -> list[str]:
        return ["Elecheck 易能电易查"]


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
    answer: AgentAnswer


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
    answer: AgentAnswer | None = None
    pending_approval: PendingApproval | None = None
    error: AgentError | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, int | float] = Field(default_factory=dict)
