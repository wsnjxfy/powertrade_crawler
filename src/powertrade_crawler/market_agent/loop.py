from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from powertrade_crawler.intent_parsing import contextualize_continuation, parse_clock_time
from powertrade_crawler.failure_messages import (
    completed_change_explanation,
    explain_agent_failure,
)
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
from powertrade_crawler.scheduler import list_elecheck_source_update_areas


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
        return "market_get_credential_setup_guide", {}
    if any(token in text for token in ("定时任务", "计划任务")) and any(
        token in text for token in ("列出", "哪些", "查看", "列表", "状态")
    ):
        return "market_list_schedules", {}
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
    if (
        any(token in text for token in ("比较", "对比", "并列"))
        and any(token in text for token in ("entso-e", "entsoe"))
        and any(token in text for token in ("elexon", "英国"))
        and not _message_dates(message)
    ):
        return "market_compare_series", {
            "series": [
                {
                    "source": "entsoe",
                    "dataset": "entsoe_day_ahead_prices",
                    "aggregation": "daily",
                },
                {
                    "source": "elexon",
                    "dataset": "elexon_system_prices",
                    "aggregation": "daily",
                },
            ]
        }
    if (
        any(token in text for token in ("比较", "对比", "并列", "放一起", "放在一起"))
        and "德国" in text
        and any(token in text for token in ("江苏", "elecheck", "现货"))
        and not _message_dates(message)
    ):
        return "market_compare_series", {
            "series": [
                {
                    "source": "elecheck",
                    "dataset": "elecheck_spot",
                    "metric": "avg_day_ahead_price",
                    "area": "江苏",
                    "aggregation": "daily",
                },
                {
                    "source": "entsoe",
                    "dataset": "entsoe_day_ahead_prices",
                    "area": "DE-LU",
                    "aggregation": "daily",
                },
            ]
        }
    relative_days = None
    if "最近七天" in text:
        relative_days = 7
    elif any(token in text for token in ("最近30天", "最近 30 天", "最近一个月")):
        relative_days = 30
    if relative_days is not None and any(
        token in text for token in ("实时比日前", "价差", "实时", "日前", "现货")
    ):
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
        if area is not None:
            end = date.today()
            return "market_analyze_elecheck_spot", {
                "area": area,
                "start_date": (end - timedelta(days=relative_days - 1)).isoformat(),
                "end_date": end.isoformat(),
                "metric": "spread" if "实时比日前" in text or "价差" in text else "day_ahead",
                "aggregation": "daily",
            }
    if "导出" in text and any(token in text for token in ("elexon", "英国")) and any(
        token in text for token in ("系统价格", "结算价格")
    ):
        return (
            "market_export_result",
            {
                "series": [
                    {
                        "source": "elexon",
                        "dataset": "elexon_system_prices",
                        "aggregation": "daily",
                    }
                ],
                "formats": ["png" if "png" in text else "csv"],
            },
        )
    if any(token in text for token in ("elexon", "英国")) and not _message_dates(message):
        if any(token in text for token in ("风电", "wind")):
            return (
                "market_query_series",
                {
                    "source": "elexon",
                    "dataset": "elexon_wind_generation_forecast",
                    "metric": "wind_generation_forecast",
                    "aggregation": "daily",
                    "statistic": "average",
                },
            )
        if any(token in text for token in ("初始全国负荷", "负荷实绩")):
            return (
                "market_query_series",
                {
                    "source": "elexon",
                    "dataset": "elexon_initial_demand_outturn",
                    "metric": "initial_demand_outturn",
                    "aggregation": "daily",
                    "statistic": "average",
                },
            )
    if (
        any(token in text for token in ("广州电力交易中心", "广州交易中心", "gzpec"))
        and any(token in text for token in ("查找", "最新", "公开信息", "消息"))
        and any(token in text for token in ("现货", "现货市场"))
    ):
        return "market_search_gzpec_news", {"news_type": "spot_market", "limit": 5}
    return None


