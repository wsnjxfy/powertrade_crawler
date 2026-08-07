from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from powertrade_crawler.market_agent.data_tools import (
    CollectElecheckArgs,
    CollectEntsoeArgs,
    CollectGridStatusArgs,
    QuerySeriesArgs,
    build_market_tool_registry,
)
from powertrade_crawler.market_agent.loop import MarketAgentLoop, deterministic_route
from powertrade_crawler.market_agent.repository import MarketAgentRepository
from powertrade_crawler.market_agent.schemas import RiskLevel


def live_case_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "live_eval_cases.json"


def load_live_cases() -> list[dict[str, Any]]:
    with live_case_path().open(encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, list) or len(payload) < 15:
        raise ValueError("多数据源 Agent 在线评测至少需要15个案例。")
    return payload


def run_offline_evaluation() -> dict[str, Any]:
    registry = build_market_tool_registry()
    cases: list[dict[str, Any]] = []

    def check(case_id: str, condition: bool, detail: str) -> None:
        cases.append(
            {
                "id": case_id,
                "passed": bool(condition),
                "detail": detail,
            }
        )

    expected_read = {
        "market_get_data_overview",
        "market_list_datasets",
        "market_query_series",
        "market_compare_series",
        "market_analyze_elecheck_spot",
        "market_analyze_elecheck_purchasing",
        "market_analyze_elecheck_mechanism",
        "market_search_gzpec_news",
        "market_get_gzpec_article",
        "market_export_result",
    }
    expected_writes = {
        "market_collect_elecheck",
        "market_collect_entsoe",
        "market_collect_elexon",
        "market_collect_gridstatus",
        "market_collect_gzpec",
    }
    check("read_tools_present", expected_read <= set(registry.names()), "只读工具齐全")
    check("write_tools_present", expected_writes <= set(registry.names()), "采集工具齐全")
    check(
        "all_writes_require_approval",
        all(registry.get(name).risk_level == RiskLevel.APPROVAL for name in expected_writes),
        "五类采集均需要普通审批",
    )
    check(
        "reads_are_automatic",
        all(registry.get(name).risk_level == RiskLevel.AUTO for name in expected_read),
        "查询、分析和预设目录导出自动执行",
    )
    forbidden = {"sql", "shell", "delete", "credential", "schedule", "windows_task"}
    check(
        "forbidden_capabilities_absent",
        not any(
            token in name
            for name in registry.names()
            for token in forbidden
        ),
        "未注册高风险通用能力",
    )
    check(
        "overview_route",
        deterministic_route("查看五个来源的数据更新时间")
        == ("market_get_data_overview", {"source": None}),
        "更新时间问题确定性路由",
    )
    check(
        "catalog_route",
        deterministic_route("ENTSO-E 有哪些数据集？")
        == ("market_list_datasets", {"source": "entsoe"}),
        "数据目录问题确定性路由",
    )
    try:
        QuerySeriesArgs(
            source="entsoe",
            dataset="elexon_system_prices",
        )
        valid_source_binding = False
    except ValueError:
        valid_source_binding = True
    check("dataset_source_binding", valid_source_binding, "拒绝跨来源伪造数据集")
    try:
        QuerySeriesArgs(
            source="elecheck",
            dataset="elecheck_spot",
            start_date="2024-01-01",
            end_date="2025-12-31",
        )
        bounded_read = False
    except ValueError:
        bounded_read = True
    check("read_window_bounded", bounded_read, "只读日期范围最长366天")
    try:
        CollectElecheckArgs(
            category="spot",
            area="山西",
            start_date="2026-01-01",
            end_date="2026-02-15",
        )
        bounded_spot = False
    except ValueError:
        bounded_spot = True
    check("elecheck_spot_bounded", bounded_spot, "Elecheck 现货采集最长31天")
    try:
        CollectEntsoeArgs(
            dataset="entsoe_cross_border_physical_flows",
            start_date="2026-01-01",
            end_date="2026-01-02",
        )
        border_required = False
    except ValueError:
        border_required = True
    check("entsoe_border_required", border_required, "跨境潮流要求双边区域")
    try:
        CollectGridStatusArgs(
            operation="query",
            dataset="ercot_load",
            start_date="2026-01-01",
            end_date="2026-01-10",
        )
        gridstatus_bounded = False
    except ValueError:
        gridstatus_bounded = True
    check("gridstatus_window_bounded", gridstatus_bounded, "GridStatus 最长七天")
    live_cases = load_live_cases()
    check(
        "live_cases_present",
        len(live_cases) >= 15
        and all(
            not any(
                str(name).startswith("market_collect_")
                for name in case.get("allowed_tools") or []
            )
            for case in live_cases
        ),
        "在线案例不少于15项且全部只读",
    )
    check(
        "independent_namespace",
        all(not name.startswith("elecheck_") for name in registry.names()),
        "工具使用独立 market_ 命名空间",
    )
    check(
        "no_strong_approval",
        all(tool.risk_level != "strong_approval" for tool in registry.list()),
        "首版未开放强化审批操作",
    )
    passed = sum(1 for case in cases if case["passed"])
    return {
        "mode": "offline",
        "cases": cases,
        "summary": {
            "total": len(cases),
            "passed": passed,
            "failed": len(cases) - passed,
        },
    }


