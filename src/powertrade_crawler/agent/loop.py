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
from powertrade_crawler.failure_messages import (
    completed_change_explanation,
    explain_agent_failure,
)
from powertrade_crawler.credential_setup import credential_is_configured
from powertrade_crawler.elecheck_collection import clear_price_area_targets
from powertrade_crawler.llm_router import (
    FreeRouterManagementClient,
    load_free_router_config,
)
from powertrade_crawler.intent_parsing import contextualize_continuation, parse_clock_time


MAX_MODEL_CALLS = 8
MAX_TOOL_CALLS_PER_RESPONSE = 8
EventCallback = Callable[[dict[str, Any]], None]


def user_failure_explanation(error: AgentError) -> tuple[str, str]:
    """Turn internal failures into a concise, actionable user-facing explanation."""
    return explain_agent_failure(
        code=error.code,
        message=error.message,
        retryable=error.retryable,
        scope_label="Elecheck ",
    )


def deterministic_elecheck_boundary_answer(message: str) -> AgentAnswer | None:
    text = message.strip().lower()
    if any(token in text for token in ("天气", "下雨", "气温", "降水")):
        return AgentAnswer(
            conclusion="无法查询天气：Elecheck Agent 只处理易能电易查业务数据。",
            warnings=["没有调用外部天气服务；请切换到具备天气数据能力的工具。"],
        )
    if any(token in text for token in ("清空", "全删", "删除数据", "压缩数据库")):
        return AgentAnswer(
            conclusion="无法删除或维护业务数据：Elecheck Agent 没有数据删除或数据库维护工具。",
            warnings=["没有修改业务数据；请使用软件的数据维护页面并人工确认。"],
        )
    if any(token in text for token in ("authorization", "token", "凭据")) and any(
        token in text
        for token in ("替我", "帮我改", "直接改", "写进", "写入", "发给你", "接收")
    ):
        return AgentAnswer(
            conclusion="无法代写或接收 Elecheck 凭据：Agent 没有修改凭据或文件的工具。",
            warnings=["请使用“API 配置向导”在本机配置，不要把凭据发送到对话中。"],
        )
    if any(token in text for token in ("entso-e", "entsoe", "elexon", "德国")):
        return AgentAnswer(
            conclusion="无法完成跨来源请求：Elecheck Agent 只处理易能电易查数据。",
            warnings=["请切换到“多数据源 Agent”进行 ENTSO-E、Elexon 等来源的查询或比较。"],
        )
    if any(
        token in text
        for token in ("powershell", "cmd", "shell", "命令行", "执行命令", "跑个命令")
    ):
        return AgentAnswer(
            conclusion="无法执行 Shell 或系统命令：Elecheck Agent 没有命令执行工具。",
            warnings=["没有启动外部进程；请使用软件提供的受控功能入口。"],
        )
    if any(
        token in text
        for token in ("浏览文件", "任意文件", "打开.env", "读取.env", "凭据文件")
    ):
        return AgentAnswer(
            conclusion="无法浏览或读取任意本机文件：Elecheck Agent 只访问受控业务表和导出目录。",
            warnings=["没有读取文件；凭据只能通过“API 配置向导”检查配置状态。"],
        )
    return None


