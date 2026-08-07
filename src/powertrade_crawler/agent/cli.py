from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from powertrade_crawler.agent.elecheck_tools import build_elecheck_tool_registry
from powertrade_crawler.agent.loop import AgentLoop, build_configured_provider
from powertrade_crawler.agent.repository import AgentRepository
from powertrade_crawler.agent.schemas import AgentRunResult, RiskLevel
from powertrade_crawler.llm_router import (
    DEFAULT_MODEL_STRATEGY,
    FreeRouterManagementClient,
    LLMRouterError,
    load_free_router_config,
)


EXIT_CONFIG = 2
EXIT_RUN_FAILED = 3
EXIT_AWAITING_APPROVAL = 4

agent_app = typer.Typer(help="Elecheck 电力市场分析 Agent。")
config_app = typer.Typer(help="查看或修改非敏感模型配置。")
sessions_app = typer.Typer(help="管理本地 Agent 会话。")
approvals_app = typer.Typer(help="审批或拒绝待执行的 Agent 操作。")
tools_app = typer.Typer(help="查看 Agent 实际注册的 Elecheck 工具。")
agent_app.add_typer(config_app, name="config")
agent_app.add_typer(sessions_app, name="sessions")
agent_app.add_typer(approvals_app, name="approvals")
agent_app.add_typer(tools_app, name="tools")


def emit_json(*, ok: bool, data: Any = None, error: Any = None) -> None:
    typer.echo(
        json.dumps(
            {"ok": ok, "data": data if ok else None, "error": error if not ok else None},
            ensure_ascii=False,
            default=str,
        )
    )


def fail(
    message: str,
    *,
    code: str,
    json_output: bool,
    exit_code: int,
    details: dict[str, Any] | None = None,
) -> None:
    error = {"code": code, "message": message, "details": details or {}}
    if json_output:
        emit_json(ok=False, error=error)
    else:
        typer.echo(f"错误：{message}", err=True)
    raise typer.Exit(exit_code)


def result_payload(result: AgentRunResult) -> dict[str, Any]:
    return result.model_dump(mode="json")


def render_result(result: AgentRunResult, *, json_output: bool) -> None:
    payload = result_payload(result)
    if json_output:
        if result.ok:
            emit_json(ok=True, data=payload)
        else:
            emit_json(
                ok=False,
                error={
                    "code": (
                        result.error.code
                        if result.error
                        else "awaiting_approval"
                    ),
                    "message": (
                        result.error.message
                        if result.error
                        else "运行已暂停，等待用户审批。"
                    ),
                    "details": payload,
                },
            )
        return
    typer.echo(f"会话：{result.session_id}")
    typer.echo(f"运行：{result.run_id}")
    typer.echo(f"状态：{result.status.value}")
    if result.answer:
        typer.echo(json.dumps(result.answer.model_dump(mode="json"), ensure_ascii=False, indent=2))
    if result.pending_approval:
        approval = result.pending_approval
        typer.echo(f"待审批工具：{approval.tool_name}")
        typer.echo(f"风险等级：{approval.risk_level.value}")
        typer.echo(
            "参数：" + json.dumps(approval.arguments, ensure_ascii=False, indent=2)
        )
        typer.echo(f"副作用：{approval.side_effect}")
        typer.echo(f"审批 ID：{approval.tool_call_id}")
    if result.error:
        typer.echo(f"错误 [{result.error.code}]：{result.error.message}", err=True)


def progress_callback(json_output: bool):
    def callback(event: dict[str, Any]) -> None:
        stage = event["stage"]
        detail = event.get("detail") or {}
        if json_output:
            typer.echo(
                json.dumps({"stage": stage, "detail": detail}, ensure_ascii=False),
                err=True,
            )
        else:
            typer.echo(f"[Agent] {stage}", err=True)

    return callback


