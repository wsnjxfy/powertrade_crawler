from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from powertrade_crawler.agent.elecheck_tools import (
    build_elecheck_tool_registry,
    resolve_sqlite_path,
)
from powertrade_crawler.agent.prompts import (
    FINAL_MODEL_CALL_PROMPT,
    FORMAT_CORRECTION_PROMPT,
    TOOL_BATCH_LIMIT_PROMPT,
    json_action_system_prompt,
    native_system_prompt,
)
from powertrade_crawler.agent.provider import (
    FreeLLMRouterProvider,
    LLMProvider,
    ProviderError,
    ProviderProtocolError,
    ProviderTimeoutError,
)
from powertrade_crawler.agent.repository import AgentRepository
from powertrade_crawler.agent.schemas import (
    AgentAnswer,
    AgentError,
    AgentProtocol,
    AgentRunResult,
    JsonFinalAction,
    JsonToolAction,
    PendingApproval,
    ProviderResponse,
    ProviderToolCall,
    RiskLevel,
    RunStatus,
)
from powertrade_crawler.agent.security import arguments_hash, redact_text, redact_value
from powertrade_crawler.agent.tools import ToolContext, ToolRegistry
from powertrade_crawler.elecheck_collection import clear_price_area_targets
from powertrade_crawler.llm_router import (
    FreeRouterManagementClient,
    load_free_router_config,
)


MAX_MODEL_CALLS = 8
MAX_TOOL_CALLS_PER_RESPONSE = 8
EventCallback = Callable[[dict[str, Any]], None]


def build_configured_provider(
    repository: AgentRepository,
) -> tuple[FreeLLMRouterProvider, dict[str, Any]]:
    config = repository.get_config()
    router_config = load_free_router_config()
    model_id = config["model_id"]
    if not model_id:
        raise ValueError("Agent 模型 ID 未配置。")
    with FreeRouterManagementClient(router_config) as router:
        router.ensure_running(start_if_needed=True)
        strategy = router.ensure_strategy_available(model_id)
    provider = FreeLLMRouterProvider(
        api_key=router_config.api_key,
        model_id=model_id,
        endpoint=router_config.api_base,
    )
    return provider, {
        **config,
        "endpoint": router_config.api_base,
        "active_model_id": model_id,
        "router_strategy": strategy,
    }