def deterministic_boundary_answer(message: str) -> MarketAgentAnswer | None:
    text = message.strip().lower()
    if any(token in text for token in ("天气", "下雨", "气温", "降水")):
        return MarketAgentAnswer(
            conclusion="无法查询天气或基于天气生成建议：多数据源 Agent 只访问已注册的电力市场数据工具。",
            warnings=["天气数据不在当前工具范围内，未调用任何数据工具，也未执行写操作。"],
        )
    if any(
        token in text
        for token in ("清空", "全删", "全清", "删除数据库", "删掉数据库")
    ):
        return MarketAgentAnswer(
            conclusion="无法删除或清空业务数据：多数据源 Agent 没有删除工具。",
            warnings=["数据库未被修改；如需维护数据，请使用软件的数据维护页面并人工确认。"],
        )
    if re.search(r"\b(update|delete|insert|drop|alter)\b", text):
        return MarketAgentAnswer(
            conclusion="无法执行 SQL 写入：多数据源 Agent 不提供任意 SQL 或数据修改能力。",
            warnings=["没有执行任何 SQL；可以改为查询负电价记录或导出原始数据。"],
        )
    if any(
        token in text
        for token in ("powershell", "cmd", "shell", "命令行", "执行命令", "跑个命令")
    ):
        return MarketAgentAnswer(
            conclusion="无法执行 Shell 或系统命令：多数据源 Agent 没有命令执行工具。",
            warnings=["没有启动外部进程；请使用软件提供的受控采集、导出和定时任务入口。"],
        )
    if any(
        token in text
        for token in ("浏览文件", "任意文件", "打开.env", "读取.env", "凭据文件")
    ):
        return MarketAgentAnswer(
            conclusion="无法浏览或读取任意本机文件：多数据源 Agent 只访问已注册的业务数据工具。",
            warnings=["没有读取文件；凭据只能通过“API 配置向导”检查配置状态。"],
        )
    if any(
        token in text
        for token in ("删除定时任务", "删掉定时任务", "把任务删", "任务删")
    ):
        return MarketAgentAnswer(
            conclusion="无法删除定时任务：多数据源 Agent 没有任务删除工具。",
            warnings=["任务未被修改；请在“定时任务 / 数据维护”页面人工确认后删除。"],
        )
    if any(
        token in text
        for token in ("token", "api key", "apikey", "authorization", "凭据")
    ) and any(
        token in text
        for token in ("替我", "帮我改", "直接改", "写进", "写入", "发给你", "接收")
    ) and not any(token in text for token in ("不发", "不想把", "不会把", "不要把")):
        return MarketAgentAnswer(
            conclusion="无法代写或接收凭据：Agent 没有读取、修改凭据或文件的工具。",
            warnings=["请使用“API 配置向导”在本机完成配置，不要把密钥发送到对话中。"],
        )
    return None


def deterministic_software_help_answer(message: str) -> MarketAgentAnswer | None:
    text = message.strip().lower()
    asks_how = any(
        token in text
        for token in ("怎么用", "如何使用", "怎么操作", "在哪里", "找不到", "使用流程", "新手")
    )
    if not asks_how:
        return None
    if any(token in text for token in ("api key", "apikey", "凭据", "密钥", "token")):
        return None
    if any(token in text for token in ("定时", "计划任务")):
        return MarketAgentAnswer(
            conclusion=(
                "设置定时采集时，请先说明数据源、数据集、运行频率和时间；Agent 会生成"
                "受控任务参数并等待审批。审批后只保存本地任务定义，不会立即采集。"
            ),
            warnings=[
                "如需软件关闭后仍自动运行，请打开“定时任务 / 数据维护”页面，为任务安装 Windows 触发器。",
                "运行结果和失败原因也在任务页面查看；缺少来源凭据时先到“API 配置向导”处理。",
            ],
        )
    if any(token in text for token in ("采集", "下载", "更新数据")):
        return MarketAgentAnswer(
            conclusion=(
                "采集数据时，请给出数据源、数据集或业务类型、地区和日期范围。Agent 会先"
                "展示范围、预计请求数、凭据状态和写库副作用，只有审批后才执行。"
            ),
            warnings=[
                "不确定数据集名称时，先问“某来源有哪些数据集”；不确定本地数据是否足够时，先问“数据更新到哪”。",
                "外部服务失败时，先检查 API 配置向导，再缩短日期范围并查看任务时间线中的具体原因。",
            ],
        )
    if "导出" in text:
        return MarketAgentAnswer(
            conclusion=(
                "先让 Agent 查询或分析目标数据，再说明 CSV 或 PNG 格式；导出文件只写入"
                "项目预设的 exports 目录，不需要审批。"
            ),
            warnings=["请明确来源、指标、地区、日期范围和文件格式，避免导出内容与预期不一致。"],
        )
    return MarketAgentAnswer(
        conclusion=(
            "新用户建议按“数据总览 → API 配置向导 → 单一数据源小范围采集 → Agent 查询/比较"
            " → 定时任务”的顺序使用。左侧导航覆盖全部功能，顶部搜索可按关键词快速跳转。"
        ),
        warnings=[
            "数据总览用于确认本地覆盖；API 配置向导用于申请、保存和验证凭据；所有 Agent 写数据操作都需要审批。"
        ],
    )


