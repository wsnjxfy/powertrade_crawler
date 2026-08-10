from __future__ import annotations

from typing import Any


def explain_agent_failure(
    *,
    code: str,
    message: str,
    retryable: bool,
    scope_label: str,
) -> tuple[str, str]:
    """Return why the request failed and the concrete next action."""
    compact = " ".join(message.split())
    lowered = compact.lower()
    if "invalid argument" in lowered or code == "invalid_tool_call":
        return (
            "这次请求没有执行：工具参数未通过安全校验。",
            "请补充数据来源、数据集、地区、日期或业务类型后重试。",
        )
    if code == "approval_rejected":
        return (
            "操作没有执行，因为用户拒绝了本次审批。",
            "如需继续，请重新发起任务并核对审批范围和参数。",
        )
    if any(token in lowered for token in ("credential", "api key", "token", "401", "403", "凭据")):
        return (
            f"这次请求没有完成：{scope_label}所需凭据缺失、失效或没有访问权限。",
            "请打开“API 配置向导”，按对应数据源重新配置并执行连接验证。",
        )
    if "429" in lowered or "请求过于频繁" in compact:
        return (
            "这次请求没有完成：外部服务触发了请求频率限制。",
            "请稍后重试、缩短日期范围，或降低定时任务的运行频率。",
        )
    if "服务暂时异常" in compact or any(f"http {status}" in lowered for status in range(500, 600)):
        return (
            "这次请求没有完成：外部数据服务暂时异常；这不代表没有新数据。",
            "请稍后重试，并在任务时间线查看失败的数据源或步骤。",
        )
    if any(token in compact for token in ("无法连接", "断网")) or any(
        token in lowered for token in ("connecterror", "connection error", "offline")
    ):
        return (
            "这次请求没有完成：当前无法连接外部服务。",
            "请检查网络、代理和目标网站状态后重试。",
        )
    if "timeout" in lowered or "超时" in compact or retryable:
        return (
            "这次请求没有完成：免费模型或外部数据服务响应超时。",
            "请稍后重试，或缩短日期范围、减少一次请求中的任务数量。",
        )
    if any(token in lowered for token in ("model unavailable", "provider unavailable")):
        return (
            "这次请求没有完成：当前没有可用的免费模型。",
            "请检查本机免费模型网关状态，稍后重试；系统不会自动切换到付费模型。",
        )
    detail = compact[:240].rstrip()
    suffix = f" 原因：{detail}" if detail else ""
    return (
        f"这次请求没有完成。{suffix}".strip(),
        "请根据上述原因调整请求；仍失败时查看任务时间线中的错误类型和步骤。",
    )


def completed_change_explanation(tool_calls: list[dict[str, Any]]) -> tuple[list[str], str]:
    completed = [row for row in tool_calls if row.get("status") == "success"]
    names = [str(row.get("tool_name") or "") for row in completed]
    changed: list[str] = []
    for row in completed:
        name = str(row.get("tool_name") or "")
        result = row.get("result")
        if _is_configuration_change(name) or _positive_write_count(result):
            changed.append(name)
    if changed:
        return names, (
            "失败前已成功完成变更："
            + "、".join(changed)
            + "；这些结果已保留，请在任务时间线和对应页面核对。"
        )
    if completed:
        return names, "失败前只完成了查询或零写入操作，业务数据和任务配置未被修改。"
    return names, "本次没有成功执行工具，业务数据和任务配置未被修改。"


def _is_configuration_change(tool_name: str) -> bool:
    return any(
        marker in tool_name
        for marker in (
            "create_schedule",
            "enable_schedule",
            "disable_schedule",
            "install_windows_schedule",
            "uninstall_windows_schedule",
        )
    )


def _positive_write_count(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower()
            if normalized in {
                "records_upserted",
                "records_written",
                "rows_written",
                "deleted",
                "updated",
            }:
                try:
                    if int(child or 0) > 0:
                        return True
                except (TypeError, ValueError):
                    pass
            if _positive_write_count(child):
                return True
    elif isinstance(value, list):
        return any(_positive_write_count(child) for child in value)
    return False