class AgentLoop:
    def __init__(
        self,
        provider: LLMProvider,
        *,
        repository: AgentRepository | None = None,
        registry: ToolRegistry | None = None,
        protocol: AgentProtocol | str = AgentProtocol.NATIVE,
        model_id: str = "",
        event_callback: EventCallback | None = None,
        max_model_calls: int = MAX_MODEL_CALLS,
        close_provider_after_run: bool = False,
    ) -> None:
        self.provider = provider
        self.repository = repository or AgentRepository()
        self.registry = registry or build_elecheck_tool_registry()
        self.protocol = AgentProtocol(protocol)
        self.model_id = model_id
        self.event_callback = event_callback
        self.max_model_calls = max_model_calls
        self.close_provider_after_run = close_provider_after_run

    @classmethod
    def from_config(
        cls,
        *,
        repository: AgentRepository | None = None,
        event_callback: EventCallback | None = None,
    ) -> "AgentLoop":
        repository = repository or AgentRepository()
        provider, config = build_configured_provider(repository)
        configured_protocol = AgentProtocol(config["protocol"])
        if configured_protocol == AgentProtocol.AUTO:
            configured_protocol = AgentProtocol(
                config.get("detected_protocol") or AgentProtocol.NATIVE.value
            )
        return cls(
            provider,
            repository=repository,
            protocol=configured_protocol,
            model_id=config["active_model_id"],
            event_callback=event_callback,
            close_provider_after_run=True,
        )

    def chat(
        self,
        message: str,
        *,
        session_id: str | None = None,
    ) -> AgentRunResult:
        try:
            return self._chat(message, session_id=session_id)
        finally:
            if self.close_provider_after_run:
                self.provider.close()

    def _chat(
        self,
        message: str,
        *,
        session_id: str | None = None,
    ) -> AgentRunResult:
        if not message.strip():
            raise ValueError("Agent message cannot be empty.")
        session_id = self.repository.ensure_session(session_id)
        self.repository.add_message(
            session_id,
            role="user",
            content={"content": message.strip()},
        )
        run_id = self.repository.create_run(
            session_id,
            protocol=self.protocol.value,
            model_id=self.model_id,
        )
        messages = self._initial_messages(session_id)
        routed_end_date = deterministic_all_spot_update_end_date(message)
        if routed_end_date is not None:
            call = ProviderToolCall(
                id=f"direct-all-spot-{uuid4()}",
                name="elecheck_update_all_spot",
                arguments={"end_date": routed_end_date},
            )
            response = ProviderResponse(tool_calls=[call])
            if self.protocol == AgentProtocol.NATIVE:
                messages.append(
                    self._native_assistant_tool_message(response, [call])
                )
            else:
                messages.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "type": "tool_call",
                                "tool_name": call.name,
                                "arguments": call.arguments,
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
            self._event(
                run_id,
                session_id,
                "intent_routed",
                {
                    "tool_name": call.name,
                    "arguments": call.arguments,
                    "reason": "explicit_all_spot_update",
                },
            )
            routed_result = self._process_tool_calls(
                run_id=run_id,
                session_id=session_id,
                messages=messages,
                protocol=self.protocol,
                tool_calls=[call],
                usage={},
                format_corrections=0,
            )
            if routed_result is not None:
                return routed_result
        return self._run(
            run_id=run_id,
            session_id=session_id,
            messages=messages,
            model_calls=0,
            usage={},
            format_corrections=0,
        )

    def resume(self, tool_call_id: str) -> AgentRunResult:
        try:
            return self._resume(tool_call_id)
        finally:
            if self.close_provider_after_run:
                self.provider.close()

    def _resume(self, tool_call_id: str) -> AgentRunResult:
        call = self.repository.get_tool_call(tool_call_id)
        run = self.repository.get_run(call["run_id"])
        if run["status"] != RunStatus.AWAITING_APPROVAL.value:
            raise ValueError(f"Run {run['id']} is not awaiting approval.")
        context = run["pending_context"]
        if not isinstance(context, dict):
            raise ValueError("Pending Agent run has no resumable context.")
        if call["arguments_hash"] != context.get("arguments_hash"):
            raise ValueError("Tool parameters changed; prior approval is invalid.")
        if call["approval_status"] not in {"approved", "rejected"}:
            raise ValueError("Tool call is still waiting for an approval decision.")

        messages = context["messages"]
        if call["approval_status"] == "approved":
            result = self._execute_tool(
                run_id=run["id"],
                session_id=run["session_id"],
                call_id=call["id"],
                tool_name=call["tool_name"],
                arguments=call["arguments"],
            )
        else:
            result = {
                "ok": False,
                "error": {
                    "code": "tool_rejected",
                    "message": "用户拒绝了该操作，工具未执行。",
                },
            }
            self._event(
                run["id"],
                run["session_id"],
                "approval_rejected",
                {"tool_name": call["tool_name"], "tool_call_id": call["id"]},
            )
        self._append_tool_result(
            messages,
            protocol=AgentProtocol(run["protocol"]),
            provider_call_id=context["provider_call_id"],
            tool_name=call["tool_name"],
            result=result,
        )
        self.repository.update_run(
            run["id"],
            status=RunStatus.RUNNING.value,
            clear_pending_context=True,
        )
        remaining_tool_calls = [
            ProviderToolCall.model_validate(item)
            for item in context.get("remaining_tool_calls", [])
        ]
        if remaining_tool_calls:
            pending_result = self._process_tool_calls(
                run_id=run["id"],
                session_id=run["session_id"],
                messages=messages,
                protocol=AgentProtocol(run["protocol"]),
                tool_calls=remaining_tool_calls,
                usage=run["usage"],
                format_corrections=int(context.get("format_corrections", 0)),
            )
            if pending_result is not None:
                return pending_result
        return self._run(
            run_id=run["id"],
            session_id=run["session_id"],
            messages=messages,
            model_calls=int(run["model_calls"]),
            usage=run["usage"],
            format_corrections=int(context.get("format_corrections", 0)),
        )

    def _initial_messages(self, session_id: str) -> list[dict[str, Any]]:
        if self.protocol == AgentProtocol.JSON:
            system = json_action_system_prompt(self.registry.describe())
        else:
            system = native_system_prompt()
        return [
            {"role": "system", "content": system},
            *self.repository.model_messages(session_id),
        ]

    def _run(
        self,
        *,
        run_id: str,
        session_id: str,
        messages: list[dict[str, Any]],
        model_calls: int,
        usage: dict[str, int | float],
        format_corrections: int,
    ) -> AgentRunResult:
        protocol = AgentProtocol(self.repository.get_run(run_id)["protocol"])
        while model_calls < self.max_model_calls:
            if self.repository.is_stopped(run_id):
                return self._stopped(run_id, session_id, usage)
            final_model_call = model_calls + 1 == self.max_model_calls
            request_messages = messages
            if final_model_call:
                request_messages = [
                    *messages,
                    {"role": "system", "content": FINAL_MODEL_CALL_PROMPT},
                ]
            self._event(
                run_id,
                session_id,
                "model_request",
                {
                    "call_number": model_calls + 1,
                    "model_id": self.model_id,
                    "final_response_only": final_model_call,
                },
            )
            try:
                response = self._request_model_with_timeout_retry(
                    run_id=run_id,
                    session_id=session_id,
                    messages=request_messages,
                    protocol=protocol,
                    allow_tools=not final_model_call,
                )
            except ProviderError as exc:
                if (
                    protocol == AgentProtocol.NATIVE
                    and isinstance(exc, ProviderProtocolError)
                    and model_calls == 0
                ):
                    protocol = AgentProtocol.JSON
                    self.repository.update_run(run_id, status=RunStatus.RUNNING.value)
                    messages[0] = {
                        "role": "system",
                        "content": json_action_system_prompt(self.registry.describe()),
                    }
                    self._event(
                        run_id,
                        session_id,
                        "protocol_fallback",
                        {"from": "native", "to": "json"},
                    )
                    continue
                return self._failed(
                    run_id,
                    session_id,
                    AgentError(
                        code=getattr(exc, "code", "provider_error"),
                        message=redact_text(str(exc)),
                        retryable=getattr(exc, "retryable", False),
                    ),
                    usage,
                )
            except Exception as exc:
                return self._failed(
                    run_id,
                    session_id,
                    AgentError(
                        code="model_request_failed",
                        message=redact_text(str(exc)),
                        details={"error_type": type(exc).__name__},
                    ),
                    usage,
                )

            model_calls += 1
            usage = merge_usage(usage, response.usage)
            self.repository.update_run(
                run_id,
                model_calls=model_calls,
                usage=usage,
                router_provider=response.router_provider,
                upstream_model=response.upstream_model,
                router_alert_count=response.router_alert_count,
            )
            self._event(
                run_id,
                session_id,
                "model_completed",
                {
                    "model_calls": model_calls,
                    "finish_reason": response.finish_reason,
                    "protocol": protocol.value,
                    "router_provider": response.router_provider,
                    "upstream_model": response.upstream_model,
                    "router_alert_count": response.router_alert_count,
                },
            )
            if self.repository.is_stopped(run_id):
                return self._stopped(run_id, session_id, usage)

            try:
                tool_calls, final_answer = self._interpret_response(response, protocol)
            except (ValueError, ValidationError) as exc:
                if format_corrections < 1:
                    format_corrections += 1
                    messages.append(
                        {
                            "role": "assistant",
                            "content": response.content or "",
                        }
                    )
                    messages.append({"role": "user", "content": FORMAT_CORRECTION_PROMPT})
                    self._event(
                        run_id,
                        session_id,
                        "format_correction",
                        {"reason": redact_text(str(exc))},
                    )
                    continue
                return self._failed(
                    run_id,
                    session_id,
                    AgentError(
                        code="invalid_structured_answer",
                        message="模型最终答案无法通过 AgentAnswer Schema 校验。",
                        details={"validation": redact_text(str(exc))},
                    ),
                    usage,
                )

            if final_answer is not None:
                final_answer = self._ground_final_answer(run_id, final_answer)
                answer_payload = final_answer.model_dump(mode="json")
                self.repository.add_message(
                    session_id,
                    role="assistant",
                    content=answer_payload,
                    run_id=run_id,
                )
                self.repository.update_run(
                    run_id,
                    status=RunStatus.COMPLETED.value,
                    answer=answer_payload,
                    clear_pending_context=True,
                    usage=usage,
                )
                self._event(run_id, session_id, "conclusion_generated", {})
                return self._result(
                    ok=True,
                    status=RunStatus.COMPLETED,
                    run_id=run_id,
                    session_id=session_id,
                    answer=final_answer,
                    usage=usage,
                )

            if len(tool_calls) > MAX_TOOL_CALLS_PER_RESPONSE:
                messages.append(
                    {"role": "assistant", "content": response.content or ""}
                )
                messages.append({"role": "user", "content": TOOL_BATCH_LIMIT_PROMPT})
                self._event(
                    run_id,
                    session_id,
                    "tool_protocol_error",
                    {
                        "count": len(tool_calls),
                        "maximum": MAX_TOOL_CALLS_PER_RESPONSE,
                    },
                )
                continue
            if not tool_calls:
                if format_corrections < 1:
                    format_corrections += 1
                    messages.append(
                        {"role": "assistant", "content": response.content or ""}
                    )
                    messages.append({"role": "user", "content": FORMAT_CORRECTION_PROMPT})
                    continue
                return self._failed(
                    run_id,
                    session_id,
                    AgentError(
                        code="empty_model_response",
                        message="模型既未调用工具，也未返回结构化答案。",
                    ),
                    usage,
                )

            if protocol == AgentProtocol.NATIVE:
                messages.append(
                    self._native_assistant_tool_message(response, tool_calls)
                )
            else:
                messages.append(
                    {"role": "assistant", "content": response.content or ""}
                )
            pending_result = self._process_tool_calls(
                run_id=run_id,
                session_id=session_id,
                messages=messages,
                protocol=protocol,
                tool_calls=tool_calls,
                usage=usage,
                format_corrections=format_corrections,
            )
            if pending_result is not None:
                return pending_result

        return self._failed(
            run_id,
            session_id,
            AgentError(
                code="model_loop_limit",
                message=f"Agent 已达到每轮最多 {self.max_model_calls} 次模型调用。",
            ),
            usage,
        )

    def _process_tool_calls(
        self,
        *,
        run_id: str,
        session_id: str,
        messages: list[dict[str, Any]],
        protocol: AgentProtocol,
        tool_calls: list[ProviderToolCall],
        usage: dict[str, int | float],
        format_corrections: int,
    ) -> AgentRunResult | None:
        for index, call in enumerate(tool_calls):
            if self.repository.is_stopped(run_id):
                return self._stopped(run_id, session_id, usage)
            provider_call_id = call.id
            call_id = f"{run_id}:{provider_call_id}"[:160]
            self._event(
                run_id,
                session_id,
                "tool_proposed",
                {"tool_name": call.name, "arguments": call.arguments},
            )
            try:
                tool, parsed_args = self.registry.validate(call.name, call.arguments)
                normalized_args = parsed_args.model_dump(mode="json")
            except ValueError as exc:
                result = {
                    "ok": False,
                    "error": {
                        "code": "invalid_tool_call",
                        "message": redact_text(str(exc)),
                    },
                }
                self._append_tool_result(
                    messages,
                    protocol=protocol,
                    provider_call_id=provider_call_id,
                    tool_name=call.name,
                    result=result,
                )
                self._event(
                    run_id,
                    session_id,
                    "tool_validation_failed",
                    {"tool_name": call.name, "message": redact_text(str(exc))},
                )
                continue

            try:
                persisted_call = self.repository.create_tool_call(
                    call_id=call_id,
                    run_id=run_id,
                    session_id=session_id,
                    tool_name=tool.name,
                    risk_level=tool.risk_level,
                    arguments=normalized_args,
                )
            except ValueError as exc:
                return self._failed(
                    run_id,
                    session_id,
                    AgentError(
                        code="tool_call_identity_conflict",
                        message=redact_text(str(exc)),
                    ),
                    usage,
                )
            call_id = persisted_call["id"]
            if persisted_call["status"] == "success":
                result = persisted_call["result"]
                self._event(
                    run_id,
                    session_id,
                    "tool_reused",
                    {"tool_name": tool.name, "tool_call_id": call_id},
                )
            elif (
                tool.risk_level != RiskLevel.AUTO
                and persisted_call["approval_status"] == "pending"
            ):
                pending = PendingApproval(
                    tool_call_id=call_id,
                    run_id=run_id,
                    tool_name=tool.name,
                    risk_level=tool.risk_level,
                    arguments=normalized_args,
                    arguments_hash=arguments_hash(normalized_args),
                    side_effect=tool.side_effect,
                )
                self.repository.update_run(
                    run_id,
                    status=RunStatus.AWAITING_APPROVAL.value,
                    pending_context={
                        "messages": messages,
                        "provider_call_id": provider_call_id,
                        "tool_call_id": call_id,
                        "arguments_hash": pending.arguments_hash,
                        "remaining_tool_calls": [
                            item.model_dump(mode="json")
                            for item in tool_calls[index + 1 :]
                        ],
                        "format_corrections": format_corrections,
                    },
                    usage=usage,
                )
                self._event(
                    run_id,
                    session_id,
                    "approval_required",
                    pending.model_dump(mode="json"),
                )
                return self._result(
                    ok=False,
                    status=RunStatus.AWAITING_APPROVAL,
                    run_id=run_id,
                    session_id=session_id,
                    pending=pending,
                    usage=usage,
                )
            elif persisted_call["approval_status"] == "rejected":
                result = {
                    "ok": False,
                    "error": {
                        "code": "tool_rejected",
                        "message": "用户已拒绝相同参数的操作，工具未执行。",
                    },
                }
            else:
                result = self._execute_tool(
                    run_id=run_id,
                    session_id=session_id,
                    call_id=call_id,
                    tool_name=tool.name,
                    arguments=normalized_args,
                )
            self._append_tool_result(
                messages,
                protocol=protocol,
                provider_call_id=provider_call_id,
                tool_name=tool.name,
                result=result,
            )
        return None

    def _request_model_with_timeout_retry(
        self,
        *,
        run_id: str,
        session_id: str,
        messages: list[dict[str, Any]],
        protocol: AgentProtocol,
        allow_tools: bool = True,
    ) -> ProviderResponse:
        for attempt in range(2):
            try:
                return self.provider.complete(
                    messages=messages,
                    tools=(
                        self.registry.native_schemas()
                        if protocol == AgentProtocol.NATIVE and allow_tools
                        else None
                    ),
                    json_mode=protocol == AgentProtocol.JSON,
                )
            except ProviderTimeoutError:
                if attempt == 1:
                    raise
                self._event(
                    run_id,
                    session_id,
                    "model_retry",
                    {"reason": "timeout", "attempt": attempt + 2},
                )
        raise AssertionError("unreachable")

    def _ground_final_answer(
        self,
        run_id: str,
        answer: AgentAnswer,
    ) -> AgentAnswer:
        completed_calls = [
            row
            for row in self.repository.list_tool_calls(run_id)
            if row["status"] == "success"
        ]
        generated_files: list[str] = []
        for row in completed_calls:
            result = row.get("result") or {}
            data = result.get("data") if isinstance(result, dict) else None
            file_path = data.get("file") if isinstance(data, dict) else None
            if isinstance(file_path, str):
                generated_files.append(file_path)
        return answer.model_copy(
            update={
                "generated_files": generated_files,
                "executed_actions": [row["tool_name"] for row in completed_calls],
                "data_sources": ["Elecheck 易能电易查"],
            }
        )

    def _interpret_response(
        self,
        response: ProviderResponse,
        protocol: AgentProtocol,
    ) -> tuple[list[ProviderToolCall], AgentAnswer | None]:
        if protocol == AgentProtocol.NATIVE:
            if response.tool_calls:
                return response.tool_calls, None
            payload = parse_json_object(response.content or "")
            if "answer" in payload and isinstance(payload["answer"], dict):
                payload = payload["answer"]
            return [], AgentAnswer.model_validate(payload)

        payload = parse_json_object(response.content or "")
        if payload.get("type") == "tool_call":
            action = JsonToolAction.model_validate(payload)
            return [
                ProviderToolCall(
                    id=action.tool_call_id or f"json-{uuid4()}",
                    name=action.tool_name,
                    arguments=action.arguments,
                )
            ], None
        action = JsonFinalAction.model_validate(payload)
        return [], action.answer

    def _execute_tool(
        self,
        *,
        run_id: str,
        session_id: str,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        existing = self.repository.get_tool_call(call_id)
        if existing["status"] == "success":
            return existing["result"]
        try:
            self.repository.begin_tool_call(call_id)
        except ValueError as exc:
            self._event(
                run_id,
                session_id,
                "tool_failed",
                {
                    "tool_name": tool_name,
                    "tool_call_id": call_id,
                    "reason": "execution_state_unknown",
                },
            )
            return {
                "ok": False,
                "error": {
                    "code": "execution_state_unknown",
                    "message": redact_text(str(exc)),
                },
            }
        self._event(
            run_id,
            session_id,
            "tool_running",
            {"tool_name": tool_name, "tool_call_id": call_id},
        )
        execution = self.registry.execute(
            tool_name,
            arguments,
            ToolContext(
                session_id=session_id,
                run_id=run_id,
                tool_call_id=call_id,
                is_cancelled=lambda: self.repository.is_stopped(run_id),
                progress_callback=lambda detail: self._event(
                    run_id,
                    session_id,
                    "tool_progress",
                    {
                        **redact_value(detail),
                        "tool_name": tool_name,
                        "tool_call_id": call_id,
                    },
                ),
            ),
        )
        payload = execution.model_dump(mode="json")
        self.repository.finish_tool_call(
            call_id,
            result=payload if execution.ok else None,
            error=payload.get("error") if not execution.ok else None,
        )
        self._event(
            run_id,
            session_id,
            "tool_completed" if execution.ok else "tool_failed",
            {"tool_name": tool_name, "tool_call_id": call_id, "ok": execution.ok},
        )
        return payload

    @staticmethod
    def _native_assistant_tool_message(
        response: ProviderResponse,
        calls: list[ProviderToolCall],
    ) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": response.content,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in calls
            ],
        }

    @staticmethod
    def _append_tool_result(
        messages: list[dict[str, Any]],
        *,
        protocol: AgentProtocol,
        provider_call_id: str,
        tool_name: str,
        result: dict[str, Any],
    ) -> None:
        content = json.dumps(
            {
                "untrusted_tool_result": True,
                "tool_name": tool_name,
                "result": result,
            },
            ensure_ascii=False,
            default=str,
        )
        if protocol == AgentProtocol.NATIVE:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": provider_call_id,
                    "name": tool_name,
                    "content": content,
                }
            )
        else:
            messages.append({"role": "user", "content": content})

    def _event(
        self,
        run_id: str,
        session_id: str,
        stage: str,
        detail: dict[str, Any],
    ) -> None:
        event = self.repository.add_event(run_id, session_id, stage, detail)
        if self.event_callback:
            try:
                self.event_callback(event)
            except Exception:
                pass

    def _failed(
        self,
        run_id: str,
        session_id: str,
        error: AgentError,
        usage: dict[str, int | float],
    ) -> AgentRunResult:
        completed_actions = [
            row["tool_name"]
            for row in self.repository.list_tool_calls(run_id)
            if row["status"] == "success"
        ]
        if completed_actions:
            error = error.model_copy(
                update={
                    "details": {
                        **error.details,
                        "completed_actions_before_failure": completed_actions,
                    }
                }
            )
        self.repository.update_run(
            run_id,
            status=RunStatus.FAILED.value,
            error=error.model_dump(mode="json"),
            usage=usage,
        )
        self._event(
            run_id,
            session_id,
            "failed",
            {"code": error.code, "message": error.message},
        )
        return self._result(
            ok=False,
            status=RunStatus.FAILED,
            run_id=run_id,
            session_id=session_id,
            error=error,
            usage=usage,
        )

    def _stopped(
        self,
        run_id: str,
        session_id: str,
        usage: dict[str, int | float],
    ) -> AgentRunResult:
        error = AgentError(
            code="run_stopped",
            message="用户已停止运行；未完成的模型结果已丢弃，后续工具不会执行。",
        )
        self.repository.update_run(
            run_id,
            status=RunStatus.STOPPED.value,
            error=error.model_dump(mode="json"),
            usage=usage,
        )
        self._event(run_id, session_id, "stopped", {})
        return self._result(
            ok=False,
            status=RunStatus.STOPPED,
            run_id=run_id,
            session_id=session_id,
            error=error,
            usage=usage,
        )

    def _result(
        self,
        *,
        ok: bool,
        status: RunStatus,
        run_id: str,
        session_id: str,
        answer: AgentAnswer | None = None,
        pending: PendingApproval | None = None,
        error: AgentError | None = None,
        usage: dict[str, int | float],
    ) -> AgentRunResult:
        return AgentRunResult(
            ok=ok,
            status=status,
            run_id=run_id,
            session_id=session_id,
            answer=answer,
            pending_approval=pending,
            error=error,
            events=self.repository.list_events(run_id),
            usage=usage,
        )