@agent_app.command("doctor")
def doctor(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="使用稳定 JSON envelope 输出。"),
    ] = False,
) -> None:
    """检查数据库、免费池配置、Endpoint、模型和工具协议。"""
    try:
        repository = AgentRepository()
        config = repository.get_config()
    except Exception as exc:
        fail(
            str(exc),
            code="database_error",
            json_output=json_output,
            exit_code=EXIT_CONFIG,
        )
    try:
        external_config = load_free_router_config()
        router_config_check = {"ok": True, **external_config.safe_summary()}
    except LLMRouterError as exc:
        router_config_check = {"ok": False, "message": str(exc)}
    checks: dict[str, Any] = {
        "database": {"ok": True},
        "free_router_config": router_config_check,
        "endpoint": {
            "ok": config["endpoint"].startswith("http://127.0.0.1:"),
            "value": config["endpoint"],
        },
        "model": {
            "ok": bool(config["model_id"]),
            "strategy": config["model_id"],
        },
        "tool_protocol": {
            "ok": False,
            "configured": config["protocol"],
            "detected": config.get("detected_protocol"),
        },
    }
    provider = None
    if all(
        checks[name]["ok"]
        for name in ("free_router_config", "endpoint", "model")
    ):
        try:
            provider, active = build_configured_provider(repository)
            models = provider.list_models()
            active_model = active["active_model_id"]
            checks["model"].update(
                {
                    "active_model_id": active_model,
                    "available": active_model in models,
                    "available_model_count": len(models),
                }
            )
            probe = provider.complete(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "这是无副作用能力探测。必须调用 "
                            "powertrade_capability_probe，不要输出普通回答。"
                        ),
                    },
                    {"role": "user", "content": "执行能力探测。"},
                ],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "powertrade_capability_probe",
                            "description": "无副作用的工具调用协议探测。",
                            "parameters": {
                                "type": "object",
                                "properties": {},
                                "additionalProperties": False,
                            },
                        },
                    }
                ],
            )
            detected = "native" if probe.tool_calls else "json"
            repository.set_config(detected_protocol=detected)
            checks["tool_protocol"].update({"ok": True, "detected": detected})
            checks["last_route"] = {
                "ok": bool(probe.router_provider),
                "provider": probe.router_provider,
                "upstream_model": probe.upstream_model,
                "alert_count": probe.router_alert_count,
            }
        except Exception as exc:
            checks["remote"] = {
                "ok": False,
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        finally:
            if provider is not None:
                provider.close()
    overall = all(
        value.get("ok", False)
        for key, value in checks.items()
        if key
        in {
            "database",
            "free_router_config",
            "endpoint",
            "model",
            "tool_protocol",
        }
    )
    if json_output:
        if overall:
            emit_json(ok=True, data={"checks": checks})
        else:
            emit_json(
                ok=False,
                error={
                    "code": "doctor_failed",
                    "message": "一项或多项 Agent 检查未通过。",
                    "details": {"checks": checks},
                },
            )
            raise typer.Exit(EXIT_CONFIG)
    else:
        for name, value in checks.items():
            typer.echo(f"{name}: {'ok' if value.get('ok') else 'failed'}")
            typer.echo(json.dumps(value, ensure_ascii=False, indent=2))
        if not overall:
            raise typer.Exit(EXIT_CONFIG)


@config_app.command("show")
def config_show(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    config = AgentRepository().get_config()
    if json_output:
        emit_json(ok=True, data=config)
    else:
        typer.echo(json.dumps(config, ensure_ascii=False, indent=2))


@config_app.command("set")
def config_set(
    endpoint: Annotated[str | None, typer.Option("--endpoint")] = None,
    model_id: Annotated[str | None, typer.Option("--model")] = None,
    advanced_model_id: Annotated[
        str | None,
        typer.Option("--advanced-model"),
    ] = None,
    model_profile: Annotated[
        str | None,
        typer.Option("--profile", help="free 或 advanced。"),
    ] = None,
    protocol: Annotated[
        str | None,
        typer.Option("--protocol", help="auto、native 或 json。"),
    ] = None,
    reasoning_effort: Annotated[
        str | None,
        typer.Option("--reasoning-effort", help="low、medium 或 high。"),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        config = AgentRepository().set_config(
            endpoint=endpoint,
            model_id=model_id,
            advanced_model_id=advanced_model_id,
            model_profile=model_profile,
            protocol=protocol,
            reasoning_effort=reasoning_effort,
        )
    except (ValueError, LLMRouterError) as exc:
        fail(
            str(exc),
            code="invalid_config",
            json_output=json_output,
            exit_code=EXIT_CONFIG,
        )
    if json_output:
        emit_json(ok=True, data=config)
    else:
        typer.echo(json.dumps(config, ensure_ascii=False, indent=2))


@config_app.command("providers")
def config_providers(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """查询当前免费渠道及其可用状态。"""
    try:
        with FreeRouterManagementClient.from_external_config() as router:
            providers = router.list_providers()
    except LLMRouterError as exc:
        fail(
            str(exc),
            code="router_unavailable",
            json_output=json_output,
            exit_code=EXIT_CONFIG,
        )
    if json_output:
        emit_json(ok=True, data=providers)
    else:
        typer.echo(json.dumps(providers, ensure_ascii=False, indent=2))


@config_app.command("alerts")
def config_alerts(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """查询免费渠道的失效或过期告警。"""
    try:
        with FreeRouterManagementClient.from_external_config() as router:
            alerts = router.list_alerts()
    except LLMRouterError as exc:
        fail(
            str(exc),
            code="router_unavailable",
            json_output=json_output,
            exit_code=EXIT_CONFIG,
        )
    if json_output:
        emit_json(ok=True, data=alerts)
    else:
        typer.echo(json.dumps(alerts, ensure_ascii=False, indent=2))


@config_app.command("strategy")
def config_strategy(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """查询当前模型选择策略。"""
    try:
        repository = AgentRepository()
        with FreeRouterManagementClient.from_external_config() as router:
            strategy = router.describe_strategy(repository.get_config()["model_id"])
    except LLMRouterError as exc:
        fail(
            str(exc),
            code="router_unavailable",
            json_output=json_output,
            exit_code=EXIT_CONFIG,
        )
    if json_output:
        emit_json(ok=True, data=strategy)
    else:
        typer.echo(json.dumps(strategy, ensure_ascii=False, indent=2))


@config_app.command("use-auto")
def config_use_auto(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """恢复 smart-auto 免费自动选择策略。"""
    try:
        config = AgentRepository().set_config(model_id=DEFAULT_MODEL_STRATEGY)
    except (ValueError, LLMRouterError) as exc:
        fail(
            str(exc),
            code="invalid_strategy",
            json_output=json_output,
            exit_code=EXIT_CONFIG,
        )
    if json_output:
        emit_json(ok=True, data=config)
    else:
        typer.echo(json.dumps(config, ensure_ascii=False, indent=2))


@config_app.command("use-provider")
def config_use_provider(
    provider_id: Annotated[str, typer.Argument(help="免费渠道 ID。")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """固定到一个 tier=free 且 available=true 的渠道。"""
    strategy = (
        provider_id if provider_id.startswith("provider/") else f"provider/{provider_id}"
    )
    try:
        config = AgentRepository().set_config(model_id=strategy)
    except (ValueError, LLMRouterError) as exc:
        fail(
            str(exc),
            code="invalid_strategy",
            json_output=json_output,
            exit_code=EXIT_CONFIG,
        )
    if json_output:
        emit_json(ok=True, data=config)
    else:
        typer.echo(json.dumps(config, ensure_ascii=False, indent=2))


@agent_app.command("chat")
def chat(
    session_id: Annotated[str | None, typer.Option("--session")] = None,
    message: Annotated[str | None, typer.Option("--message", "-m")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """发送单条消息；未提供 message 时进入交互模式。"""
    current_session = session_id
    while True:
        if message is None:
            try:
                prompt = typer.prompt("你")
            except (EOFError, KeyboardInterrupt):
                return
            if prompt.strip().lower() in {"exit", "quit", "/exit", "/quit"}:
                return
        else:
            prompt = message
        try:
            loop = AgentLoop.from_config(
                event_callback=progress_callback(json_output)
            )
            result = loop.chat(prompt, session_id=current_session)
        except ValueError as exc:
            fail(
                str(exc),
                code="agent_config_error",
                json_output=json_output,
                exit_code=EXIT_CONFIG,
            )
        current_session = result.session_id
        render_result(result, json_output=json_output)
        if result.status.value == "awaiting_approval":
            if message is not None:
                raise typer.Exit(EXIT_AWAITING_APPROVAL)
            typer.echo("运行已暂停。请使用 agent approvals approve|reject 审批。")
        elif not result.ok and message is not None:
            raise typer.Exit(EXIT_RUN_FAILED)
        if message is not None:
            return


@sessions_app.command("list")
def sessions_list(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    rows = AgentRepository().list_sessions()
    if json_output:
        emit_json(ok=True, data={"sessions": rows})
    else:
        for row in rows:
            typer.echo(f"{row['id']}\t{row['updated_at']}\t{row['title']}")


@sessions_app.command("show")
def sessions_show(
    session_id: Annotated[str, typer.Argument()],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        row = AgentRepository().get_session_record(session_id)
    except ValueError as exc:
        fail(
            str(exc),
            code="unknown_session",
            json_output=json_output,
            exit_code=EXIT_CONFIG,
        )
    if json_output:
        emit_json(ok=True, data=row)
    else:
        typer.echo(json.dumps(row, ensure_ascii=False, indent=2))


@sessions_app.command("delete")
def sessions_delete(
    session_id: Annotated[str, typer.Argument()],
    yes: Annotated[bool, typer.Option("--yes", help="跳过交互确认。")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    if not yes and not typer.confirm(f"删除本地 Agent 会话 {session_id} 及其运行轨迹？"):
        raise typer.Abort()
    try:
        AgentRepository().delete_session(session_id)
    except ValueError as exc:
        fail(
            str(exc),
            code="unknown_session",
            json_output=json_output,
            exit_code=EXIT_CONFIG,
        )
    if json_output:
        emit_json(ok=True, data={"deleted_session_id": session_id})
    else:
        typer.echo(f"已删除会话 {session_id}")


@approvals_app.command("list")
def approvals_list(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    rows = AgentRepository().list_pending_approvals()
    if json_output:
        emit_json(ok=True, data={"approvals": rows})
    else:
        for row in rows:
            typer.echo(
                f"{row['id']}\t{row['risk_level']}\t{row['tool_name']}\t"
                f"{json.dumps(row['arguments'], ensure_ascii=False)}"
            )


def decide_and_resume(
    tool_call_id: str,
    *,
    approved: bool,
    yes: bool,
    strong_confirm: str | None,
    json_output: bool,
) -> None:
    repository = AgentRepository()
    try:
        call = repository.get_tool_call(tool_call_id)
        tool = build_elecheck_tool_registry().get(call["tool_name"])
    except ValueError as exc:
        fail(
            str(exc),
            code="unknown_approval",
            json_output=json_output,
            exit_code=EXIT_CONFIG,
        )
    if approved:
        if not yes:
            typer.echo(f"工具：{tool.name}")
            typer.echo(
                "参数：" + json.dumps(call["arguments"], ensure_ascii=False, indent=2)
            )
            typer.echo(f"副作用：{tool.side_effect}")
            if not typer.confirm("确认执行以上操作？"):
                raise typer.Abort()
        if tool.risk_level == RiskLevel.STRONG_APPROVAL:
            confirmation = strong_confirm
            if confirmation is None:
                confirmation = typer.prompt(f"二次确认：请输入工具名 {tool.name}")
            if confirmation != tool.name:
                fail(
                    "强化审批确认文本不匹配，操作未执行。",
                    code="strong_approval_mismatch",
                    json_output=json_output,
                    exit_code=EXIT_CONFIG,
                )
    try:
        repository.decide_approval(
            tool_call_id,
            approved=approved,
            expected_arguments_hash=call["arguments_hash"],
        )
        loop = AgentLoop.from_config(
            repository=repository,
            event_callback=progress_callback(json_output),
        )
        result = loop.resume(tool_call_id)
    except ValueError as exc:
        fail(
            str(exc),
            code="approval_resume_failed",
            json_output=json_output,
            exit_code=EXIT_CONFIG,
        )
    render_result(result, json_output=json_output)
    if result.status.value == "awaiting_approval":
        raise typer.Exit(EXIT_AWAITING_APPROVAL)
    if not result.ok:
        raise typer.Exit(EXIT_RUN_FAILED)


@approvals_app.command("approve")
def approvals_approve(
    tool_call_id: Annotated[str, typer.Argument()],
    yes: Annotated[bool, typer.Option("--yes")] = False,
    strong_confirm: Annotated[
        str | None,
        typer.Option("--strong-confirm", help="强化审批时必须精确输入工具名。"),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    decide_and_resume(
        tool_call_id,
        approved=True,
        yes=yes,
        strong_confirm=strong_confirm,
        json_output=json_output,
    )


@approvals_app.command("reject")
def approvals_reject(
    tool_call_id: Annotated[str, typer.Argument()],
    yes: Annotated[bool, typer.Option("--yes")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    if not yes and not typer.confirm("确认拒绝该操作并让 Agent 继续生成结论？"):
        raise typer.Abort()
    decide_and_resume(
        tool_call_id,
        approved=False,
        yes=True,
        strong_confirm=None,
        json_output=json_output,
    )


@tools_app.command("list")
def tools_list(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    rows = build_elecheck_tool_registry().describe()
    compact = [
        {
            "name": row["name"],
            "risk_level": row["risk_level"],
            "description": row["description"],
        }
        for row in rows
    ]
    if json_output:
        emit_json(ok=True, data={"tools": compact})
    else:
        for row in compact:
            typer.echo(f"{row['name']}\t{row['risk_level']}\t{row['description']}")


@tools_app.command("show")
def tools_show(
    tool_name: Annotated[str, typer.Argument()],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        tool = build_elecheck_tool_registry().get(tool_name)
    except ValueError as exc:
        fail(
            str(exc),
            code="unknown_tool",
            json_output=json_output,
            exit_code=EXIT_CONFIG,
        )
    payload = {
        "name": tool.name,
        "description": tool.description,
        "risk_level": tool.risk_level.value,
        "side_effect": tool.side_effect,
        "parameters": tool.args_model.model_json_schema(),
    }
    if json_output:
        emit_json(ok=True, data=payload)
    else:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@agent_app.command("eval")
def evaluate(
    mode: Annotated[str, typer.Option("--mode")] = "offline",
    limit: Annotated[int | None, typer.Option("--limit", min=1)] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    from powertrade_crawler.agent.evaluation import run_evaluation

    try:
        report = run_evaluation(
            mode=mode,
            limit=limit,
            output_path=output,
            progress_callback=lambda index, total, _prompt: typer.echo(
                f"[Agent Eval] {index}/{total}",
                err=True,
            ),
        )
    except ValueError as exc:
        fail(
            str(exc),
            code="evaluation_config_error",
            json_output=json_output,
            exit_code=EXIT_CONFIG,
        )
    if json_output:
        emit_json(ok=bool(report["ok"]), data=report if report["ok"] else None, error=None if report["ok"] else report)
    else:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["ok"]:
        raise typer.Exit(EXIT_RUN_FAILED)


if __name__ == "__main__":
    agent_app()