def deterministic_elecheck_read_route(message: str) -> tuple[str, dict[str, Any]] | None:
    text = message.strip().lower()
    if any(token in text for token in ("authorization", "token", "凭据")) and any(
        token in text for token in ("是否已配置", "检查", "配置状态", "有没有配置")
    ):
        return "elecheck_credential_status", {}
    credential_mentioned = any(
        token in text
        for token in (
            "api key",
            "apikey",
            "authorization",
            "token",
            "凭据",
            "密钥",
            "配置向导",
            "401",
            "403",
        )
    )
    setup_help_requested = any(
        token in text
        for token in (
            "配置",
            "申请",
            "注册",
            "怎么",
            "如何",
            "需要哪些",
            "新用户",
            "缺少",
            "失效",
            "修改",
            "替我",
            "401",
            "403",
            "向导",
        )
    )
    if credential_mentioned and setup_help_requested:
        return "elecheck_credential_setup_guide", {}
    if "导出" in text and "现货" in text:
        dates = _elecheck_message_dates(message)
        area = next(
            (
                name
                for name in (
                    "江苏",
                    "山西",
                    "山东",
                    "广东",
                    "浙江",
                    "安徽",
                    "福建",
                    "甘肃",
                    "蒙西",
                    "湖北",
                    "湖南",
                    "河南",
                    "河北",
                    "辽宁",
                )
                if name in message
            ),
            None,
        )
        if dates and area:
            series = "all"
            if any(token in text for token in ("只要日前", "仅日前", "只导日前")):
                series = "day_ahead"
            elif any(token in text for token in ("只要实时", "仅实时", "只导实时")):
                series = "real_time"
            elif "价差" in text and not any(
                token in text for token in ("别混价差", "不要价差", "不含价差")
            ):
                series = "spread"
            elif "日前" in text and "实时" not in text:
                series = "day_ahead"
            elif "实时" in text and "日前" not in text:
                series = "real_time"
            return "elecheck_export_spot", {
                "area": area,
                "selected_date": dates[0].isoformat(),
                "file_format": "png" if "png" in text else "csv",
                "series": series,
            }
    if "导出" in text and "代理购电" in text and any(
        token in text for token in ("最新", "全部省", "各省")
    ):
        return "elecheck_export_purchasing", {}
    if any(token in text for token in ("更新时间", "记录量", "多少条", "新到哪")) and any(
        token in text for token in ("三类", "现货", "代理购电", "机制", "elecheck")
    ):
        return "elecheck_data_freshness", {}
    schedule_creation = any(
        token in text for token in ("创建", "新建", "建个", "建一个", "发起创建")
    )
    if (
        "定时" in text
        and not schedule_creation
        and any(token in text for token in ("运行记录", "运行情况", "跑得", "失败", "结果"))
    ):
        return "elecheck_list_schedule_runs", {}
    if "定时任务" in text and any(token in text for token in ("列出", "哪些", "查看", "列表")):
        return "elecheck_list_schedules", {}
    return None


def deterministic_monthly_extrema_routes(
    message: str,
) -> list[tuple[str, dict[str, Any]]] | None:
    text = message.strip().lower()
    if "日均价" not in text or not any(token in text for token in ("最高", "最低")):
        return None
    match = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月", message)
    if match is None:
        match = re.search(r"(20\d{2})-(\d{1,2})", message)
    if match is None:
        return None
    year, month_number = (int(part) for part in match.groups())
    if not 1 <= month_number <= 12:
        return None
    month = f"{year:04d}-{month_number:02d}"
    area = next(
        (
            name
            for name in (
                "江苏",
                "山西",
                "山东",
                "广东",
                "浙江",
                "安徽",
                "福建",
                "甘肃",
                "蒙西",
                "湖北",
                "湖南",
                "河南",
                "河北",
                "辽宁",
            )
            if name in message
        ),
        None,
    )
    price_type = "day_ahead" if "日前" in text else "real_time"
    directions = [
        direction
        for token, direction in (("最高", "highest"), ("最低", "lowest"))
        if token in text
    ]
    return [
        (
            "elecheck_analyze_spot_monthly_extrema",
            {
                "month": month,
                "price_type": price_type,
                "direction": direction,
                "area": area,
                "limit": 5,
            },
        )
        for direction in directions
    ]


def _elecheck_action_failure(reason: str, guidance: str) -> AgentAnswer:
    return AgentAnswer(
        conclusion=f"当前无法发起操作：{reason}",
        warnings=[f"没有执行采集，也没有修改业务数据。{guidance}"],
    )


def _elecheck_message_dates(message: str) -> list[date]:
    values = []
    for raw in re.findall(r"20\d{2}-\d{2}-\d{2}", message):
        try:
            parsed = date.fromisoformat(raw)
        except ValueError:
            continue
        if parsed not in values:
            values.append(parsed)
    return values