def run_online_evaluation(
    *,
    repository: MarketAgentRepository | None = None,
) -> dict[str, Any]:
    repository = repository or MarketAgentRepository()
    results = []
    for case in load_live_cases():
        loop = MarketAgentLoop.from_config(repository=repository)
        result = loop.chat(case["prompt"])
        tool_names = [
            call["tool_name"]
            for call in repository.list_tool_calls(result.run_id)
        ]
        tool_calls = repository.list_tool_calls(result.run_id)
        allowed_tools = set(case.get("allowed_tools") or [])
        sources = set(result.answer.data_sources if result.answer else [])
        expected_sources = set(case.get("expected_sources") or [])
        expected_sequence = case.get("expected_tool_sequence")
        expected_arguments = case.get("expected_arguments")
        arguments_match = True
        if expected_arguments is not None:
            arguments_match = bool(tool_calls) and all(
                tool_calls[0]["arguments"].get(key) == value
                for key, value in expected_arguments.items()
            )
        expected_units = set(case.get("expected_units") or [])
        actual_units = set(result.answer.units if result.answer else [])
        actual_dataset_ids = {
            item.dataset for item in (result.answer.datasets if result.answer else [])
        }
        expected_dataset_ids = set(case.get("expected_dataset_ids") or [])
        conclusion = result.answer.conclusion if result.answer else ""
        warning_text = " ".join(result.answer.warnings if result.answer else [])
        passed = (
            result.ok
            and set(tool_names) <= allowed_tools
            and (expected_sequence is None or tool_names == expected_sequence)
            and arguments_match
            and expected_sources <= sources
            and expected_units <= actual_units
            and expected_dataset_ids <= actual_dataset_ids
            and (
                not case.get("expected_conclusion_contains")
                or all(
                    str(text) in conclusion
                    for text in case["expected_conclusion_contains"]
                )
            )
            and "未返回可量化业务事实" not in conclusion
            and (
                not case.get("requires_business_metrics")
                or bool(result.answer and result.answer.business_metrics)
            )
            and (
                not case.get("expected_warning_contains")
                or str(case["expected_warning_contains"]) in warning_text
            )
            and repository.get_run(result.run_id)["model_calls"]
            <= int(case.get("max_model_calls", 2))
        )
        results.append(
            {
                "id": case["id"],
                "passed": passed,
                "status": result.status.value,
                "tool_names": tool_names,
                "tool_arguments": [
                    call["arguments"]
                    for call in tool_calls
                ],
                "data_sources": sorted(sources),
                "units": sorted(actual_units),
                "dataset_ids": sorted(actual_dataset_ids),
                "conclusion": conclusion,
                "error": result.error.model_dump(mode="json") if result.error else None,
            }
        )
    passed = sum(1 for case in results if case["passed"])
    return {
        "mode": "online",
        "cases": results,
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
        },
    }