def _collection_failure(reason: str, guidance: str) -> MarketAgentAnswer:
    return MarketAgentAnswer(
        conclusion=f"当前无法发起采集：{reason}",
        warnings=[f"未执行采集，也没有修改业务数据。{guidance}"],
    )


def _message_dates(message: str) -> list[date]:
    values = []
    for raw in re.findall(r"20\d{2}-\d{2}-\d{2}", message):
        try:
            parsed = date.fromisoformat(raw)
        except ValueError:
            continue
        if parsed not in values:
            values.append(parsed)
    return values


def _has_schedule_intent(text: str) -> bool:
    explicit_schedule = any(
        token in text for token in ("定时任务", "计划任务", "定时采集")
    )
    recurring_schedule = any(token in text for token in ("每天", "每周", "每月")) and any(
        token in text
        for token in ("自动", "采集", "补采", "抓取", "更新", "安排", "创建", "任务")
    )
    return explicit_schedule or recurring_schedule


def deterministic_schedule_route(
    message: str,
) -> tuple[str, dict[str, Any]] | MarketAgentAnswer | None:
    text = message.strip().lower()
    schedule_intent = _has_schedule_intent(text)
    if not schedule_intent:
        return None
    if any(token in text for token in ("列出", "哪些", "查看", "列表", "状态")):
        return None

    hour, minute = parse_clock_time(message)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return _collection_failure(
            "定时任务运行时间无效。",
            "请使用 00:00 至 23:59 的本地时间。",
        )

    schedule_kind = "daily"
    if "每周" in text:
        schedule_kind = "weekly"
    elif "每月" in text:
        schedule_kind = "monthly"
    enabled = not any(token in text for token in ("禁用", "先别启用", "不要启用"))
    name_match = re.search(r"名字叫([^；;，,。]+)", message)

    dataset = None
    area = None
    label = None
    if any(token in text for token in ("entso-e", "entsoe", "欧洲", "德国", "法国")):
        area = "DE-LU" if any(token in text for token in ("德国", "de-lu")) else None
        if "法国" in text:
            area = "FR"
        if area is None:
            return _collection_failure(
                "ENTSO-E 定时任务缺少可识别的竞价区。",
                "请指定德国、法国或明确的 ENTSO-E 区域代码。",
            )
        if any(token in text for token in ("负荷", "需求")):
            dataset = "entsoe_actual_total_load"
            label = "ENTSO-E 昨日负荷"
        elif any(token in text for token in ("电价", "价格", "日前价")):
            dataset = "entsoe_day_ahead_prices"
            label = "ENTSO-E 昨日价格"
        else:
            dataset = "entsoe_core"
            label = "ENTSO-E 核心数据增量更新"
    elif any(token in text for token in ("elexon", "英国")):
        if any(token in text for token in ("负荷", "需求")):
            dataset = "elexon_initial_demand_outturn"
            label = "Elexon 需求补采"
        elif any(token in text for token in ("电价", "价格", "系统价")):
            dataset = "elexon_system_prices"
            label = "Elexon 系统价格补采"
        else:
            dataset = "elexon_core"
            label = "Elexon 核心数据增量更新"
    elif any(token in text for token in ("gridstatus", "北美")):
        if any(token in text for token in ("仅目录", "只更新目录", "只刷新目录")):
            dataset = "gridstatus_datasets"
            label = "GridStatus 目录刷新"
        else:
            dataset = "gridstatus_core"
            label = "GridStatus 核心数据增量更新"
    elif any(token in text for token in ("广州电力交易中心", "广州交易中心", "gzpec")):
        dataset = "gzpec_all"
        label = "广州交易中心公开信息更新"
    elif any(token in text for token in ("elecheck", "易能电", "现货")):
        try:
            elecheck_areas = list_elecheck_source_update_areas()
        except (OSError, ValueError, sqlite3.Error):
            elecheck_areas = []
        if not elecheck_areas:
            elecheck_areas = [
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
            ]
        area = next(
            (
                item for item in sorted(elecheck_areas, key=len, reverse=True) if item in message
            ),
            None,
        )
        if area is not None and "现货" in text and not any(
            token in text for token in ("全部", "各类", "所有数据", "来源新数据")
        ):
            dataset = "elecheck_spot"
            label = f"{area} Elecheck 昨日现货"
        else:
            dataset = "elecheck_all"
            label = f"{area or '全部地区'} Elecheck 增量更新"
    if dataset is None or label is None:
        return _collection_failure(
            "没有识别出受支持的数据源和数据集。",
            "请指定 ENTSO-E、Elexon、GridStatus、Elecheck 或广州交易中心。",
        )
    name = name_match.group(1).strip() if name_match else label
    return "market_create_schedule", {
        "name": name,
        "dataset": dataset,
        "schedule_kind": schedule_kind,
        "schedule_time": f"{hour:02d}:{minute:02d}",
        "enabled": enabled,
        "area": area,
    }


