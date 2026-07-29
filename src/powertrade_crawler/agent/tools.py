from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from powertrade_crawler.agent.schemas import (
    AgentError,
    RiskLevel,
    ToolExecutionResult,
)
from powertrade_crawler.agent.security import redact_text, redact_value


ArgsT = TypeVar("ArgsT", bound=BaseModel)
ToolHandler = Callable[[BaseModel, "ToolContext"], dict[str, Any]]


@dataclass(frozen=True)
class ToolContext:
    session_id: str
    run_id: str
    tool_call_id: str
    is_cancelled: Callable[[], bool] = lambda: False
    progress_callback: Callable[[dict[str, Any]], None] = lambda _detail: None

    def report_progress(self, detail: dict[str, Any]) -> None:
        self.progress_callback(detail)


@dataclass(frozen=True)
class ToolDefinition(Generic[ArgsT]):
    name: str
    description: str
    args_model: type[ArgsT]
    handler: Callable[[ArgsT, ToolContext], dict[str, Any]]
    risk_level: RiskLevel = RiskLevel.AUTO
    side_effect: str = "只读，不修改本地或远程数据。"

    def native_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_model.model_json_schema(),
            },
        }

    def validate(self, arguments: dict[str, Any]) -> ArgsT:
        return self.args_model.model_validate(arguments)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition[Any]] = {}

    def register(self, tool: ToolDefinition[Any]) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition[Any]:
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"Unknown or unavailable tool: {name}")
        return tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    def list(self) -> list[ToolDefinition[Any]]:
        return [self._tools[name] for name in self.names()]

    def native_schemas(self) -> list[dict[str, Any]]:
        return [tool.native_schema() for tool in self.list()]

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "risk_level": tool.risk_level.value,
                "side_effect": tool.side_effect,
                "parameters": tool.args_model.model_json_schema(),
            }
            for tool in self.list()
        ]

    def validate(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> tuple[ToolDefinition[Any], BaseModel]:
        tool = self.get(name)
        try:
            parsed = tool.validate(arguments)
        except ValidationError as exc:
            raise ValueError(
                f"Invalid arguments for {name}: {exc.errors(include_url=False)}"
            ) from exc
        return tool, parsed

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolExecutionResult:
        try:
            tool, parsed = self.validate(name, arguments)
            if context.is_cancelled():
                return ToolExecutionResult(
                    ok=False,
                    error=AgentError(
                        code="run_stopped",
                        message="用户已停止运行，工具未执行。",
                    ),
                )
            data = tool.handler(parsed, context)
            return ToolExecutionResult(ok=True, data=redact_value(data))
        except ValueError as exc:
            return ToolExecutionResult(
                ok=False,
                error=AgentError(
                    code="invalid_tool_call",
                    message=redact_text(str(exc)),
                ),
            )
        except Exception as exc:
            return ToolExecutionResult(
                ok=False,
                error=AgentError(
                    code="tool_execution_failed",
                    message=redact_text(str(exc)),
                    details={"error_type": type(exc).__name__},
                ),
            )
