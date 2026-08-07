from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from powertrade_crawler.llm_router import (
    FreeRouterManagementClient,
    load_free_router_config,
)
from powertrade_crawler.market_agent.data_tools import (
    build_market_tool_registry,
    collection_preview,
)
from powertrade_crawler.market_agent.prompts import (
    DIRECT_TOOL_EXPLANATION_PROMPT,
    FINAL_MODEL_CALL_PROMPT,
    FORMAT_CORRECTION_PROMPT,
    json_action_system_prompt,
    native_system_prompt,
)
from powertrade_crawler.market_agent.provider import (
    FreeLLMRouterProvider,
    LLMProvider,
    ProviderProtocolError,
    ProviderTimeoutError,
)
from powertrade_crawler.market_agent.repository import MarketAgentRepository
from powertrade_crawler.market_agent.schemas import (
    AgentError,
    AgentProtocol,
    AgentRunResult,
    DatasetCatalogItem,
    DatasetCatalogSummary,
    GroundedFact,
    JsonFinalAction,
    JsonToolAction,
    MarketAgentAnswer,
    PendingApproval,
    ProviderResponse,
    ProviderToolCall,
    ReferenceItem,
    RiskLevel,
    RunStatus,
)
from powertrade_crawler.market_agent.security import redact_text, redact_value
from powertrade_crawler.market_agent.tools import ToolContext, ToolRegistry


MAX_MODEL_CALLS = 6
MAX_TOOL_CALLS_PER_RESPONSE = 4
EventCallback = Callable[[dict[str, Any]], None]


def build_configured_provider(
    repository: MarketAgentRepository,
) -> tuple[FreeLLMRouterProvider, dict[str, Any]]:
    config = repository.get_config()
    router_config = load_free_router_config()
    model_id = config["model_id"]
    if not model_id:
        raise ValueError("多数据源 Agent 模型 ID 未配置。")
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


def deterministic_route(message: str) -> tuple[str, dict[str, Any]] | None:
    text = message.strip().lower()
    source = None
    for name, aliases in {
        "elecheck": ("elecheck", "易能电", "电易查"),
        "entsoe": ("entso-e", "entsoe", "欧洲"),
        "elexon": ("elexon", "英国"),
        "gridstatus": ("gridstatus", "北美"),
        "gzpec": ("广州电力交易中心", "gzpec", "广州交易中心"),
    }.items():
        if any(alias in text for alias in aliases):
            source = name
            break
    if any(token in text for token in ("数据概况", "数据状态", "更新时间", "数据覆盖")):
        return "market_get_data_overview", {"source": source}
    if "数据集" in text and any(token in text for token in ("哪些", "目录", "列表", "有什么")):
        return "market_list_datasets", {"source": source}
    return None


def candidate_tool_names(message: str, registry: ToolRegistry) -> list[str]:
    text = message.strip().lower()
    is_write = any(token in text for token in ("采集", "更新", "刷新", "抓取"))
    if is_write:
        if any(token in text for token in ("entso-e", "entsoe", "欧洲")):
            return ["market_collect_entsoe"]
        if any(token in text for token in ("elexon", "英国")):
            return ["market_collect_elexon"]
        if any(token in text for token in ("gridstatus", "北美")):
            return ["market_collect_gridstatus"]
        if any(token in text for token in ("广州电力交易中心", "gzpec", "新闻")):
            return ["market_collect_gzpec"]
        if any(
            token in text
            for token in ("elecheck", "易能电", "现货", "代理购电", "机制电价")
        ):
            return ["market_collect_elecheck"]
    if any(token in text for token in ("比较", "对比", "并列", "差额", "排名")):
        return ["market_compare_series"]
    if any(token in text for token in ("广州电力交易中心", "gzpec", "新闻", "公开信息")):
        return ["market_search_gzpec_news", "market_get_gzpec_article"]
    if "代理购电" in text:
        return ["market_analyze_elecheck_purchasing"]
    if any(token in text for token in ("机制电价", "增量机制", "燃煤基准")):
        return ["market_analyze_elecheck_mechanism"]
    if any(
        token in text
        for token in ("entso-e", "entsoe", "elexon", "gridstatus", "英国", "欧洲", "北美")
    ):
        return ["market_query_series"]
    if any(token in text for token in ("日前价", "实时价", "现货", "价差")):
        return ["market_analyze_elecheck_spot"]
    if any(token in text for token in ("导出", "csv", "png")):
        return ["market_export_result"]
    preferred = [
        "market_query_series",
        "market_compare_series",
        "market_get_data_overview",
        "market_list_datasets",
    ]
    return [name for name in preferred if name in registry.names()]