def deterministic_all_spot_update_end_date(
    message: str,
    *,
    today: date | None = None,
) -> str | None:
    compact = re.sub(r"\s+", "", message).lower()
    if "现货" not in compact or not any(
        verb in compact for verb in ("更新", "采集", "补采")
    ):
        return None
    if "elecheck" not in compact and "易能电易查" not in compact:
        return None
    if "从" in compact and len(re.findall(r"20\d{2}[-/年]\d{1,2}", compact)) > 1:
        return None

    try:
        area_names = {
            str(row["area_name"]).strip()
            for row in clear_price_area_targets(resolve_sqlite_path())
        }
    except (OSError, sqlite3.Error, ValueError):
        area_names = set()
    if any(area_name and area_name in message for area_name in area_names):
        return None

    current = today or date.today()
    for label, offset in (("前天", 2), ("昨天", 1), ("今天", 0)):
        if f"到{label}" in compact or f"截至{label}" in compact or f"截止{label}" in compact:
            return (current - timedelta(days=offset)).isoformat()

    match = re.search(
        r"(?:到|截至|截止)(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})(?:日)?",
        compact,
    )
    if match is None:
        return None
    try:
        return date(*(int(part) for part in match.groups())).isoformat()
    except ValueError:
        return None


def parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        first = text.find("{")
        last = text.rfind("}")
        payload = None
        if first >= 0 and last > first:
            try:
                payload = json.loads(text[first : last + 1])
            except json.JSONDecodeError:
                payload = None
        if payload is None:
            decoder = json.JSONDecoder()
            for index, character in enumerate(text):
                if character != "{":
                    continue
                try:
                    candidate, _end = decoder.raw_decode(text[index:])
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict):
                    payload = candidate
                    break
        if payload is None:
            raise ValueError("Model response is not valid JSON.")
    if not isinstance(payload, dict):
        raise ValueError("Model response JSON must be an object.")
    return payload


def merge_usage(
    current: dict[str, int | float],
    update: dict[str, int | float],
) -> dict[str, int | float]:
    result = dict(current)
    for key, value in update.items():
        result[key] = result.get(key, 0) + value
    return result
