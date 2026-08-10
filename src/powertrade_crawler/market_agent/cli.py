from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from powertrade_crawler.credentials import get_credential
from powertrade_crawler.llm_router import (
    DEFAULT_MODEL_STRATEGY,
    FreeRouterManagementClient,
    LLMRouterError,
    load_free_router_config,
)
from powertrade_crawler.market_agent.data_tools import (
    SOURCE_LABELS,
    build_market_tool_registry,
)
from powertrade_crawler.market_agent.evaluation import (
    run_offline_evaluation,
    run_online_evaluation,
)
from powertrade_crawler.market_agent.loop import (
    MarketAgentLoop,
    build_configured_provider,
)
from powertrade_crawler.market_agent.provider import FakeProvider
from powertrade_crawler.market_agent.repository import MarketAgentRepository
from powertrade_crawler.market_agent.rag import RagIndexService
from powertrade_crawler.market_agent.rag_evaluation import run_rag_evaluation


market_agent_app = typer.Typer(help="独立的多数据源电力市场分析 Agent。")
config_app = typer.Typer(help="查看或修改多数据源 Agent 的非敏感模型配置。")
sessions_app = typer.Typer(help="管理多数据源 Agent 会话。")
approvals_app = typer.Typer(help="审批或拒绝多数据源 Agent 的采集操作。")
tools_app = typer.Typer(help="查看多数据源 Agent 的受控工具。")
rag_app = typer.Typer(help="管理和检索本地混合知识库。")
market_agent_app.add_typer(config_app, name="config")
market_agent_app.add_typer(sessions_app, name="sessions")
market_agent_app.add_typer(approvals_app, name="approvals")
market_agent_app.add_typer(tools_app, name="tools")
market_agent_app.add_typer(rag_app, name="rag")