class MarketAgentLoop:
    def __init__(
        self,
        provider: LLMProvider,
        *,
        repository: MarketAgentRepository | None = None,
        registry: ToolRegistry | None = None,
        protocol: AgentProtocol | str = AgentProtocol.NATIVE,
        model_id: str = "",
        event_callback: EventCallback | None = None,
        max_model_calls: int = MAX_MODEL_CALLS,
        allow_protocol_fallback: bool = False,
        close_provider_after_run: bool = False,
    ) -> None:
        self.provider = provider
        self.repository = repository or MarketAgentRepository()
        self.registry = registry or build_market_tool_registry()
        self.protocol = AgentProtocol(protocol)
        self.model_id = model_id
        self.event_callback = event_callback
        self.max_model_calls = max_model_calls
        self.allow_protocol_fallback = allow_protocol_fallback
        self.close_provider_after_run = close_provider_after_run
        self._active_tool_names = self.registry.names()

    @classmethod
    def from_config(
        cls,
        *,
        repository: MarketAgentRepository | None = None,
        event_callback: EventCallback | None = None,
    ) -> "MarketAgentLoop":
        repository = repository or MarketAgentRepository()
        provider, config = build_configured_provider(repository)
        requested = AgentProtocol(config["protocol"])
        protocol = requested
        if requested == AgentProtocol.AUTO:
            protocol = AgentProtocol(
                config.get("detected_protocol") or AgentProtocol.NATIVE.value
            )
        return cls(
            provider,
            repository=repository,
            protocol=protocol,
            model_id=config["active_model_id"],
            event_callback=event_callback,
            allow_protocol_fallback=requested == AgentProtocol.AUTO,
            close_provider_after_run=True,
        )

    def chat(
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
        self._event(run_id, session_id, "run_started", {"protocol": self.protocol.value})
        try:
            route = deterministic_route(message)
            if route is not None:
                tool_name, arguments = route
                direct_call = ProviderToolCall(
                    id=f"direct-{uuid4()}",
                    name=tool_name,
                    arguments=arguments,
                )
                self._event(
                    run_id,
                    session_id,
                    "intent_routed",
                    {"tool_name": tool_name, "arguments": arguments},
                )
                payload = self._execute_tool_call(
                    run_id,
                    session_id,
                    direct_call,
                )
                tool_payloads = [(tool_name, payload)]
                if self._payload_is_program_renderable(payload):
                    answer = self._ground_answer(None, tool_payloads)
                    return self._complete(
                        run_id,
                        session_id,
                        answer,
                        model_calls=0,
                        usage={},
                    )
                self._active_tool_names = []
                messages = self._initial_messages(session_id)
                messages.extend(
                    [
                        self._assistant_tool_message(
                            ProviderResponse(),
                            [direct_call],
                        ),
                        self._tool_result_message(
                            direct_call.id,
                            direct_call.name,
                            payload,
                        ),
                        {
                            "role": "system",
                            "content": DIRECT_TOOL_EXPLANATION_PROMPT,
                        },
                    ]
                )
                self._event(
                    run_id,
                    session_id,
                    "model_explanation_required",
                    {"tool_name": tool_name},
                )
                return self._run_model_loop(
                    run_id,
                    session_id,
                    messages,
                    tool_payloads=tool_payloads,
                    model_calls=0,
                    usage={},
                    explanation_only=True,
                )

            self._active_tool_names = candidate_tool_names(message, self.registry)
            messages = self._initial_messages(session_id)
            return self._run_model_loop(
                run_id,
                session_id,
                messages,
                tool_payloads=[],
                model_calls=0,
                usage={},
            )
        except Exception as exc:
            return self._fail(run_id, session_id, exc)
        finally:
            if self.close_provider_after_run:
                self.provider.close()

    def resume_approval(
        self,
        tool_call_id: str,
        *,
        approved: bool,
        expected_arguments_hash: str | None = None,
    ) -> AgentRunResult:
        call = self.repository.get_tool_call(tool_call_id)
        run = self.repository.get_run(call["run_id"])
        run_id = run["id"]
        session_id = run["session_id"]
        if run["status"] != RunStatus.AWAITING_APPROVAL.value:
            raise ValueError("The run is not awaiting approval.")
        try:
            self.repository.decide_approval(
                tool_call_id,
                approved=approved,
                expected_arguments_hash=expected_arguments_hash,
            )
            if not approved:
                error = AgentError(
                    code="approval_rejected",
                    message="用户拒绝了待执行操作。",
                )
                self.repository.update_run(
                    run_id,
                    status=RunStatus.REJECTED.value,
                    error=error.model_dump(mode="json"),
                    clear_pending_context=True,
                )
                self._event(run_id, session_id, "approval_rejected", {})
                return AgentRunResult(
                    ok=False,
                    status=RunStatus.REJECTED,
                    session_id=session_id,
                    run_id=run_id,
                    error=error,
                    events=self.repository.list_events(run_id),
                    usage=run["usage"],
                )
            pending = run["pending_context"] or {}
            resolved_protocol = AgentProtocol(
                pending.get("protocol") or run["protocol"]
            )
            self.protocol = resolved_protocol
            self._active_tool_names = list(
                pending.get("active_tool_names") or self.registry.names()
            )
            messages = list(pending.get("messages") or [])
            tool_payloads = [
                (str(item["tool_name"]), dict(item["data"]))
                for item in pending.get("tool_payloads") or []
            ]
            self._event(
                run_id,
                session_id,
                "approval_granted",
                {"tool_name": call["tool_name"]},
            )
            payload = self._execute_tool_call(
                run_id,
                session_id,
                ProviderToolCall(
                    id=call["id"],
                    name=call["tool_name"],
                    arguments=call["arguments"],
                ),
                existing_call=True,
            )
            tool_payloads.append((call["tool_name"], payload))
            messages.append(self._tool_result_message(call["id"], call["tool_name"], payload))
            self.repository.update_run(
                run_id,
                status=RunStatus.RUNNING.value,
                clear_pending_context=True,
            )
            return self._run_model_loop(
                run_id,
                session_id,
                messages,
                tool_payloads=tool_payloads,
                model_calls=int(run["model_calls"]),
                usage=dict(run["usage"]),
            )
        except Exception as exc:
            return self._fail(run_id, session_id, exc)
        finally:
            if self.close_provider_after_run:
                self.provider.close()

    def _run_model_loop(
        self,
        run_id: str,
        session_id: str,
        messages: list[dict[str, Any]],
        *,
        tool_payloads: list[tuple[str, dict[str, Any]]],
        model_calls: int,
        usage: dict[str, int | float],
        explanation_only: bool = False,
    ) -> AgentRunResult:
        correction_attempted = False
        while model_calls < self.max_model_calls:
            if self.repository.is_stopped(run_id):
                return self._stopped(run_id, session_id, usage)
            force_final = explanation_only or (
                bool(tool_payloads) and model_calls >= self.max_model_calls - 1
            )
            if force_final and not explanation_only:
                messages.append({"role": "system", "content": FINAL_MODEL_CALL_PROMPT})
            try:
                response, calls_used = self._provider_complete(
                    run_id,
                    session_id,
                    messages,
                    force_final=force_final,
                    allow_fallback=model_calls + 1 < self.max_model_calls,
                )
            except ProviderProtocolError:
                if force_final and tool_payloads:
                    model_calls += 1
                    answer = self._ground_answer(None, tool_payloads)
                    answer.warnings.append(
                        "最终模型响应格式无效，已直接使用工具事实生成结论。"
                    )
                    return self._complete(
                        run_id,
                        session_id,
                        answer,
                        model_calls=model_calls,
                        usage=usage,
                    )
                raise
            except ProviderTimeoutError:
                model_calls += 1
                self.repository.update_run(
                    run_id,
                    model_calls=model_calls,
                    usage=usage,
                    protocol=self.protocol.value,
                )
                self._event(
                    run_id,
                    session_id,
                    "model_timeout",
                    {
                        "model_calls": model_calls,
                        "retryable": not tool_payloads,
                    },
                )
                if tool_payloads:
                    answer = self._ground_answer(None, tool_payloads)
                    answer.warnings.append(
                        "最终模型请求超时，已直接使用工具事实生成结论。"
                    )
                    return self._complete(
                        run_id,
                        session_id,
                        answer,
                        model_calls=model_calls,
                        usage=usage,
                    )
                raise
            model_calls += calls_used
            usage = self._merge_usage(usage, response.usage)
            self.repository.update_run(
                run_id,
                model_calls=model_calls,
                usage=usage,
                protocol=self.protocol.value,
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
                    "protocol": self.protocol.value,
                    "router_provider": response.router_provider,
                    "upstream_model": response.upstream_model,
                    "router_alert_count": response.router_alert_count,
                },
            )
            if self.repository.is_stopped(run_id):
                return self._stopped(run_id, session_id, usage)
            action = self._response_action(response)
            if isinstance(action, MarketAgentAnswer):
                answer = self._ground_answer(action, tool_payloads)
                return self._complete(
                    run_id,
                    session_id,
                    answer,
                    model_calls=model_calls,
                    usage=usage,
                )
            if isinstance(action, list):
                if force_final:
                    action = []
                if not action:
                    if tool_payloads:
                        answer = self._ground_answer(None, tool_payloads)
                        return self._complete(
                            run_id,
                            session_id,
                            answer,
                            model_calls=model_calls,
                            usage=usage,
                        )
                    if correction_attempted:
                        raise ProviderProtocolError("模型未返回工具调用或有效最终答案。")
                    messages.append({"role": "system", "content": FORMAT_CORRECTION_PROMPT})
                    correction_attempted = True
                    continue
                if len(action) > MAX_TOOL_CALLS_PER_RESPONSE:
                    raise ProviderProtocolError("模型单次返回的工具调用数量超过限制。")
                if len(action) > 1 and any(
                    self.registry.get(call.name).risk_level == RiskLevel.APPROVAL
                    for call in action
                ):
                    raise ProviderProtocolError("需要审批的操作必须单独调用。")
                prepared_calls = []
                for call in action:
                    definition, _parsed = self.registry.validate(call.name, call.arguments)
                    record = self.repository.create_tool_call(
                        call_id=call.id,
                        run_id=run_id,
                        session_id=session_id,
                        tool_name=call.name,
                        risk_level=definition.risk_level,
                        arguments=call.arguments,
                    )
                    call = call.model_copy(update={"id": record["id"]})
                    prepared_calls.append((call, definition, record))
                messages.append(
                    self._assistant_tool_message(
                        response,
                        [call for call, _definition, _record in prepared_calls],
                    )
                )
                for call, definition, record in prepared_calls:
                    if definition.risk_level == RiskLevel.APPROVAL:
                        preview = collection_preview(call.name, call.arguments)
                        side_effect = (
                            f"{definition.side_effect} "
                            f"预计请求：{preview['estimated_requests']}；"
                            f"范围：{preview.get('start') or '当前快照'}"
                            f" 至 {preview.get('end') or '当前快照'}。"
                        )
                        pending_context = {
                            "protocol": self.protocol.value,
                            "active_tool_names": self._active_tool_names,
                            "messages": messages,
                            "tool_payloads": [
                                {"tool_name": name, "data": data}
                                for name, data in tool_payloads
                            ],
                        }
                        self.repository.update_run(
                            run_id,
                            status=RunStatus.AWAITING_APPROVAL.value,
                            pending_context=pending_context,
                            protocol=self.protocol.value,
                        )
                        self._event(
                            run_id,
                            session_id,
                            "approval_required",
                            {
                                "tool_name": call.name,
                                "arguments": call.arguments,
                                "arguments_hash": record["arguments_hash"],
                                "side_effect": side_effect,
                                "preview": preview,
                            },
                        )
                        return AgentRunResult(
                            ok=False,
                            status=RunStatus.AWAITING_APPROVAL,
                            session_id=session_id,
                            run_id=run_id,
                            pending_approval=PendingApproval(
                                tool_call_id=call.id,
                                run_id=run_id,
                                tool_name=call.name,
                                risk_level=definition.risk_level,
                                arguments=call.arguments,
                                arguments_hash=record["arguments_hash"],
                                side_effect=side_effect,
                            ),
                            events=self.repository.list_events(run_id),
                            usage=usage,
                        )
                    payload = self._execute_tool_call(
                        run_id,
                        session_id,
                        call,
                        existing_call=True,
                    )
                    tool_payloads.append((call.name, payload))
                    messages.append(self._tool_result_message(call.id, call.name, payload))
                correction_attempted = False
                continue
        if tool_payloads:
            answer = self._ground_answer(None, tool_payloads)
            return self._complete(
                run_id,
                session_id,
                answer,
                model_calls=model_calls,
                usage=usage,
            )
        raise ProviderProtocolError("模型调用达到上限且没有可用工具事实。")

    def _provider_complete(
        self,
        run_id: str,
        session_id: str,
        messages: list[dict[str, Any]],
        *,
        force_final: bool,
        allow_fallback: bool,
    ) -> tuple[ProviderResponse, int]:
        try:
            if self.protocol == AgentProtocol.NATIVE:
                response = self.provider.complete(
                    messages=messages,
                    tools=(
                        None
                        if force_final
                        else [
                            self.registry.get(name).native_schema()
                            for name in self._active_tool_names
                        ]
                    ),
                    json_mode=force_final,
                )
            else:
                response = self.provider.complete(messages=messages, json_mode=True)
        except ProviderProtocolError:
            if (
                not self.allow_protocol_fallback
                or self.protocol != AgentProtocol.NATIVE
                or not allow_fallback
            ):
                raise
            self.protocol = AgentProtocol.JSON
            self.repository.set_config(detected_protocol=AgentProtocol.JSON.value)
            self.repository.update_run(run_id, protocol=AgentProtocol.JSON.value)
            messages[0] = {
                "role": "system",
                "content": json_action_system_prompt(
                    [
                        item
                        for item in self.registry.describe()
                        if item["name"] in self._active_tool_names
                    ]
                ),
            }
            self._event(
                run_id,
                session_id,
                "protocol_fallback",
                {"from": "native", "to": "json"},
            )
            return self.provider.complete(messages=messages, json_mode=True), 2
        if self.allow_protocol_fallback:
            self.repository.set_config(detected_protocol=self.protocol.value)
        return response, 1

    def _response_action(
        self,
        response: ProviderResponse,
    ) -> MarketAgentAnswer | list[ProviderToolCall]:
        if response.tool_calls:
            return response.tool_calls
        if not response.content:
            return []
        try:
            payload = json.loads(response.content)
        except json.JSONDecodeError:
            return []
        if self.protocol == AgentProtocol.JSON and isinstance(payload, dict):
            if payload.get("type") == "tool_call":
                try:
                    action = JsonToolAction.model_validate(payload)
                except ValidationError:
                    return []
                return [
                    ProviderToolCall(
                        id=action.tool_call_id or f"json-{uuid4()}",
                        name=action.tool_name,
                        arguments=action.arguments,
                    )
                ]
            if payload.get("type") == "final":
                try:
                    return JsonFinalAction.model_validate(payload).answer
                except ValidationError:
                    return []
        try:
            return MarketAgentAnswer.model_validate(payload)
        except ValidationError:
            return []

    def _execute_tool_call(
        self,
        run_id: str,
        session_id: str,
        call: ProviderToolCall,
        *,
        existing_call: bool = False,
    ) -> dict[str, Any]:
        definition, _parsed = self.registry.validate(call.name, call.arguments)
        if not existing_call:
            record = self.repository.create_tool_call(
                call_id=call.id,
                run_id=run_id,
                session_id=session_id,
                tool_name=call.name,
                risk_level=definition.risk_level,
                arguments=call.arguments,
            )
            call = call.model_copy(update={"id": record["id"]})
        current = self.repository.get_tool_call(call.id)
        if current["status"] == "success" and current["result"] is not None:
            self._event(
                run_id,
                session_id,
                "tool_reused",
                {"tool_name": call.name, "tool_call_id": call.id},
            )
            return dict(current["result"])
        self.repository.begin_tool_call(call.id)
        self._event(
            run_id,
            session_id,
            "tool_started",
            {"tool_name": call.name, "arguments": call.arguments},
        )
        context = ToolContext(
            session_id=session_id,
            run_id=run_id,
            tool_call_id=call.id,
            is_cancelled=lambda: self.repository.is_stopped(run_id),
            progress_callback=lambda detail: self._event(
                run_id,
                session_id,
                "tool_progress",
                {"tool_name": call.name, **detail},
            ),
        )
        result = self.registry.execute(call.name, call.arguments, context)
        if not result.ok:
            error = (
                result.error.model_dump(mode="json")
                if result.error
                else {"code": "tool_failed", "message": "工具执行失败。"}
            )
            self.repository.finish_tool_call(call.id, error=error)
            self._event(
                run_id,
                session_id,
                "tool_failed",
                {"tool_name": call.name, "error": error},
            )
            raise ValueError(error["message"])
        payload = result.data
        self.repository.finish_tool_call(call.id, result=payload)
        self._event(
            run_id,
            session_id,
            "tool_completed",
            {
                "tool_name": call.name,
                "records_written": payload.get("records_written"),
                "generated_files": payload.get("generated_files", []),
            },
        )
        return payload

    def _ground_answer(
        self,
        model_answer: MarketAgentAnswer | None,
        tool_payloads: list[tuple[str, dict[str, Any]]],
    ) -> MarketAgentAnswer:
        facts: list[GroundedFact] = []
        warnings: list[str] = []
        sources: list[str] = []
        files: list[str] = []
        actions: list[str] = []
        data_ranges: list[str] = []
        comparison_status = "none"
        comparison_basis: list[str] = []
        completeness = []
        datasets: list[DatasetCatalogItem] = []
        catalog_summaries: list[DatasetCatalogSummary] = []
        reference_items: list[ReferenceItem] = []
        for tool_name, payload in tool_payloads:
            for raw_fact in payload.get("facts") or []:
                try:
                    fact = GroundedFact.model_validate(raw_fact)
                except ValidationError:
                    continue
                facts.append(fact)
                if fact.coverage and fact.coverage not in data_ranges:
                    data_ranges.append(fact.coverage)
            warnings.extend(str(item) for item in payload.get("warnings") or [])
            sources.extend(str(item) for item in payload.get("data_sources") or [])
            files.extend(str(item) for item in payload.get("generated_files") or [])
            actions.extend(str(item) for item in payload.get("executed_actions") or [])
            if tool_name not in actions:
                actions.append(tool_name)
            if payload.get("comparison_status"):
                comparison_status = str(payload["comparison_status"])
            comparison_basis.extend(
                str(item) for item in payload.get("comparison_basis") or []
            )
            completeness.extend(payload.get("completeness") or [])
            for raw_dataset in payload.get("datasets") or []:
                if not isinstance(raw_dataset, dict):
                    continue
                selected = {
                    name: raw_dataset[name]
                    for name in DatasetCatalogItem.model_fields
                    if name in raw_dataset
                }
                try:
                    datasets.append(DatasetCatalogItem.model_validate(selected))
                except ValidationError:
                    continue
            for raw_summary in payload.get("catalog_summaries") or []:
                try:
                    catalog_summaries.append(
                        DatasetCatalogSummary.model_validate(raw_summary)
                    )
                except ValidationError:
                    continue
            reference_items.extend(self._reference_items(payload))

        conclusion = (
            model_answer.conclusion.strip()
            if model_answer and model_answer.conclusion.strip()
            else self._deterministic_conclusion(
                facts,
                files,
                actions,
                datasets,
                catalog_summaries,
                reference_items,
            )
        )
        if "未返回可量化业务事实" in conclusion:
            conclusion = self._deterministic_conclusion(
                facts,
                files,
                actions,
                datasets,
                catalog_summaries,
                reference_items,
            )
            if model_answer:
                warnings.append("已将不适用于非数值结果的模型兜底语句替换为工具内容。")
        if not self._numeric_claims_are_grounded(conclusion, facts):
            conclusion = self._deterministic_conclusion(
                facts,
                files,
                actions,
                datasets,
                catalog_summaries,
                reference_items,
            )
            warnings.append("模型数值未通过校验，已使用工具事实重新生成结论。")
        for summary in catalog_summaries:
            for required_text in (summary.scope_note, summary.lookup_hint):
                if required_text and required_text not in conclusion:
                    conclusion = f"{conclusion.rstrip()} {required_text}"
        if model_answer:
            warnings.extend(model_answer.warnings)

        metrics = [
            {
                "name": fact.label,
                "value": fact.value,
                "unit": fact.unit,
                "description": (
                    f"{fact.source} · {fact.dataset} · {fact.calculation}"
                ),
                "fact_id": fact.fact_id,
            }
            for fact in facts
        ]
        return MarketAgentAnswer(
            conclusion=conclusion,
            data_range=self._unique(data_ranges),
            business_metrics=metrics,
            units=self._unique([fact.unit for fact in facts if fact.unit]),
            completeness=completeness,
            warnings=self._unique(warnings),
            generated_files=self._unique(files),
            executed_actions=self._unique(actions),
            data_sources=self._unique(sources or [fact.source for fact in facts]),
            comparison_status=comparison_status,
            comparison_basis=self._unique(comparison_basis),
            grounded_facts=facts,
            datasets=self._unique_models(datasets, ("source", "dataset")),
            dataset_catalog_summaries=self._unique_models(
                catalog_summaries,
                ("source",),
            ),
            reference_items=self._unique_models(
                reference_items,
                ("kind", "url", "title"),
            ),
        )

    @staticmethod
    def _payload_is_program_renderable(payload: dict[str, Any]) -> bool:
        if "datasets" in payload or isinstance(payload.get("article"), dict):
            return True
        if payload.get("facts") or payload.get("generated_files"):
            return True
        if payload.get("series") or payload.get("comparison_status"):
            return True
        records = payload.get("records") or []
        return any(isinstance(item, dict) and item.get("title") for item in records)

    @staticmethod
    def _reference_items(payload: dict[str, Any]) -> list[ReferenceItem]:
        items: list[ReferenceItem] = []
        for record in payload.get("records") or []:
            if not isinstance(record, dict) or not record.get("title"):
                continue
            items.append(
                ReferenceItem(
                    kind="news",
                    title=str(record["title"]),
                    url=str(record["url"]) if record.get("url") else None,
                    publish_date=(
                        str(record["publish_date"])
                        if record.get("publish_date")
                        else None
                    ),
                    category=(
                        str(record["category"]) if record.get("category") else None
                    ),
                    news_type=(
                        str(record["news_type"])
                        if record.get("news_type")
                        else None
                    ),
                    collected_at=(
                        str(record["collected_at"])
                        if record.get("collected_at")
                        else None
                    ),
                )
            )
        article = payload.get("article")
        if isinstance(article, dict) and article.get("title"):
            text_blocks = [
                str(block["text"]).strip()
                for block in article.get("content_blocks") or []
                if isinstance(block, dict) and block.get("text")
            ]
            summary = " ".join(text_blocks).strip()
            if len(summary) > 300:
                summary = summary[:297].rstrip() + "..."
            items.append(
                ReferenceItem(
                    kind="article",
                    title=str(article["title"]),
                    url=str(article["url"]) if article.get("url") else None,
                    publish_date=(
                        str(article["publish_date"])
                        if article.get("publish_date")
                        else None
                    ),
                    category=(
                        str(article["category"]) if article.get("category") else None
                    ),
                    news_type=(
                        str(article["news_type"])
                        if article.get("news_type")
                        else None
                    ),
                    summary=summary or None,
                    collected_at=(
                        str(article["collected_at"])
                        if article.get("collected_at")
                        else None
                    ),
                )
            )
        return items

    @staticmethod
    def _numeric_claims_are_grounded(
        conclusion: str,
        facts: list[GroundedFact],
    ) -> bool:
        claims = re.findall(
            r"(-?\d+(?:\.\d+)?)\s*(CNY/kWh|CNY/MWh|EUR/MWh|GBP/MWh|MW|MWh|条|篇|%)",
            conclusion,
            flags=re.IGNORECASE,
        )
        if not claims:
            return True
        for raw_value, raw_unit in claims:
            value = float(raw_value)
            unit = raw_unit.lower()
            matched = False
            for fact in facts:
                if not isinstance(fact.value, (int, float)) or not fact.unit:
                    continue
                if fact.unit.lower() != unit:
                    continue
                tolerance = max(abs(float(fact.value)) * 1e-6, 1e-6)
                if abs(float(fact.value) - value) <= tolerance:
                    matched = True
                    break
            if not matched:
                return False
        return True

    @staticmethod
    def _deterministic_conclusion(
        facts: list[GroundedFact],
        files: list[str],
        actions: list[str],
        datasets: list[DatasetCatalogItem],
        catalog_summaries: list[DatasetCatalogSummary],
        reference_items: list[ReferenceItem],
    ) -> str:
        if catalog_summaries or datasets:
            parts = [item.scope_note for item in catalog_summaries]
            if datasets:
                names = "、".join(item.title for item in datasets[:8])
                suffix = "等" if len(datasets) > 8 else ""
                parts.append(f"本次返回 {len(datasets)} 项：{names}{suffix}。")
            else:
                parts.append("没有找到符合当前筛选条件的数据集。")
            parts.extend(
                item.lookup_hint
                for item in catalog_summaries
                if item.lookup_hint
            )
            return " ".join(parts)
        if reference_items:
            article_count = sum(item.kind == "article" for item in reference_items)
            if article_count:
                return f"已读取文章《{reference_items[0].title}》，正文摘要见参考内容。"
            names = "、".join(item.title for item in reference_items[:5])
            suffix = "等" if len(reference_items) > 5 else ""
            return f"已找到 {len(reference_items)} 条公开信息：{names}{suffix}。"
        if facts:
            snippets = []
            for fact in facts[:3]:
                unit = f" {fact.unit}" if fact.unit else ""
                snippets.append(f"{fact.label}为 {fact.value}{unit}")
            return "；".join(snippets) + "。"
        if files:
            return f"已生成 {len(files)} 个文件。"
        if actions:
            return "工具已完成处理，但结果中没有可直接展示的结构化内容。"
        return "查询完成，但没有找到符合条件的数据。"

    def _initial_messages(self, session_id: str) -> list[dict[str, Any]]:
        system = (
            native_system_prompt()
            if self.protocol == AgentProtocol.NATIVE
            else json_action_system_prompt(
                [
                    item
                    for item in self.registry.describe()
                    if item["name"] in self._active_tool_names
                ]
            )
        )
        return [{"role": "system", "content": system}, *self.repository.model_messages(session_id)]

    def _assistant_tool_message(
        self,
        response: ProviderResponse,
        calls: list[ProviderToolCall],
    ) -> dict[str, Any]:
        if self.protocol == AgentProtocol.JSON:
            call = calls[0]
            return {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "type": "tool_call",
                        "tool_name": call.name,
                        "arguments": call.arguments,
                        "tool_call_id": call.id,
                    },
                    ensure_ascii=False,
                ),
            }
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

    def _tool_result_message(
        self,
        call_id: str,
        tool_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        safe_payload = json.dumps(redact_value(payload), ensure_ascii=False, default=str)
        if self.protocol == AgentProtocol.JSON:
            return {
                "role": "user",
                "content": json.dumps(
                    {
                        "type": "tool_result",
                        "tool_name": tool_name,
                        "tool_call_id": call_id,
                        "result": json.loads(safe_payload),
                    },
                    ensure_ascii=False,
                ),
            }
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "name": tool_name,
            "content": safe_payload,
        }

    def _complete(
        self,
        run_id: str,
        session_id: str,
        answer: MarketAgentAnswer,
        *,
        model_calls: int,
        usage: dict[str, int | float],
    ) -> AgentRunResult:
        payload = answer.model_dump(mode="json")
        self.repository.update_run(
            run_id,
            status=RunStatus.COMPLETED.value,
            model_calls=model_calls,
            answer=payload,
            usage=usage,
            clear_pending_context=True,
        )
        self.repository.add_message(
            session_id,
            role="assistant",
            content=payload,
            run_id=run_id,
        )
        self._event(run_id, session_id, "conclusion_generated", {})
        return AgentRunResult(
            ok=True,
            status=RunStatus.COMPLETED,
            session_id=session_id,
            run_id=run_id,
            answer=answer,
            events=self.repository.list_events(run_id),
            usage=usage,
        )

    def _fail(
        self,
        run_id: str,
        session_id: str,
        exc: Exception,
    ) -> AgentRunResult:
        error = AgentError(
            code=getattr(exc, "code", "agent_run_failed"),
            message=redact_text(str(exc)),
            retryable=bool(getattr(exc, "retryable", False)),
            details={"error_type": type(exc).__name__},
        )
        try:
            run = self.repository.get_run(run_id)
            if run["status"] == RunStatus.AWAITING_APPROVAL.value:
                status = RunStatus.AWAITING_APPROVAL
            else:
                status = RunStatus.FAILED
                self.repository.update_run(
                    run_id,
                    status=status.value,
                    error=error.model_dump(mode="json"),
                )
            self._event(
                run_id,
                session_id,
                "failed",
                {"code": error.code, "message": error.message},
            )
            events = self.repository.list_events(run_id)
            usage = run["usage"]
        except Exception:
            status = RunStatus.FAILED
            events = []
            usage = {}
        return AgentRunResult(
            ok=False,
            status=status,
            session_id=session_id,
            run_id=run_id,
            error=error,
            events=events,
            usage=usage,
        )

    def _stopped(
        self,
        run_id: str,
        session_id: str,
        usage: dict[str, int | float],
    ) -> AgentRunResult:
        error = AgentError(code="run_stopped", message="用户已停止运行。")
        self.repository.update_run(
            run_id,
            status=RunStatus.STOPPED.value,
            error=error.model_dump(mode="json"),
            usage=usage,
        )
        self._event(run_id, session_id, "stopped", {})
        return AgentRunResult(
            ok=False,
            status=RunStatus.STOPPED,
            session_id=session_id,
            run_id=run_id,
            error=error,
            events=self.repository.list_events(run_id),
            usage=usage,
        )

    def _event(
        self,
        run_id: str,
        session_id: str,
        stage: str,
        detail: dict[str, Any],
    ) -> None:
        event = self.repository.add_event(run_id, session_id, stage, detail)
        if self.event_callback:
            self.event_callback(event)

    @staticmethod
    def _merge_usage(
        current: dict[str, int | float],
        addition: dict[str, int | float],
    ) -> dict[str, int | float]:
        result = dict(current)
        for key, value in addition.items():
            result[key] = result.get(key, 0) + value
        return result

    @staticmethod
    def _unique(values: list[Any]) -> list[Any]:
        result = []
        for value in values:
            if value not in result:
                result.append(value)
        return result

    @staticmethod
    def _unique_models(values: list[Any], fields: tuple[str, ...]) -> list[Any]:
        result = []
        seen = set()
        for value in values:
            key = tuple(getattr(value, field, None) for field in fields)
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result
