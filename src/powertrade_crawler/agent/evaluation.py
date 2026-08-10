from __future__ import annotations

import gc
import json
import os
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from powertrade_crawler.agent.loop import AgentLoop
from powertrade_crawler.agent.provider import FakeProvider
from powertrade_crawler.agent.repository import AgentRepository
from powertrade_crawler.agent.schemas import (
    AgentAnswer,
    AgentProtocol,
    ProviderResponse,
    ProviderToolCall,
    RiskLevel,
)
from powertrade_crawler.agent.security import redact_value
from powertrade_crawler.agent.tools import ToolContext, ToolDefinition, ToolRegistry
from powertrade_crawler.config import get_settings


def load_live_cases() -> list[dict[str, Any]]:
    resource = files("powertrade_crawler.agent").joinpath(
        "data/live_eval_cases.json"
    )
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) < 24:
        raise ValueError("Live evaluation fixture must contain at least 24 cases.")
    return payload


def answer_payload(conclusion: str = "离线评测完成。") -> dict[str, Any]:
    return AgentAnswer(
        conclusion=conclusion,
        data_range=[],
        business_metrics=[],
        units=[],
        completeness=[],
        warnings=[],
        generated_files=[],
        executed_actions=[],
        data_sources=["Elecheck 易能电易查"],
    ).model_dump(mode="json")


class _NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ValueArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


def _offline_registry(counter: dict[str, int]) -> ToolRegistry:
    registry = ToolRegistry()

    def read_handler(args: _ValueArgs, _context: ToolContext) -> dict[str, Any]:
        counter["read"] = counter.get("read", 0) + 1
        return {"value": args.value}

    def write_handler(_args: _NoArgs, _context: ToolContext) -> dict[str, Any]:
        counter["write"] = counter.get("write", 0) + 1
        return {"written": True}

    registry.register(
        ToolDefinition(
            name="test_read",
            description="离线只读测试工具。",
            args_model=_ValueArgs,
            handler=read_handler,
        )
    )
    registry.register(
        ToolDefinition(
            name="test_write",
            description="离线审批测试工具。",
            args_model=_NoArgs,
            handler=write_handler,
            risk_level=RiskLevel.APPROVAL,
            side_effect="只修改独立测试计数器。",
        )
    )
    return registry


@contextmanager
def isolated_database():
    previous = os.environ.get("DATABASE_URL")
    temporary = tempfile.TemporaryDirectory(
        prefix="powertrade-agent-eval-",
        ignore_cleanup_errors=True,
    )
    try:
        directory = temporary.name
        db_path = Path(directory) / "agent-eval.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
        get_settings.cache_clear()
        try:
            yield db_path
        finally:
            if previous is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous
            get_settings.cache_clear()
    finally:
        gc.collect()
        temporary.cleanup()


def _case_result(name: str, passed: bool, **details: Any) -> dict[str, Any]:
    return {
        "name": name,
        "passed": passed,
        "details": redact_value(details),
    }