@rag_app.command("status")
def rag_status(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    del json_output
    echo_json(RagIndexService().status())


@rag_app.command("update")
def rag_update(
    source: Annotated[list[str] | None, typer.Option("--source")] = None,
) -> None:
    service = RagIndexService()
    result = service.update(
        sources=source,
        progress_callback=lambda item: typer.echo(
            f"[{item['current']}/{item['total']}] {item['stage']}", err=True
        ),
    )
    echo_json(result)


@rag_app.command("rebuild")
def rag_rebuild() -> None:
    service = RagIndexService()
    result = service.rebuild(
        progress_callback=lambda item: typer.echo(
            f"[{item['current']}/{item['total']}] {item['stage']}", err=True
        )
    )
    echo_json(result)


@rag_app.command("search")
def rag_search(
    query: Annotated[str, typer.Argument(help="要检索的政策、规则或数据集问题。")],
    top_k: Annotated[int, typer.Option("--top-k", min=1, max=8)] = 6,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    del json_output
    echo_json(RagIndexService().search(query, top_k=top_k))


@rag_app.command("eval")
def rag_eval(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    del json_output
    report = run_rag_evaluation()
    echo_json(report)
    if not report["summary"]["accepted"]:
        raise typer.Exit(code=1)


def console_safe_json(
    value: Any,
    *,
    indent: int | None = 2,
    encoding: str | None = None,
) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=indent, default=str)
    output_encoding = encoding or sys.stdout.encoding or "utf-8"
    return text.encode(output_encoding, errors="backslashreplace").decode(output_encoding)


def echo_json(value) -> None:
    typer.echo(console_safe_json(value))


def fail_router_command(exc: Exception) -> None:
    echo_json(
        {
            "ok": False,
            "error": {
                "code": "free_router_error",
                "message": str(exc),
            },
        }
    )
    raise typer.Exit(code=2)


@market_agent_app.command("doctor")
def doctor(
    online: Annotated[
        bool,
        typer.Option("--online", help="同时请求本地免费池模型列表检查连接。"),
    ] = False,
) -> None:
    repository = MarketAgentRepository()
    try:
        free_router_status = load_free_router_config().safe_summary()
    except LLMRouterError as exc:
        free_router_status = {"status": "unavailable", "message": str(exc)}
    result = {
        "config": repository.get_config(),
        "credentials": {
            "free_router": free_router_status,
            "elecheck": bool(get_credential("elecheck_authorization")),
            "entsoe": bool(get_credential("entsoe_security_token")),
            "elexon": bool(get_credential("elexon_api_key")),
            "gridstatus": bool(get_credential("gridstatus_api_key")),
            "gzpec": True,
        },
        "sources": SOURCE_LABELS,
        "connection": "not_checked",
    }
    if online:
        try:
            provider, _config = build_configured_provider(repository)
            try:
                result["available_models"] = provider.list_models()
                result["connection"] = "ok"
            finally:
                provider.close()
        except Exception as exc:
            result["connection"] = "failed"
            result["connection_error"] = str(exc)
    echo_json(result)


@config_app.command("show")
def config_show() -> None:
    echo_json(MarketAgentRepository().get_config())


@config_app.command("set")
def config_set(
    endpoint: Annotated[str | None, typer.Option()] = None,
    model_id: Annotated[str | None, typer.Option()] = None,
    advanced_model_id: Annotated[str | None, typer.Option()] = None,
    model_profile: Annotated[str | None, typer.Option()] = None,
    reasoning_effort: Annotated[str | None, typer.Option()] = None,
    protocol: Annotated[str | None, typer.Option()] = None,
) -> None:
    try:
        config = MarketAgentRepository().set_config(
            endpoint=endpoint,
            model_id=model_id,
            advanced_model_id=advanced_model_id,
            model_profile=model_profile,
            reasoning_effort=reasoning_effort,
            protocol=protocol,
        )
    except (ValueError, LLMRouterError) as exc:
        fail_router_command(exc)
    echo_json(config)


@config_app.command("providers")
def config_providers() -> None:
    """查询当前免费渠道及其可用状态。"""
    try:
        with FreeRouterManagementClient.from_external_config() as router:
            echo_json(router.list_providers())
    except LLMRouterError as exc:
        fail_router_command(exc)


@config_app.command("alerts")
def config_alerts() -> None:
    """查询免费渠道的失效或过期告警。"""
    try:
        with FreeRouterManagementClient.from_external_config() as router:
            echo_json(router.list_alerts())
    except LLMRouterError as exc:
        fail_router_command(exc)


@config_app.command("strategy")
def config_strategy() -> None:
    """查询当前模型选择策略。"""
    try:
        repository = MarketAgentRepository()
        with FreeRouterManagementClient.from_external_config() as router:
            echo_json(router.describe_strategy(repository.get_config()["model_id"]))
    except LLMRouterError as exc:
        fail_router_command(exc)


@config_app.command("use-auto")
def config_use_auto() -> None:
    """恢复 smart-auto 免费自动选择策略。"""
    try:
        config = MarketAgentRepository().set_config(
            model_id=DEFAULT_MODEL_STRATEGY
        )
    except (ValueError, LLMRouterError) as exc:
        fail_router_command(exc)
    echo_json(config)


@config_app.command("use-provider")
def config_use_provider(
    provider_id: Annotated[str, typer.Argument(help="免费渠道 ID。")],
) -> None:
    """固定到一个 tier=free 且 available=true 的渠道。"""
    strategy = (
        provider_id if provider_id.startswith("provider/") else f"provider/{provider_id}"
    )
    try:
        echo_json(MarketAgentRepository().set_config(model_id=strategy))
    except (ValueError, LLMRouterError) as exc:
        fail_router_command(exc)


@market_agent_app.command("chat")
def chat(
    message: Annotated[str, typer.Argument(help="自然语言业务问题。")],
    session_id: Annotated[str | None, typer.Option("--session")] = None,
) -> None:
    result = MarketAgentLoop.from_config().chat(message, session_id=session_id)
    echo_json(result.model_dump(mode="json"))
    if not result.ok and result.pending_approval is None:
        raise typer.Exit(code=1)


@sessions_app.command("list")
def sessions_list() -> None:
    echo_json(MarketAgentRepository().list_sessions())


@sessions_app.command("show")
def sessions_show(session_id: str) -> None:
    repository = MarketAgentRepository()
    record = repository.get_session_record(session_id)
    if record["latest_run_id"]:
        record["latest_run"] = repository.get_run(record["latest_run_id"])
        record["events"] = repository.list_events(record["latest_run_id"])
    echo_json(record)


@sessions_app.command("delete")
def sessions_delete(
    session_id: str,
    yes: Annotated[bool, typer.Option("--yes", help="确认删除 Agent 会话记录。")] = False,
) -> None:
    if not yes:
        raise typer.BadParameter("删除会话需要 --yes；不会删除业务数据。")
    MarketAgentRepository().delete_session(session_id)
    echo_json({"deleted_session": session_id})


@approvals_app.command("list")
def approvals_list() -> None:
    echo_json(MarketAgentRepository().list_pending_approvals())


def decide_approval(call_id: str, approved: bool, expected_hash: str | None) -> None:
    repository = MarketAgentRepository()
    loop = (
        MarketAgentLoop.from_config(repository=repository)
        if approved
        else MarketAgentLoop(FakeProvider([]), repository=repository)
    )
    result = loop.resume_approval(
        call_id,
        approved=approved,
        expected_arguments_hash=expected_hash,
    )
    echo_json(result.model_dump(mode="json"))
    if not result.ok and approved:
        raise typer.Exit(code=1)


@approvals_app.command("approve")
def approvals_approve(
    call_id: str,
    expected_hash: Annotated[str | None, typer.Option("--arguments-hash")] = None,
) -> None:
    decide_approval(call_id, True, expected_hash)


@approvals_app.command("reject")
def approvals_reject(
    call_id: str,
    expected_hash: Annotated[str | None, typer.Option("--arguments-hash")] = None,
) -> None:
    decide_approval(call_id, False, expected_hash)


@tools_app.command("list")
def tools_list() -> None:
    echo_json(build_market_tool_registry().describe())


@tools_app.command("show")
def tools_show(name: str) -> None:
    registry = build_market_tool_registry()
    matching = [item for item in registry.describe() if item["name"] == name]
    if not matching:
        raise typer.BadParameter(f"Unknown market agent tool: {name}")
    echo_json(matching[0])


@market_agent_app.command("eval")
def evaluate(
    online: Annotated[
        bool,
        typer.Option("--online", help="运行真实模型的15项只读评测。"),
    ] = False,
    start: Annotated[int, typer.Option("--start", min=1)] = 1,
    limit: Annotated[int | None, typer.Option("--limit", min=1)] = None,
    execute_collections: Annotated[
        bool,
        typer.Option(
            "--execute-collections",
            help="审批并执行案例中显式标记的小范围真实采集。",
        ),
    ] = False,
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    if execute_collections and not online:
        raise typer.BadParameter("--execute-collections 只能与 --online 一起使用。")
    report = (
        run_online_evaluation(
            start=start,
            limit=limit,
            execute_collections=execute_collections,
            progress_callback=lambda index, total, prompt: typer.echo(
                f"[{index}/{total}] {prompt}",
                err=True,
            ),
        )
        if online
        else run_offline_evaluation()
    )
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        report["output_path"] = str(output.resolve())
    echo_json(report)
    if report["summary"]["failed"]:
        raise typer.Exit(code=1)