def deterministic_elecheck_action(
    message: str,
    *,
    today: date | None = None,
) -> tuple[str, dict[str, Any]] | AgentAnswer | None:
    text = message.strip().lower()
    schedule_request = (
        "任务" in text
        and any(token in text for token in ("创建", "新建", "建个", "建一个"))
    ) or (
        any(token in text for token in ("每天", "每周", "每月", "定时"))
        and any(token in text for token in ("自动", "抓取", "采集", "更新", "采"))
        and any(token in text for token in ("elecheck", "易能电", "现货"))
    )
    if schedule_request:
        try:
            configured_area_names = {
                str(row["area_name"]).strip()
                for row in clear_price_area_targets(resolve_sqlite_path())
                if row.get("area_name")
            }
        except (OSError, sqlite3.Error, ValueError):
            configured_area_names = set()
        configured_area_names.update(
            {
                "江苏",
                "山西",
                "山东",
                "广东",
                "浙江",
                "安徽",
                "福建",
                "甘肃",
                "蒙西",
                "湖北",
                "湖南",
                "河南",
                "河北",
                "辽宁",
            }
        )
        area = next(
            (
                name
                for name in sorted(configured_area_names, key=len, reverse=True)
                if name in message
            ),
            None,
        )
        source_wide = "现货" not in text or any(
            token in text for token in ("来源新数据", "全部", "全量", "所有数据", "各类")
        )
        if area is None and not source_wide:
            return _elecheck_action_failure(
                "定时现货任务缺少地区。",
                "请指定一个 Elecheck 地区。",
            )
        hour, minute = parse_clock_time(message)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            return _elecheck_action_failure(
                "定时任务运行时间无效。",
                "请使用 00:00 至 23:59 的时间。",
            )
        name_match = re.search(r"(?:名字叫|名称为)\s*([^；;，,。]+)", message)
        name = (
            name_match.group(1).strip()
            if name_match
            else (
                f"{area or '全部地区'} Elecheck 增量更新"
                if source_wide
                else f"{area}昨日现货采集"
            )
        )
        schedule_kind = "daily"
        if "每周" in text:
            schedule_kind = "weekly"
        elif "每月" in text:
            schedule_kind = "monthly"
        return "elecheck_create_schedule", {
            "name": name,
            "spider_name": (
                "elecheck_source_update" if source_wide else "elecheck_clear_price"
            ),
            "schedule_kind": schedule_kind,
            "schedule_time": f"{hour:02d}:{minute:02d}",
            "enabled": not any(token in text for token in ("禁用", "先别", "不要启用")),
            "area": area,
            "date_mode": (
                "none" if source_wide else ("yesterday" if "昨天" in text else "none")
            ),
        }

    routed_end_date = deterministic_all_spot_update_end_date(message, today=today)
    if routed_end_date is not None:
        return "elecheck_update_all_spot", {"end_date": routed_end_date}

    is_action = any(
        token in text
        for token in (
            "采集",
            "补采",
            "帮我采",
            "采一下",
            "补一下",
            "采完",
            "更新",
            "刷新",
        )
    )
    if not is_action:
        return None
    category_count = sum(
        (
            "现货" in text,
            "代理购电" in text,
            any(token in text for token in ("机制电价", "增量机制")),
        )
    )
    if category_count > 1:
        return None
    if (
        any(token in text for token in ("机制电价", "增量机制"))
        and "代理购电" not in text
        and "现货" not in text
    ):
        return "elecheck_update_mechanism", {}
    if "代理购电" in text:
        months = []
        for raw in re.findall(r"20\d{2}-(?:0[1-9]|1[0-2])", message):
            if raw not in months:
                months.append(raw)
        if not months:
            return _elecheck_action_failure(
                "代理购电采集缺少月份范围。",
                "请提供 YYYY-MM 格式的开始和结束月份。",
            )
        return "elecheck_collect_purchasing", {
            "start_month": months[0],
            "end_month": months[-1],
        }
    if "现货" not in text:
        return None

    dates = _elecheck_message_dates(message)
    if not dates:
        return _elecheck_action_failure(
            "现货采集缺少日期范围。",
            "请提供开始和结束日期；单次最多 31 天。",
        )
    start, end = dates[0], dates[-1]
    if (end - start).days + 1 > 31:
        return _elecheck_action_failure(
            "现货单次采集范围超过 31 天。",
            "请把任务拆分为多个不超过 31 天的批次。",
        )
    try:
        area_names = {
            str(row["area_name"]).strip()
            for row in clear_price_area_targets(resolve_sqlite_path())
        }
    except (OSError, sqlite3.Error, ValueError):
        area_names = set()
    area = next((name for name in area_names if name and name in message), None)
    if area is None:
        area = next(
            (
                name
                for name in (
                    "江苏",
                    "山西",
                    "山东",
                    "广东",
                    "浙江",
                    "安徽",
                    "福建",
                    "甘肃",
                    "蒙西",
                    "湖北",
                    "湖南",
                    "河南",
                    "河北",
                    "辽宁",
                )
                if name in message
            ),
            None,
        )
    if area is None:
        return _elecheck_action_failure(
            "现货采集缺少可识别的地区。",
            "请指定一个 Elecheck 地区。",
        )
    return "elecheck_collect_spot", {
        "area": area,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }


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
        read_route = deterministic_elecheck_read_route(message)
        boundary_answer = deterministic_elecheck_boundary_answer(message)
        if boundary_answer is not None:
            self._event(
                run_id,
                session_id,
                "request_declined",
                {"reason": "unsupported_or_unsafe_capability"},
            )
            return self._complete_answer(
                run_id,
                session_id,
                boundary_answer,
                usage={},
            )
        extrema_routes = deterministic_monthly_extrema_routes(message)
        if extrema_routes is not None:
            return self._execute_direct_extrema(
                run_id,
                session_id,
                extrema_routes,
            )
        if read_route is not None:
            return self._execute_direct_read(
                run_id,
                session_id,
                read_route[0],
                read_route[1],
            )
        contextual_message = contextualize_continuation(
            message,
            self.repository.model_messages(session_id),
        )
        routed_action = deterministic_elecheck_action(contextual_message)
        if isinstance(routed_action, AgentAnswer):
            self._event(
                run_id,
                session_id,
                "collection_details_required",
                {"reason": routed_action.conclusion},
            )
            return self._complete_answer(
                run_id,
                session_id,
                routed_action,
                usage={},
            )
        if routed_action is not None:
            routed_tool_name, routed_arguments = routed_action
            call = ProviderToolCall(
                id=f"direct-all-spot-{uuid4()}",
                name=routed_tool_name,
                arguments=routed_arguments,
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
                    "reason": "deterministic_elecheck_action",
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
        if call["approval_status"] == "approved" and result.get("ok"):
            data = result.get("data") or {}
            tool_labels = {
                "elecheck_collect_spot": "Elecheck 现货采集",
                "elecheck_collect_purchasing": "代理购电采集",
                "elecheck_update_mechanism": "增量机制电价更新",
                "elecheck_update_all_spot": "全部地区现货更新",
                "elecheck_create_schedule": "定时任务创建",
            }
            record_count = next(
                (
                    data[key]
                    for key in (
                        "records_upserted",
                        "records_written",
                        "records_produced",
                    )
                    if isinstance(data.get(key), int)
                ),
                None,
            )
            count_text = f"，写入或更新 {record_count} 条记录" if record_count is not None else ""
            answer = AgentAnswer(
                conclusion=(
                    f"已审批并完成 {tool_labels.get(call['tool_name'], call['tool_name'])}"
                    f"{count_text}。"
                ),
                business_metrics=(
                    [
                        {
                            "name": "写入或更新记录数",
                            "value": record_count,
                            "unit": "条",
                        }
                    ]
                    if record_count is not None
                    else []
                ),
                units=["条"] if record_count is not None else [],
            )
            return self._complete_answer(
                run["id"],
                run["session_id"],
                answer,
                usage=run["usage"],
            )
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

    def _complete_answer(
        self,
        run_id: str,
        session_id: str,
        answer: AgentAnswer,
        *,
        usage: dict[str, int | float],
    ) -> AgentRunResult:
        answer = self._ground_final_answer(run_id, answer)
        payload = answer.model_dump(mode="json")
        self.repository.add_message(
            session_id,
            role="assistant",
            content=payload,
            run_id=run_id,
        )
        self.repository.update_run(
            run_id,
            status=RunStatus.COMPLETED.value,
            answer=payload,
            clear_pending_context=True,
            usage=usage,
        )
        self._event(run_id, session_id, "conclusion_generated", {})
        return self._result(
            ok=True,
            status=RunStatus.COMPLETED,
            run_id=run_id,
            session_id=session_id,
            answer=answer,
            usage=usage,
        )

    def _execute_direct_read(
        self,
        run_id: str,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> AgentRunResult:
        tool, parsed = self.registry.validate(tool_name, arguments)
        if tool.risk_level != RiskLevel.AUTO:
            raise ValueError("确定性只读路由不能执行需要审批的工具。")
        normalized_arguments = parsed.model_dump(mode="json")
        call_id = f"{run_id}:direct-{uuid4()}"[:160]
        record = self.repository.create_tool_call(
            call_id=call_id,
            run_id=run_id,
            session_id=session_id,
            tool_name=tool_name,
            risk_level=tool.risk_level,
            arguments=normalized_arguments,
        )
        self._event(
            run_id,
            session_id,
            "intent_routed",
            {"tool_name": tool_name, "arguments": normalized_arguments},
        )
        self._event(
            run_id,
            session_id,
            "tool_proposed",
            {"tool_name": tool_name, "arguments": normalized_arguments},
        )
        result = self._execute_tool(
            run_id=run_id,
            session_id=session_id,
            call_id=record["id"],
            tool_name=tool_name,
            arguments=normalized_arguments,
        )
        if not result.get("ok"):
            raw_error = result.get("error") or {
                "code": "tool_execution_failed",
                "message": "工具执行失败。",
            }
            return self._failed(
                run_id,
                session_id,
                AgentError.model_validate(raw_error),
                {},
            )
        answer = self._direct_tool_answer(tool_name, result.get("data") or {})
        return self._complete_answer(
            run_id,
            session_id,
            answer,
            usage={},
        )

    def _execute_direct_extrema(
        self,
        run_id: str,
        session_id: str,
        routes: list[tuple[str, dict[str, Any]]],
    ) -> AgentRunResult:
        payloads = []
        for tool_name, arguments in routes:
            tool, parsed = self.registry.validate(tool_name, arguments)
            normalized_arguments = parsed.model_dump(mode="json")
            call_id = f"{run_id}:direct-{uuid4()}"[:160]
            record = self.repository.create_tool_call(
                call_id=call_id,
                run_id=run_id,
                session_id=session_id,
                tool_name=tool_name,
                risk_level=tool.risk_level,
                arguments=normalized_arguments,
            )
            self._event(
                run_id,
                session_id,
                "intent_routed",
                {"tool_name": tool_name, "arguments": normalized_arguments},
            )
            self._event(
                run_id,
                session_id,
                "tool_proposed",
                {"tool_name": tool_name, "arguments": normalized_arguments},
            )
            result = self._execute_tool(
                run_id=run_id,
                session_id=session_id,
                call_id=record["id"],
                tool_name=tool_name,
                arguments=normalized_arguments,
            )
            if not result.get("ok"):
                raw_error = result.get("error") or {
                    "code": "tool_execution_failed",
                    "message": "工具执行失败。",
                }
                return self._failed(
                    run_id,
                    session_id,
                    AgentError.model_validate(raw_error),
                    {},
                )
            payloads.append(result.get("data") or {})

        snippets = []
        metrics = []
        warnings = []
        data_range = []
        for data in payloads:
            extreme = data.get("extreme") or {}
            direction = "最高" if data.get("direction") == "highest" else "最低"
            scope = data.get("scope") or {}
            scope_label = scope.get("area_name") or "全部可用地区等权平均"
            date_text = extreme.get("date") or "未知日期"
            value = extreme.get("average_price")
            unit = extreme.get("unit") or "CNY/MWh"
            display_value = f"{value:.2f}" if isinstance(value, (int, float)) else value
            snippets.append(
                f"{scope_label}{direction}日为 {date_text}，日均价 {display_value} {unit}"
            )
            metrics.append(
                {
                    "name": f"{direction}日均价",
                    "value": value,
                    "unit": unit,
                    "description": date_text,
                }
            )
            available = data.get("available_date_range") or {}
            if available.get("first_date") and available.get("last_date"):
                data_range.append(
                    f"{available['first_date']} 至 {available['last_date']}"
                )
            warnings.extend(str(item) for item in data.get("warnings") or [])
        answer = AgentAnswer(
            conclusion="；".join(snippets) + "。",
            data_range=list(dict.fromkeys(data_range)),
            business_metrics=metrics,
            units=["CNY/MWh"],
            warnings=list(dict.fromkeys(warnings)),
        )
        return self._complete_answer(
            run_id,
            session_id,
            answer,
            usage={},
        )

    @staticmethod
    def _direct_tool_answer(tool_name: str, data: dict[str, Any]) -> AgentAnswer:
        if tool_name == "elecheck_credential_setup_guide":
            return AgentAnswer(
                conclusion=str(
                    data.get("assistant_conclusion")
                    or "已检查 API 配置状态，请打开 API 配置向导继续。"
                ),
                warnings=[str(item) for item in data.get("warnings") or []],
                business_metrics=[
                    {
                        "name": "已配置采集凭据",
                        "value": int(data.get("collection_configured") or 0),
                        "unit": "项",
                    },
                    {
                        "name": "采集必需凭据",
                        "value": int(data.get("collection_required") or 0),
                        "unit": "项",
                    },
                    {
                        "name": "可用免费模型渠道",
                        "value": int(
                            (data.get("router") or {}).get(
                                "available_free_providers", 0
                            )
                        ),
                        "unit": "个",
                    },
                ],
                units=["项", "个"],
                data_sources=[str(item) for item in data.get("data_sources") or []],
            )
        if tool_name == "elecheck_data_freshness":
            labels = {
                "spot_price": "现货价格",
                "purchasing_price": "代理购电",
                "mechanism_price": "增量机制电价",
            }
            summaries = []
            metrics = []
            for key, label in labels.items():
                row = data.get("tables", {}).get(key, {})
                records = int(row.get("records") or 0)
                latest = row.get("latest_business_period") or "无业务日期"
                summaries.append(f"{label} {records} 条，最新业务期 {latest}")
                metrics.append(
                    {
                        "name": f"{label}记录数",
                        "value": records,
                        "unit": "条",
                        "description": f"最新业务期：{latest}",
                    }
                )
            return AgentAnswer(
                conclusion="；".join(summaries) + "。",
                business_metrics=metrics,
                units=["条"],
                data_range=[f"统计日期：{data.get('as_of_date') or '未知'}"],
            )
        if tool_name == "elecheck_list_schedule_runs":
            runs = data.get("runs") or []
            if not runs:
                return AgentAnswer(
                    conclusion="当前没有 Elecheck 定时任务运行记录。",
                    warnings=["可能尚未创建任务，或现有任务尚未运行。"],
                )
            latest = runs[0]
            return AgentAnswer(
                conclusion=(
                    f"共找到 {len(runs)} 条近期运行记录；最近一次状态为 "
                    f"{latest.get('status') or '未知'}。"
                ),
                business_metrics=[
                    {"name": "近期运行记录数", "value": len(runs), "unit": "条"}
                ],
                warnings=(
                    [str(latest.get("message"))]
                    if latest.get("status") == "failed" and latest.get("message")
                    else []
                ),
            )
        if tool_name == "elecheck_list_schedules":
            jobs = data.get("jobs") or []
            return AgentAnswer(
                conclusion=(
                    f"当前共有 {len(jobs)} 个 Elecheck 定时任务。"
                    if jobs
                    else "当前没有 Elecheck 定时任务。"
                ),
                business_metrics=[
                    {"name": "定时任务数", "value": len(jobs), "unit": "个"}
                ],
            )
        if tool_name.startswith("elecheck_export_"):
            if data.get("empty"):
                reason = str(data.get("reason") or "所选范围没有可导出的数据")
                return AgentAnswer(
                    conclusion=f"无法导出：{reason}；未生成空文件。",
                    warnings=["请先采集对应日期的数据，或调整日期后重试。"],
                )
            file_path = str(data.get("file") or "")
            rows = data.get("rows_or_figures")
            return AgentAnswer(
                conclusion=(
                    f"导出已完成：{file_path}。"
                    if file_path
                    else "导出工具已完成。"
                ),
                business_metrics=(
                    [
                        {
                            "name": "导出行数或图表数",
                            "value": rows,
                            "unit": "项",
                        }
                    ]
                    if isinstance(rows, int)
                    else []
                ),
            )
        return AgentAnswer(conclusion=f"{tool_name} 已完成。")

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
                side_effect = tool.side_effect
                if tool.name in {
                    "elecheck_collect_spot",
                    "elecheck_update_all_spot",
                    "elecheck_collect_purchasing",
                    "elecheck_update_mechanism",
                    "elecheck_run_schedule",
                }:
                    credential_status = (
                        "已配置"
                        if credential_is_configured("elecheck_authorization")
                        else "待配置"
                    )
                    side_effect = (
                        f"{side_effect} Elecheck Authorization：{credential_status}。"
                    )
                pending = PendingApproval(
                    tool_call_id=call_id,
                    run_id=run_id,
                    tool_name=tool.name,
                    risk_level=tool.risk_level,
                    arguments=normalized_args,
                    arguments_hash=arguments_hash(normalized_args),
                    side_effect=side_effect,
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
                if len(response.tool_calls) == 1:
                    call = response.tool_calls[0]
                    pseudo_arguments = None
                    if call.name.replace("_", "").lower() == "agentanswer":
                        pseudo_arguments = dict(call.arguments)
                    elif "conclusion" in call.name:
                        try:
                            embedded = parse_json_object(call.name)
                        except ValueError:
                            embedded = None
                        if isinstance(embedded, dict) and embedded.get("conclusion"):
                            pseudo_arguments = embedded
                    if pseudo_arguments is not None:
                        arguments = pseudo_arguments
                        list_fields = {
                            "data_range",
                            "business_metrics",
                            "units",
                            "completeness",
                            "warnings",
                            "generated_files",
                            "executed_actions",
                            "data_sources",
                        }
                        for field in list_fields:
                            value = arguments.get(field)
                            if isinstance(value, str) and value.strip().startswith("["):
                                try:
                                    arguments[field] = json.loads(value)
                                except json.JSONDecodeError:
                                    pass
                        return [], AgentAnswer.model_validate(arguments)
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
        completed_actions, change_note = completed_change_explanation(
            self.repository.list_tool_calls(run_id)
        )
        if completed_actions:
            error = error.model_copy(
                update={
                    "details": {
                        **error.details,
                        "completed_actions_before_failure": completed_actions,
                    }
                }
            )
        conclusion, guidance = user_failure_explanation(error)
        answer = AgentAnswer(
            conclusion=conclusion,
            warnings=[change_note, guidance],
            executed_actions=completed_actions,
        )
        answer_payload = answer.model_dump(mode="json")
        self.repository.update_run(
            run_id,
            status=RunStatus.FAILED.value,
            answer=answer_payload,
            error=error.model_dump(mode="json"),
            usage=usage,
        )
        self.repository.add_message(
            session_id,
            role="assistant",
            content=answer_payload,
            run_id=run_id,
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
            answer=answer,
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
        answer = AgentAnswer(
            conclusion="任务已按用户要求停止。",
            warnings=["未完成的模型结果已丢弃，后续工具不会执行。"],
        )
        answer_payload = answer.model_dump(mode="json")
        self.repository.update_run(
            run_id,
            status=RunStatus.STOPPED.value,
            answer=answer_payload,
            error=error.model_dump(mode="json"),
            usage=usage,
        )
        self.repository.add_message(
            session_id,
            role="assistant",
            content=answer_payload,
            run_id=run_id,
        )
        self._event(run_id, session_id, "stopped", {})
        return self._result(
            ok=False,
            status=RunStatus.STOPPED,
            run_id=run_id,
            session_id=session_id,
            answer=answer,
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
        verb in compact for verb in ("更新", "采集", "补采", "补到")
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
