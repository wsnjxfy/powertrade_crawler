import pytest

from powertrade_crawler.failure_messages import (
    completed_change_explanation,
    explain_agent_failure,
)


@pytest.mark.parametrize(
    ("code", "message", "retryable", "expected", "guidance"),
    [
        (
            "invalid_tool_call",
            "invalid arguments",
            False,
            "参数未通过安全校验",
            "补充数据来源",
        ),
        (
            "tool_execution_failed",
            "GridStatus 请求过于频繁（HTTP 429）",
            True,
            "请求频率限制",
            "降低定时任务",
        ),
        (
            "tool_execution_failed",
            "ENTSO-E 服务暂时异常（HTTP 503）",
            True,
            "不代表没有新数据",
            "任务时间线",
        ),
        (
            "tool_execution_failed",
            "Elexon 无法连接",
            True,
            "无法连接外部服务",
            "检查网络",
        ),
        (
            "tool_execution_failed",
            "Elecheck HTTP 401",
            False,
            "凭据缺失、失效",
            "API 配置向导",
        ),
        (
            "provider_error",
            "model unavailable",
            False,
            "没有可用的免费模型",
            "不会自动切换到付费模型",
        ),
        (
            "approval_rejected",
            "rejected",
            False,
            "拒绝了本次审批",
            "重新发起任务",
        ),
    ],
)
def test_failure_paths_always_explain_why_and_next_step(
    code,
    message,
    retryable,
    expected,
    guidance,
):
    conclusion, next_step = explain_agent_failure(
        code=code,
        message=message,
        retryable=retryable,
        scope_label="对应数据源",
    )

    assert expected in conclusion
    assert guidance in next_step


def test_completed_change_explanation_reports_whether_state_changed():
    names, unchanged = completed_change_explanation(
        [
            {
                "tool_name": "market_query_series",
                "status": "success",
                "result": {"ok": True, "data": {"rows": []}},
            }
        ]
    )
    assert names == ["market_query_series"]
    assert "未被修改" in unchanged

    names, changed = completed_change_explanation(
        [
            {
                "tool_name": "market_collect_entsoe",
                "status": "success",
                "result": {"ok": True, "data": {"records_upserted": 24}},
            }
        ]
    )
    assert names == ["market_collect_entsoe"]
    assert "已成功完成变更" in changed
    assert "已保留" in changed