def run_offline_evaluation(limit: int | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    cases: list[dict[str, Any]] = []
    with isolated_database():
        repository = AgentRepository()

        native = AgentLoop(
            FakeProvider(
                [ProviderResponse(content=json.dumps(answer_payload()), raw_protocol="native")]
            ),
            repository=repository,
            registry=_offline_registry({}),
            protocol=AgentProtocol.NATIVE,
            model_id="fake/native",
        ).chat("返回结构化答案")
        cases.append(
            _case_result(
                "native_structured_answer",
                native.ok and native.answer is not None,
                status=native.status.value,
            )
        )

        json_result = AgentLoop(
            FakeProvider(
                [
                    ProviderResponse(
                        content=json.dumps(
                            {"type": "final", "answer": answer_payload()},
                            ensure_ascii=False,
                        ),
                        raw_protocol="json",
                    )
                ]
            ),
            repository=repository,
            registry=_offline_registry({}),
            protocol=AgentProtocol.JSON,
            model_id="fake/json",
        ).chat("JSON 降级")
        cases.append(
            _case_result(
                "json_fallback",
                json_result.ok and json_result.answer is not None,
                status=json_result.status.value,
            )
        )

        unknown = AgentLoop(
            FakeProvider(
                [
                    ProviderResponse(
                        tool_calls=[
                            ProviderToolCall(
                                id="unknown",
                                name="arbitrary_shell",
                                arguments={},
                            )
                        ]
                    ),
                    ProviderResponse(content=json.dumps(answer_payload("未知工具已拒绝。"))),
                ]
            ),
            repository=repository,
            registry=_offline_registry({}),
            protocol=AgentProtocol.NATIVE,
            model_id="fake/native",
        ).chat("尝试未知工具")
        cases.append(
            _case_result(
                "unknown_tool_rejected",
                unknown.ok
                and any(
                    event["stage"] == "tool_validation_failed"
                    for event in unknown.events
                ),
            )
        )

        invalid = AgentLoop(
            FakeProvider(
                [
                    ProviderResponse(
                        tool_calls=[
                            ProviderToolCall(
                                id="invalid",
                                name="test_read",
                                arguments={"value": 1, "extra": "blocked"},
                            )
                        ]
                    ),
                    ProviderResponse(content=json.dumps(answer_payload("额外参数已拒绝。"))),
                ]
            ),
            repository=repository,
            registry=_offline_registry({}),
            protocol=AgentProtocol.NATIVE,
            model_id="fake/native",
        ).chat("参数校验")
        cases.append(
            _case_result(
                "extra_argument_rejected",
                invalid.ok
                and any(
                    event["stage"] == "tool_validation_failed"
                    for event in invalid.events
                ),
            )
        )

        approval_counter: dict[str, int] = {}
        approval_provider = FakeProvider(
            [
                ProviderResponse(
                    tool_calls=[
                        ProviderToolCall(
                            id="write-once",
                            name="test_write",
                            arguments={},
                        )
                    ]
                ),
                ProviderResponse(content=json.dumps(answer_payload("写操作已审批并执行。"))),
            ]
        )
        approval_loop = AgentLoop(
            approval_provider,
            repository=repository,
            registry=_offline_registry(approval_counter),
            protocol=AgentProtocol.NATIVE,
            model_id="fake/native",
        )
        pending = approval_loop.chat("执行写操作")
        pending_ok = (
            pending.status.value == "awaiting_approval"
            and approval_counter.get("write", 0) == 0
            and pending.pending_approval is not None
        )
        if pending.pending_approval:
            repository.decide_approval(
                pending.pending_approval.tool_call_id,
                approved=True,
                expected_arguments_hash=pending.pending_approval.arguments_hash,
            )
            resumed = approval_loop.resume(pending.pending_approval.tool_call_id)
        else:
            resumed = None
        cases.append(
            _case_result(
                "approval_pause_resume",
                pending_ok
                and resumed is not None
                and resumed.ok
                and approval_counter.get("write", 0) == 1,
            )
        )

        duplicate_counter: dict[str, int] = {}
        duplicate = AgentLoop(
            FakeProvider(
                [
                    ProviderResponse(
                        tool_calls=[
                            ProviderToolCall(
                                id="same-call",
                                name="test_read",
                                arguments={"value": 7},
                            )
                        ]
                    ),
                    ProviderResponse(
                        tool_calls=[
                            ProviderToolCall(
                                id="same-call",
                                name="test_read",
                                arguments={"value": 7},
                            )
                        ]
                    ),
                    ProviderResponse(content=json.dumps(answer_payload("只执行一次。"))),
                ]
            ),
            repository=repository,
            registry=_offline_registry(duplicate_counter),
            protocol=AgentProtocol.NATIVE,
            model_id="fake/native",
        ).chat("重复调用")
        cases.append(
            _case_result(
                "duplicate_execution_guard",
                duplicate.ok and duplicate_counter.get("read", 0) == 1,
                executions=duplicate_counter.get("read", 0),
            )
        )

        loop_limited = AgentLoop(
            FakeProvider(
                [
                    ProviderResponse(
                        tool_calls=[
                            ProviderToolCall(
                                id=f"unknown-{index}",
                                name="missing_tool",
                                arguments={},
                            )
                        ]
                    )
                    for index in range(2)
                ]
            ),
            repository=repository,
            registry=_offline_registry({}),
            protocol=AgentProtocol.NATIVE,
            model_id="fake/native",
            max_model_calls=2,
        ).chat("循环上限")
        cases.append(
            _case_result(
                "loop_limit",
                not loop_limited.ok
                and loop_limited.error is not None
                and loop_limited.error.code == "model_loop_limit",
            )
        )

        persisted_session = repository.get_session_record(native.session_id)
        cases.append(
            _case_result(
                "persistent_session",
                len(persisted_session["messages"]) >= 2,
                message_count=len(persisted_session["messages"]),
            )
        )

        event = repository.add_event(
            native.run_id,
            native.session_id,
            "redaction_test",
            {
                "api_key": "should-not-appear",
                "message": "Authorization: Bearer should-not-appear",
            },
        )
        serialized_event = json.dumps(event, ensure_ascii=False)
        cases.append(
            _case_result(
                "secret_redaction",
                "should-not-appear" not in serialized_event and "***" in serialized_event,
            )
        )

        if limit is not None:
            cases = cases[:limit]

    passed = sum(bool(case["passed"]) for case in cases)
    return {
        "ok": passed == len(cases),
        "mode": "offline",
        "summary": {
            "total": len(cases),
            "passed": passed,
            "completion_rate": passed / len(cases) if cases else 1.0,
            "structured_output_rate": 1.0
            if all(
                case["passed"]
                for case in cases
                if case["name"] in {"native_structured_answer", "json_fallback"}
            )
            else 0.0,
            "approval_boundary_rate": 1.0
            if all(
                case["passed"]
                for case in cases
                if case["name"] == "approval_pause_resume"
            )
            else 0.0,
            "sensitive_information_rate": 1.0
            if all(
                case["passed"]
                for case in cases
                if case["name"] == "secret_redaction"
            )
            else 0.0,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        },
        "cases": cases,
    }


def run_live_evaluation(
    limit: int | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    *,
    start: int = 1,
    execute_actions: bool = False,
) -> dict[str, Any]:
    live_cases = load_live_cases()
    if start < 1:
        raise ValueError("start must be at least 1.")
    remaining_cases = live_cases[start - 1 :]
    selected_cases = remaining_cases[:limit] if limit is not None else remaining_cases
    rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    for index, case in enumerate(selected_cases, start=1):
        turns = [str(item) for item in case.get("turns") or [case["prompt"]]]
        prompt = " → ".join(turns)
        if progress_callback:
            progress_callback(index, len(selected_cases), prompt)
        case_started = time.perf_counter()
        loop = None
        proposal = None
        session_id = None
        run_ids: list[str] = []
        for turn in turns:
            loop = AgentLoop.from_config(repository=loop.repository if loop else None)
            proposal = loop.chat(turn, session_id=session_id)
            session_id = proposal.session_id
            run_ids.append(proposal.run_id)
            if proposal.pending_approval is not None:
                break
        if proposal is None or loop is None:
            raise AssertionError("A live evaluation case must contain at least one turn.")
        proposal_status = proposal.status.value
        result = proposal
        action_executed = False
        if (
            execute_actions
            and case.get("execute_action")
            and proposal.pending_approval is not None
        ):
            action_executed = True
            repository = loop.repository
            repository.decide_approval(
                proposal.pending_approval.tool_call_id,
                approved=True,
                expected_arguments_hash=proposal.pending_approval.arguments_hash,
            )
            result = AgentLoop.from_config(repository=repository).resume(
                proposal.pending_approval.tool_call_id
            )
        proposed_tools = [
            event["detail"].get("tool_name")
            for run_id in run_ids
            for event in loop.repository.list_events(run_id)
            if event["stage"] == "tool_proposed"
        ]
        action_case = bool(case.get("approval_expected"))
        expected_tool = case.get("expected_tool")
        allowed_tools = set(case.get("allowed_tools") or [])
        tool_correct = (
            str(expected_tool) in proposed_tools
            if expected_tool
            else (
                set(proposed_tools) <= allowed_tools
                if "allowed_tools" in case
                else True
            )
        )
        validation_failed = any(
            event["stage"] == "tool_validation_failed"
            for run_id in run_ids
            for event in loop.repository.list_events(run_id)
        )
        parameter_correct = bool(case.get("allow_validation_failure")) or not validation_failed
        tool_calls = [
            call
            for run_id in run_ids
            for call in loop.repository.list_tool_calls(run_id)
        ]
        expected_arguments = case.get("expected_arguments")
        arguments_correct = True
        if expected_arguments is not None and expected_tool:
            matching_calls = [
                call for call in tool_calls if call["tool_name"] == expected_tool
            ]
            arguments_correct = bool(matching_calls) and any(
                all(
                    call["arguments"].get(key) == value
                    for key, value in expected_arguments.items()
                )
                for call in matching_calls
            )
        expected_status = str(
            case.get("expected_status")
            or ("awaiting_approval" if action_case else "completed")
        )
        approval_correct = (
            proposal_status == "awaiting_approval"
            if action_case
            else proposal_status != "awaiting_approval"
        )
        answer = result.answer
        explanation_text = ""
        if answer:
            explanation_text = f"{answer.conclusion} {' '.join(answer.warnings)}".strip()
        explanation_ok = not case.get("requires_explanation") or (
            len(explanation_text) >= 12
            and any(
                token in explanation_text
                for token in (
                    "无法",
                    "不能",
                    "不支持",
                    "未",
                    "需要",
                    "请提供",
                    "失败",
                    "拒绝",
                )
            )
        )
        execution_ok = (
            not (execute_actions and case.get("execute_action"))
            or (
                action_executed
                and result.ok
                and any(call["status"] == "success" for call in tool_calls)
            )
        )
        passed = bool(
            tool_correct
            and parameter_correct
            and arguments_correct
            and approval_correct
            and proposal_status == expected_status
            and (result.ok or action_case)
            and explanation_ok
            and execution_ok
        )
        rows.append(
            {
                "case_id": index,
                "prompt": prompt,
                "turns": turns,
                "passed": bool(passed),
                "status": result.status.value,
                "proposal_status": proposal_status,
                "proposed_tools": proposed_tools,
                "expected_tool": expected_tool,
                "tool_correct": tool_correct,
                "parameter_correct": parameter_correct,
                "arguments_correct": arguments_correct,
                "approval_expected": action_case,
                "approval_correct": approval_correct,
                "action_executed": action_executed,
                "tool_call_statuses": [call["status"] for call in tool_calls],
                "conclusion": answer.conclusion if answer else "",
                "latency_seconds": round(time.perf_counter() - case_started, 3),
                "usage": result.usage,
                "error_code": result.error.code if result.error else None,
            }
        )
    passed = sum(bool(row["passed"]) for row in rows)
    tool_cases = [row for row in rows if row["proposed_tools"]]
    action_rows = [row for row in rows if row["approval_expected"]]
    approval_rate = (
        sum(row["proposal_status"] == "awaiting_approval" for row in action_rows)
        / len(action_rows)
        if action_rows
        else 1.0
    )
    tool_rate = (
        sum(row["tool_correct"] for row in rows) / len(rows) if rows else 1.0
    )
    parameter_rate = (
        sum(row["parameter_correct"] for row in rows) / len(rows)
        if rows
        else 1.0
    )
    return {
        "ok": (
            passed == len(rows)
            and tool_rate >= 0.85
            and parameter_rate >= 0.85
            and approval_rate == 1.0
        ),
        "mode": "live",
        "summary": {
            "total": len(rows),
            "passed": passed,
            "completion_rate": passed / len(rows) if rows else 1.0,
            "tool_selection_rate": tool_rate,
            "parameter_validation_rate": parameter_rate,
            "approval_boundary_rate": approval_rate,
            "tool_case_count": len(tool_cases),
            "action_cases_executed": sum(
                bool(row["action_executed"]) for row in rows
            ),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        },
        "cases": rows,
    }


def run_evaluation(
    *,
    mode: str,
    limit: int | None = None,
    output_path: Path | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    start: int = 1,
    execute_actions: bool = False,
) -> dict[str, Any]:
    if mode == "offline":
        report = run_offline_evaluation(limit)
    elif mode == "live":
        report = run_live_evaluation(
            limit,
            progress_callback,
            start=start,
            execute_actions=execute_actions,
        )
    else:
        raise ValueError("Evaluation mode must be offline or live.")
    safe_report = redact_value(report)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(safe_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        safe_report["output_path"] = str(output_path)
    return safe_report