def deterministic_collection_route(
    message: str,
) -> tuple[str, dict[str, Any]] | MarketAgentAnswer | None:
    text = message.strip().lower()
    explicit_collection = any(
        token in text
        for token in (
            "采集",
            "补采",
            "补一下",
            "采一下",
            "采一采",
            "采完",
            "全采",
            "抓取",
            "帮我抓",
            "刷新",
        )
    )
    explicit_collection = explicit_collection or (
        "更新" in text and "更新时间" not in text and "更新到哪" not in text
    )
    if not explicit_collection:
        return None

    dates = _message_dates(message)
    if any(token in text for token in ("广州电力交易中心", "广州交易中心", "gzpec")):
        return "market_collect_gzpec", {}
    if "gridstatus" in text or "北美" in text:
        if any(token in text for token in ("目录", "数据集", "catalog")):
            dataset_candidates = [
                token
                for token in re.findall(r"\b[a-z][a-z0-9_]{2,}\b", text)
                if "_" in token and token != "gridstatus"
            ]
            if not dataset_candidates:
                return "market_collect_gridstatus", {"operation": "refresh_catalog"}
        else:
            dataset_candidates = [
                token
                for token in re.findall(r"\b[a-z][a-z0-9_]{2,}\b", text)
                if "_" in token and token != "gridstatus"
            ]
        if dataset_candidates and dates:
            start, end = dates[0], dates[-1]
            if (end - start).days + 1 > 7:
                return _collection_failure(
                    "GridStatus 单次查询范围超过 7 天。",
                    "请缩短日期范围后重试。",
                )
            return "market_collect_gridstatus", {
                "operation": "query",
                "dataset": dataset_candidates[0],
                "location": "PSEG" if "pseg" in text else None,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "limit": 1000,
            }
        return _collection_failure(
            "GridStatus 数据查询缺少精确数据集和日期范围。",
            "请先查看本地数据集目录，再指定数据集以及最多 7 天的日期范围。",
        )

    if any(token in text for token in ("elexon", "英国")):
        if not dates:
            return _collection_failure(
                "Elexon 采集缺少业务日期。",
                "请提供开始和结束日期；单次范围最多 31 天。",
            )
        start, end = dates[0], dates[-1]
        if (end - start).days + 1 > 31:
            return _collection_failure(
                "Elexon 单次采集范围超过 31 天。",
                "请把任务拆分为多个不超过 31 天的批次。",
            )
        dataset = "elexon_system_prices"
        if any(token in text for token in ("负荷", "需求")):
            dataset = "elexon_initial_demand_outturn"
        elif "风电" in text:
            dataset = "elexon_wind_generation_forecast"
        elif any(token in text for token in ("燃料", "发电")):
            dataset = "elexon_generation_by_fuel_half_hourly"
        elif any(token in text for token in ("互联", "潮流")):
            dataset = "elexon_interconnector_flows"
        return "market_collect_elexon", {
            "dataset": dataset,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }

    if any(token in text for token in ("entso-e", "entsoe", "欧洲")):
        if not dates:
            return _collection_failure(
                "ENTSO-E 采集缺少地区和日期范围。",
                "请指定竞价区以及开始、结束日期；单次范围最多 31 天。",
            )
        start, end = dates[0], dates[-1]
        if (end - start).days + 1 > 31:
            return _collection_failure(
                "ENTSO-E 单次采集范围超过 31 天。",
                "请把任务拆分为多个不超过 31 天的批次。",
            )
        area_aliases = {
            "德国": "DE-LU",
            "de-lu": "DE-LU",
            "法国": "FR",
            "西班牙": "ES",
            "意大利": "IT-NORTH",
            "英国": "GB",
        }
        matched_areas = []
        for token, value in area_aliases.items():
            if token in text and value not in matched_areas:
                matched_areas.append(value)
        cross_border = any(token in text for token in ("跨境", "跨区", "潮流", "互联线"))
        if cross_border:
            if len(matched_areas) < 2:
                return _collection_failure(
                    "ENTSO-E 跨境潮流采集缺少两个竞价区。",
                    "请明确流入区和流出区，例如德国到法国；单次范围最多 31 天。",
                )
            return "market_collect_entsoe", {
                "dataset": "entsoe_cross_border_physical_flows",
                "in_area": matched_areas[0],
                "out_area": matched_areas[1],
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            }
        area = matched_areas[0] if matched_areas else None
        if area is None:
            return _collection_failure(
                "ENTSO-E 采集缺少可识别的竞价区。",
                "请补充地区，例如德国、法国或明确的 ENTSO-E 区域代码。",
            )
        dataset = "entsoe_day_ahead_prices"
        if "负荷" in text:
            dataset = "entsoe_actual_total_load"
        elif "发电" in text:
            dataset = "entsoe_actual_generation_by_type"
        return "market_collect_entsoe", {
            "dataset": dataset,
            "area": area,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }

    if any(token in text for token in ("elecheck", "易能电", "现货", "代理购电", "机制电价")):
        if any(token in text for token in ("机制电价", "增量机制")):
            return "market_collect_elecheck", {"category": "mechanism"}
        if "代理购电" in text:
            months = []
            for raw in re.findall(r"20\d{2}-(?:0[1-9]|1[0-2])", message):
                if raw not in months:
                    months.append(raw)
            if not months:
                return _collection_failure(
                    "代理购电采集缺少月份范围。",
                    "请提供 YYYY-MM 格式的开始和结束月份。",
                )
            return "market_collect_elecheck", {
                "category": "purchasing",
                "start_month": months[0],
                "end_month": months[-1],
            }
        try:
            areas = list_elecheck_source_update_areas()
        except (OSError, ValueError, sqlite3.Error):
            areas = []
        if not areas:
            areas = ["江苏", "山西", "山东", "广东", "浙江", "安徽", "福建", "甘肃", "蒙西"]
        area = next(
            (item for item in sorted(areas, key=len, reverse=True) if item in message),
            None,
        )
        if area is None or not dates:
            return _collection_failure(
                "Elecheck 现货采集缺少地区或日期范围。",
                "请指定一个地区和开始、结束日期；单次范围最多 31 天。",
            )
        start, end = dates[0], dates[-1]
        if (end - start).days + 1 > 31:
            return _collection_failure(
                "Elecheck 现货单次采集范围超过 31 天。",
                "请把任务拆分为多个不超过 31 天的批次。",
            )
        return "market_collect_elecheck", {
            "category": "spot",
            "area": area,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
    return None


def candidate_tool_names(message: str, registry: ToolRegistry) -> list[str]:
    text = message.strip().lower()
    if any(token in text for token in ("api key", "apikey", "凭据", "密钥", "401", "403")):
        return ["market_get_credential_setup_guide"]
    if _has_schedule_intent(text):
        if any(token in text for token in ("列出", "查看", "哪些", "状态")):
            return ["market_list_schedules"]
        return ["market_create_schedule"]
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
    if any(token in text for token in ("导出", "csv", "png")):
        return ["market_export_result"]
    source_mentions = sum(
        any(alias in text for alias in aliases)
        for aliases in (
            ("elecheck", "易能电", "江苏", "山西"),
            ("entso-e", "entsoe", "欧洲", "德国"),
            ("elexon", "英国"),
            ("gridstatus", "北美"),
            ("广州电力交易中心", "广州交易中心", "gzpec"),
        )
    )
    mentions_external_source = any(
        token in text
        for token in (
            "entso-e",
            "entsoe",
            "欧洲",
            "德国",
            "elexon",
            "英国",
            "gridstatus",
            "北美",
            "广州电力交易中心",
            "广州交易中心",
            "gzpec",
        )
    )
    if source_mentions <= 1 and not mentions_external_source and any(
        token in text for token in ("日前价", "实时价", "现货", "价差", "实时比日前")
    ):
        return ["market_analyze_elecheck_spot"]
    if any(token in text for token in ("比较", "对比", "并列", "差额", "排名")):
        return ["market_compare_series"]
    if any(
        token in text
        for token in (
            "广州电力交易中心",
            "广州交易中心",
            "gzpec",
            "新闻",
            "消息",
            "公开信息",
            "绿证",
        )
    ):
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
    preferred = [
        "market_query_series",
        "market_compare_series",
        "market_get_data_overview",
        "market_list_datasets",
    ]
    return [name for name in preferred if name in registry.names()]


def user_failure_explanation(error: AgentError) -> tuple[str, str]:
    """Turn internal failures into a concise, actionable user-facing explanation."""
    return explain_agent_failure(
        code=error.code,
        message=error.message,
        retryable=error.retryable,
        scope_label="对应数据源",
    )


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
            boundary_answer = deterministic_boundary_answer(message)
            if boundary_answer is not None:
                self._event(
                    run_id,
                    session_id,
                    "request_declined",
                    {"reason": "unsupported_or_unsafe_capability"},
                )
                return self._complete(
                    run_id,
                    session_id,
                    boundary_answer,
                    model_calls=0,
                    usage={},
                )
            software_help = deterministic_software_help_answer(message)
            if software_help is not None:
                self._event(
                    run_id,
                    session_id,
                    "software_guidance",
                    {},
                )
                return self._complete(
                    run_id,
                    session_id,
                    software_help,
                    model_calls=0,
                    usage={},
                )
            schedule_route = deterministic_schedule_route(message)
            if isinstance(schedule_route, MarketAgentAnswer):
                self._event(
                    run_id,
                    session_id,
                    "schedule_details_required",
                    {"reason": schedule_route.conclusion},
                )
                return self._complete(
                    run_id,
                    session_id,
                    schedule_route,
                    model_calls=0,
                    usage={},
                )
            if schedule_route is not None:
                tool_name, arguments = schedule_route
                return self._propose_direct_approval(
                    run_id,
                    session_id,
                    tool_name,
                    arguments,
                )
            contextual_message = contextualize_continuation(
                message,
                self.repository.model_messages(session_id),
            )
            collection_route = deterministic_collection_route(contextual_message)
            if isinstance(collection_route, MarketAgentAnswer):
                self._event(
                    run_id,
                    session_id,
                    "collection_details_required",
                    {"reason": collection_route.conclusion},
                )
                return self._complete(
                    run_id,
                    session_id,
                    collection_route,
                    model_calls=0,
                    usage={},
                )
            if collection_route is not None:
                tool_name, arguments = collection_route
                return self._propose_direct_approval(
                    run_id,
                    session_id,
                    tool_name,
                    arguments,
                )
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

    def _propose_direct_approval(
        self,
        run_id: str,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> AgentRunResult:
        definition, parsed = self.registry.validate(tool_name, arguments)
        if definition.risk_level != RiskLevel.APPROVAL:
            raise ValueError("确定性采集路由只能提出需要审批的工具。")
        normalized_arguments = parsed.model_dump(mode="json")
        call = ProviderToolCall(
            id=f"direct-{uuid4()}",
            name=tool_name,
            arguments=normalized_arguments,
        )
        record = self.repository.create_tool_call(
            call_id=call.id,
            run_id=run_id,
            session_id=session_id,
            tool_name=tool_name,
            risk_level=definition.risk_level,
            arguments=normalized_arguments,
        )
        call = call.model_copy(update={"id": record["id"]})
        self._active_tool_names = [tool_name]
        messages = self._initial_messages(session_id)
        messages.append(self._assistant_tool_message(ProviderResponse(), [call]))
        preview = (
            collection_preview(tool_name, normalized_arguments)
            if tool_name.startswith("market_collect_")
            else {
                "estimated_requests": 0,
                "start": "保存本地任务定义",
                "end": "不立即运行",
            }
        )
        side_effect = (
            f"{definition.side_effect} "
            f"预计请求：{preview['estimated_requests']}；"
            f"范围：{preview.get('start') or '当前快照'}"
            f" 至 {preview.get('end') or '当前快照'}；"
            f"运行凭据：{preview.get('credential_status') or '不适用'}。"
        )
        pending_context = {
            "protocol": self.protocol.value,
            "active_tool_names": self._active_tool_names,
            "messages": messages,
            "tool_payloads": [],
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
            "intent_routed",
            {"tool_name": tool_name, "arguments": normalized_arguments},
        )
        self._event(
            run_id,
            session_id,
            "tool_proposed",
            {"tool_name": tool_name, "arguments": normalized_arguments},
        )
        self._event(
            run_id,
            session_id,
            "approval_required",
            {
                "tool_name": tool_name,
                "arguments": normalized_arguments,
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
                tool_name=tool_name,
                risk_level=definition.risk_level,
                arguments=normalized_arguments,
                arguments_hash=record["arguments_hash"],
                side_effect=side_effect,
            ),
            events=self.repository.list_events(run_id),
            usage={},
        )

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
                conclusion, guidance = user_failure_explanation(error)
                answer = MarketAgentAnswer(
                    conclusion=conclusion,
                    warnings=[guidance],
                )
                answer_payload = answer.model_dump(mode="json")
                self.repository.update_run(
                    run_id,
                    status=RunStatus.REJECTED.value,
                    answer=answer_payload,
                    error=error.model_dump(mode="json"),
                    clear_pending_context=True,
                )
                self.repository.add_message(
                    session_id,
                    role="assistant",
                    content=answer_payload,
                    run_id=run_id,
                )
                self._event(run_id, session_id, "approval_rejected", {})
                return AgentRunResult(
                    ok=False,
                    status=RunStatus.REJECTED,
                    session_id=session_id,
                    run_id=run_id,
                    answer=answer,
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
            if self._payload_is_program_renderable(payload):
                answer = self._ground_answer(None, tool_payloads)
                return self._complete(
                    run_id,
                    session_id,
                    answer,
                    model_calls=int(run["model_calls"]),
                    usage=dict(run["usage"]),
                )
            self._active_tool_names = []
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
                approval_calls = []
                for call in action:
                    try:
                        is_approval = (
                            self.registry.get(call.name).risk_level
                            == RiskLevel.APPROVAL
                        )
                    except ValueError:
                        is_approval = False
                    if is_approval:
                        approval_calls.append(call)
                if len(action) > 1 and approval_calls:
                    raise ProviderProtocolError("需要审批的操作必须单独调用。")
                prepared_calls = []
                invalid_calls: list[tuple[ProviderToolCall, str]] = []
                assistant_calls: list[ProviderToolCall] = []
                for call in action:
                    try:
                        definition, parsed = self.registry.validate(
                            call.name,
                            call.arguments,
                        )
                        normalized_arguments = parsed.model_dump(mode="json")
                        record = self.repository.create_tool_call(
                            call_id=call.id,
                            run_id=run_id,
                            session_id=session_id,
                            tool_name=call.name,
                            risk_level=definition.risk_level,
                            arguments=normalized_arguments,
                        )
                    except ValueError as exc:
                        message = redact_text(str(exc))
                        self._event(
                            run_id,
                            session_id,
                            "tool_validation_failed",
                            {"tool_name": call.name, "message": message},
                        )
                        invalid_calls.append((call, message))
                        assistant_calls.append(call)
                        continue
                    call = call.model_copy(update={"arguments": normalized_arguments})
                    call = call.model_copy(update={"id": record["id"]})
                    prepared_calls.append((call, definition, record))
                    assistant_calls.append(call)
                messages.append(self._assistant_tool_message(response, assistant_calls))
                for call, message in invalid_calls:
                    messages.append(
                        self._tool_result_message(
                            call.id,
                            call.name,
                            {
                                "ok": False,
                                "error": {
                                    "code": "invalid_tool_call",
                                    "message": message,
                                },
                            },
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
                    reused_success = record["status"] == "success"
                    try:
                        payload = self._execute_tool_call(
                            run_id,
                            session_id,
                            call,
                            existing_call=True,
                        )
                    except ValueError as exc:
                        messages.append(
                            self._tool_result_message(
                                call.id,
                                call.name,
                                {
                                    "ok": False,
                                    "error": {
                                        "code": "tool_execution_failed",
                                        "message": redact_text(str(exc)),
                                    },
                                },
                            )
                        )
                        continue
                    tool_payloads.append((call.name, payload))
                    messages.append(self._tool_result_message(call.id, call.name, payload))
                    if reused_success and self._payload_is_program_renderable(payload):
                        answer = self._ground_answer(None, tool_payloads)
                        answer.warnings.append(
                            "模型重复了相同查询，系统已复用结果并直接生成结论。"
                        )
                        return self._complete(
                            run_id,
                            session_id,
                            answer,
                            model_calls=model_calls,
                            usage=usage,
                        )
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
        assistant_conclusions: list[str] = []
        series_units: list[str] = []
        empty_series_count = 0
        for tool_name, payload in tool_payloads:
            if payload.get("assistant_conclusion"):
                assistant_conclusions.append(str(payload["assistant_conclusion"]))
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
            raw_series = payload.get("series")
            series_items = (
                [raw_series]
                if isinstance(raw_series, dict)
                else raw_series
                if isinstance(raw_series, list)
                else []
            )
            for series in series_items:
                if not isinstance(series, dict):
                    continue
                if series.get("unit"):
                    series_units.append(str(series["unit"]))
                if "points" in series and not series.get("points"):
                    empty_series_count += 1
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
            else " ".join(assistant_conclusions)
            if assistant_conclusions
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
        if not facts and empty_series_count:
            conclusion = "查询完成，但在所选地区和日期范围内没有找到本地数据；未补零，也未编造数值。"
            warnings.append("可以先采集对应日期的数据，或调整日期范围后重试。")
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
            units=self._unique(
                [fact.unit for fact in facts if fact.unit] + series_units
            ),
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
        if payload.get("assistant_conclusion"):
            return True
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
            dataset_keys = list(dict.fromkeys((fact.source, fact.dataset) for fact in facts))
            selected_facts = facts[:3]
            if len(dataset_keys) > 1:
                selected_facts = []
                for source, dataset in dataset_keys[:4]:
                    average = next(
                        (
                            fact
                            for fact in facts
                            if fact.source == source
                            and fact.dataset == dataset
                            and fact.label == "结果均值"
                        ),
                        None,
                    )
                    if average is not None:
                        selected_facts.append(average)
            snippets = []
            for fact in selected_facts:
                unit = f" {fact.unit}" if fact.unit else ""
                value = (
                    f"{fact.value:.2f}"
                    if isinstance(fact.value, float)
                    else str(fact.value)
                )
                label = fact.label
                if len(dataset_keys) > 1:
                    label = f"{fact.source} / {fact.dataset} {label}"
                snippets.append(f"{label}为 {value}{unit}")
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
        conclusion, guidance = user_failure_explanation(error)
        completed_actions, change_note = completed_change_explanation(
            self.repository.list_tool_calls(run_id)
        )
        answer = MarketAgentAnswer(
            conclusion=conclusion,
            warnings=[change_note, guidance],
            executed_actions=completed_actions,
        )
        answer_payload = answer.model_dump(mode="json")
        try:
            run = self.repository.get_run(run_id)
            if run["status"] == RunStatus.AWAITING_APPROVAL.value:
                status = RunStatus.AWAITING_APPROVAL
            else:
                status = RunStatus.FAILED
                self.repository.update_run(
                    run_id,
                    status=status.value,
                    answer=answer_payload,
                    error=error.model_dump(mode="json"),
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
            answer=answer,
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
        answer = MarketAgentAnswer(
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
        return AgentRunResult(
            ok=False,
            status=RunStatus.STOPPED,
            session_id=session_id,
            run_id=run_id,
            answer=answer,
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
