from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from powertrade_crawler.market_agent.data_tools import (
    CollectElecheckArgs,
    CollectEntsoeArgs,
    CollectGridStatusArgs,
    QuerySeriesArgs,
    build_market_tool_registry,
)
from powertrade_crawler.market_agent.loop import (
    MarketAgentLoop,
    candidate_tool_names,
    deterministic_route,
)
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
        "market_get_credential_setup_guide",
        "market_list_schedules",
        "market_list_datasets",
        "market_query_series",
        "market_compare_series",
        "market_analyze_elecheck_spot",
        "market_analyze_elecheck_purchasing",
        "market_analyze_elecheck_mechanism",
        "market_search_knowledge",
        "market_search_gzpec_news",
        "market_get_gzpec_article",
        "market_export_result",
    }
    expected_writes = {
        "market_create_schedule",
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
        "五类采集和定时任务创建均需要普通审批",
    )
    check(
        "reads_are_automatic",
        all(registry.get(name).risk_level == RiskLevel.AUTO for name in expected_read),
        "查询、分析和预设目录导出自动执行",
    )
    forbidden = {"sql", "shell", "delete", "set_credential", "windows_task"}
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
        len(live_cases) >= 30
        and all(
            (
                not any(
                    str(name).startswith("market_collect_")
                    for name in case.get("allowed_tools") or []
                )
                or case.get("expected_status") == "awaiting_approval"
            )
            for case in live_cases
        ),
        "在线案例不少于30项，采集案例必须停在审批边界",
    )
    knowledge_cases = [
        case for case in live_cases if str(case.get("id", "")).startswith("rag_")
    ]
    routed_knowledge_cases = sum(
        candidate_tool_names(case["prompt"], registry)
        == ["market_search_knowledge"]
        for case in knowledge_cases
    )
    check(
        "rag_agent_cases_present",
        len(knowledge_cases) >= 20,
        "至少20条知识问答真实口语评测",
    )
    check(
        "rag_agent_static_tool_selection",
        bool(knowledge_cases)
        and routed_knowledge_cases / len(knowledge_cases) >= 0.90,
        "知识问答确定性候选工具选择率不低于90%",
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
    start: int = 1,
    limit: int | None = None,
    execute_collections: bool = False,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    repository = repository or MarketAgentRepository()
    results = []
    all_cases = load_live_cases()
    if start < 1:
        raise ValueError("start must be at least 1.")
    remaining_cases = all_cases[start - 1 :]
    selected_cases = remaining_cases[:limit] if limit is not None else remaining_cases
    started = time.perf_counter()
    for index, case in enumerate(selected_cases, start=1):
        turns = [str(item) for item in case.get("turns") or [case["prompt"]]]
        display_prompt = " → ".join(turns)
        if progress_callback:
            progress_callback(index, len(selected_cases), display_prompt)
        case_started = time.perf_counter()
        session_id = None
        run_ids: list[str] = []
        proposal = None
        loop = None
        for turn in turns:
            loop = MarketAgentLoop.from_config(repository=repository)
            proposal = loop.chat(turn, session_id=session_id)
            session_id = proposal.session_id
            run_ids.append(proposal.run_id)
            if proposal.pending_approval is not None:
                break
        if proposal is None or loop is None:
            raise AssertionError("A live evaluation case must contain at least one turn.")
        proposal_status = proposal.status.value
        result = proposal
        collection_executed = False
        if (
            execute_collections
            and case.get("execute_collection")
            and proposal.pending_approval is not None
        ):
            collection_executed = True
            result = MarketAgentLoop.from_config(repository=repository).resume_approval(
                proposal.pending_approval.tool_call_id,
                approved=True,
                expected_arguments_hash=proposal.pending_approval.arguments_hash,
            )
        tool_calls = [
            call
            for run_id in run_ids
            for call in repository.list_tool_calls(run_id)
        ]
        tool_names = [call["tool_name"] for call in tool_calls]
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
        window_match = True
        if case.get("expected_window_days") is not None:
            try:
                window_start = date.fromisoformat(str(tool_calls[0]["arguments"]["start_date"]))
                window_end = date.fromisoformat(str(tool_calls[0]["arguments"]["end_date"]))
            except (IndexError, KeyError, TypeError, ValueError):
                window_match = False
            else:
                window_match = (
                    (window_end - window_start).days + 1
                    == int(case["expected_window_days"])
                )
                if case.get("expected_end_date") == "today":
                    window_match = window_match and window_end == date.today()
        expected_units = set(case.get("expected_units") or [])
        actual_units = set(result.answer.units if result.answer else [])
        actual_dataset_ids = {
            item.dataset for item in (result.answer.datasets if result.answer else [])
        }
        expected_dataset_ids = set(case.get("expected_dataset_ids") or [])
        conclusion = result.answer.conclusion if result.answer else ""
        warning_text = " ".join(result.answer.warnings if result.answer else [])
        expected_status = str(case.get("expected_status") or "completed")
        explanation_text = f"{conclusion} {warning_text}".strip()
        explanation_ok = not case.get("requires_explanation") or (
            len(explanation_text) >= 12
            and any(
                token in explanation_text
                for token in ("无法", "不能", "不支持", "未", "需要", "失败", "拒绝")
            )
        )
        result_content_ok = not case.get("requires_result_content") or bool(
            result.answer
            and (
                result.answer.business_metrics
                or result.answer.reference_items
                or result.answer.datasets
                or result.answer.dataset_catalog_summaries
                or result.answer.knowledge_citations
            )
        )
        knowledge_citation_ok = not case.get("requires_knowledge_citations") or bool(
            result.answer
            and result.answer.knowledge_citations
            and result.answer.citation_ids
            and set(result.answer.citation_ids)
            == {item.citation_id for item in result.answer.knowledge_citations}
        )
        tool_call_success = all(call["status"] == "success" for call in tool_calls)
        execution_ok = (
            not (execute_collections and case.get("execute_collection"))
            or (collection_executed and tool_call_success and result.ok)
        )
        passed = (
            proposal_status == expected_status
            and (result.ok or expected_status == "awaiting_approval")
            and set(tool_names) <= allowed_tools
            and (expected_sequence is None or tool_names == expected_sequence)
            and arguments_match
            and window_match
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
            and result_content_ok
            and knowledge_citation_ok
            and explanation_ok
            and execution_ok
            and (
                not case.get("expected_warning_contains")
                or str(case["expected_warning_contains"]) in warning_text
            )
            and sum(repository.get_run(run_id)["model_calls"] for run_id in run_ids)
            <= int(case.get("max_model_calls", 2))
        )
        results.append(
            {
                "id": case["id"],
                "turns": turns,
                "passed": passed,
                "status": result.status.value,
                "proposal_status": proposal_status,
                "tool_names": tool_names,
                "tool_arguments": [
                    call["arguments"]
                    for call in tool_calls
                ],
                "window_match": window_match,
                "data_sources": sorted(sources),
                "units": sorted(actual_units),
                "dataset_ids": sorted(actual_dataset_ids),
                "citation_ids": (
                    result.answer.citation_ids if result.answer else []
                ),
                "retrieval_mode": (
                    result.answer.retrieval_mode if result.answer else "none"
                ),
                "conclusion": conclusion,
                "error": result.error.model_dump(mode="json") if result.error else None,
                "collection_executed": collection_executed,
                "tool_call_statuses": [call["status"] for call in tool_calls],
                "latency_seconds": round(time.perf_counter() - case_started, 3),
            }
        )
    passed = sum(1 for case in results if case["passed"])
    paired_results = list(zip(results, selected_cases, strict=True))
    knowledge_results = [
        item
        for item, case in paired_results
        if str(case.get("id", "")).startswith("rag_")
    ]
    citation_required_results = [
        item
        for item, case in paired_results
        if case.get("requires_knowledge_citations")
    ]
    knowledge_tool_selections = sum(
        item["tool_names"] == ["market_search_knowledge"]
        for item in knowledge_results
    )
    knowledge_citation_successes = sum(
        bool(item["citation_ids"]) for item in citation_required_results
    )
    return {
        "mode": "online",
        "cases": results,
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "collection_cases_executed": sum(
                bool(case["collection_executed"]) for case in results
            ),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "knowledge_case_count": len(knowledge_results),
            "knowledge_tool_selection_rate": (
                round(knowledge_tool_selections / len(knowledge_results), 4)
                if knowledge_results
                else 1.0
            ),
            "knowledge_citation_success_rate": (
                round(
                    knowledge_citation_successes / len(citation_required_results),
                    4,
                )
                if citation_required_results
                else 1.0
            ),
        },
    }
