import csv
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from powertrade_crawler import elecheck_collection
from powertrade_crawler.agent import elecheck_tools
from powertrade_crawler.agent.elecheck_tools import build_elecheck_tool_registry
from powertrade_crawler.agent.evaluation import run_offline_evaluation
from powertrade_crawler.agent.gui import ElecheckAgentApp
from powertrade_crawler.agent.provider import FakeProvider
from powertrade_crawler.agent.repository import AgentRepository
from powertrade_crawler.agent.schemas import (
    AgentAnswer,
    AgentProtocol,
    ProviderResponse,
    ProviderToolCall,
    RiskLevel,
    RunStatus,
)
from powertrade_crawler.agent.tools import ToolContext
from powertrade_crawler.agent.loop import (
    AgentLoop,
    deterministic_all_spot_update_end_date,
    deterministic_elecheck_action,
    deterministic_elecheck_boundary_answer,
    deterministic_monthly_extrema_routes,
)
from powertrade_crawler.cli import app
from powertrade_crawler.config import get_settings


@pytest.fixture
def agent_database(monkeypatch, tmp_path):
    db_path = tmp_path / "agent.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    yield db_path
    get_settings.cache_clear()


def structured_answer(conclusion="完成"):
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


def test_agent_tables_and_default_models_are_created(agent_database):
    repository = AgentRepository()

    assert repository.get_config()["model_id"] == "smart-auto"
    with sqlite3.connect(agent_database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {
        "agent_settings",
        "agent_sessions",
        "agent_messages",
        "agent_runs",
        "agent_tool_calls",
        "agent_events",
    } <= tables


def test_agent_persists_free_router_route_metadata(agent_database):
    repository = AgentRepository()
    provider = FakeProvider(
        [
            ProviderResponse(
                content=json.dumps(structured_answer()),
                raw_protocol="json",
                router_provider="free-route",
                upstream_model="upstream/model",
                router_alert_count=1,
            )
        ]
    )
    loop = AgentLoop(
        provider,
        repository=repository,
        protocol=AgentProtocol.JSON,
        model_id="smart-auto",
    )

    result = loop.chat("测试路由记录")
    run = repository.get_run(result.run_id)

    assert run["router_provider"] == "free-route"
    assert run["upstream_model"] == "upstream/model"
    assert run["router_alert_count"] == 1
    assert any(
        event["stage"] == "model_completed"
        and event["detail"]["router_provider"] == "free-route"
        for event in repository.list_events(result.run_id)
    )


def test_agent_session_title_is_redacted(agent_database):
    repository = AgentRepository()
    session_id = repository.create_session()

    repository.add_message(
        session_id,
        role="user",
        content={"content": "Authorization: Bearer should-not-be-stored"},
    )

    title = repository.get_session_record(session_id)["title"]
    assert "should-not-be-stored" not in title
    assert "***" in title


def test_agent_answer_canonicalizes_data_source():
    answer = AgentAnswer(
        conclusion="完成",
        data_sources=["local SQLite"],
    )

    assert answer.data_sources == ["Elecheck 易能电易查"]


def test_agent_gui_formats_structured_result_for_readability():
    text = ElecheckAgentApp._format_result(
        {
            "conclusion": "最低价格为 333.4 CNY/MWh。",
            "business_metrics": [
                {
                    "name": "最低日前价格",
                    "value": 333.4,
                    "unit": "CNY/MWh",
                    "description": "09:30",
                }
            ],
            "completeness": [
                {
                    "name": "日前完整度",
                    "actual": 96,
                    "expected": 96,
                    "ratio": 1.0,
                }
            ],
            "warnings": [],
            "generated_files": [],
            "executed_actions": ["elecheck_analyze_spot"],
            "data_sources": ["Elecheck 易能电易查"],
        }
    )

    assert "结论\n最低价格为 333.4 CNY/MWh。" in text
    assert "• 最低日前价格：333.4 CNY/MWh · 09:30" in text
    assert "• 日前完整度：96/96（100.0%）" in text
    assert ElecheckAgentApp._event_tag("tool_completed") == "success"
    assert ElecheckAgentApp._event_tag("approval_required") == "warning"
    assert ElecheckAgentApp._event_tag("failed") == "error"


def test_tool_registry_does_not_expose_forbidden_capabilities():
    registry = build_elecheck_tool_registry()

    assert not {
        "sql",
        "shell",
        "delete_data",
        "delete_schedule",
        "set_credential",
        "read_file",
    } & set(registry.names())
    assert registry.get("elecheck_sql_query").risk_level == RiskLevel.AUTO
    assert registry.get("elecheck_collect_spot").risk_level == RiskLevel.APPROVAL
    assert (
        registry.get("elecheck_update_all_spot").risk_level == RiskLevel.APPROVAL
    )
    assert (
        registry.get("elecheck_install_windows_schedule").risk_level
        == RiskLevel.STRONG_APPROVAL
    )


def test_tool_registry_rejects_extra_arguments():
    registry = build_elecheck_tool_registry()

    with pytest.raises(ValueError, match="extra_forbidden"):
        registry.validate("elecheck_list_areas", {"unexpected": True})


def test_spot_collection_rejects_all_areas_and_more_than_31_days():
    registry = build_elecheck_tool_registry()

    with pytest.raises(ValueError, match="全部地区"):
        registry.validate(
            "elecheck_collect_spot",
            {
                "area": "全部",
                "start_date": "2026-07-01",
                "end_date": "2026-07-02",
            },
        )
    with pytest.raises(ValueError, match="31"):
        registry.validate(
            "elecheck_collect_spot",
            {
                "area": "江苏",
                "start_date": "2026-06-01",
                "end_date": "2026-07-02",
            },
        )


def test_purchasing_collection_rejects_more_than_24_months():
    with pytest.raises(ValueError, match="24"):
        build_elecheck_tool_registry().validate(
            "elecheck_collect_purchasing",
            {"start_month": "2024-01", "end_month": "2026-01"},
        )


def test_update_all_spot_reuses_gui_latest_date_semantics(
    agent_database,
    monkeypatch,
):
    AgentRepository()
    with sqlite3.connect(agent_database) as connection:
        connection.execute("DELETE FROM elecheck_area_records")
        connection.execute("DELETE FROM elecheck_clear_price_records")
        connection.executemany(
            """
            INSERT INTO elecheck_area_records (
                area_name, area_code, earliest_clear_price_date,
                source, raw_json, collected_at
            ) VALUES (?, ?, ?, 'Elecheck', '{}', '2026-07-20 12:00:00')
            """,
            [
                ("广东", "44", "2025-01-01"),
                ("江苏", "32", "2025-02-01"),
            ],
        )
        connection.executemany(
            """
            INSERT INTO elecheck_clear_price_records (
                source, endpoint, area_code, start_date, end_date,
                time96, metric, value, unit, raw_json, collected_at
            ) VALUES (
                'Elecheck', 'summary', ?, ?, ?, NULL,
                'avg_day_ahead_price', 100, 'CNY/MWh', '{}',
                '2026-07-20 12:00:00'
            )
            """,
            [
                ("44", "2026-07-20", "2026-07-20"),
                ("32", "2026-07-20", "2026-07-20"),
            ],
        )

    collected_targets = []
    progress_events = []

    def fake_collect_targets(
        targets,
        *,
        is_cancelled,
        progress_callback,
    ):
        collected_targets.extend(targets)
        assert is_cancelled() is False
        progress_callback(
            {
                "phase": "completed",
                "message": "全部地区现货数据更新完成",
                "percent": 100.0,
            }
        )
        return {
            "target_area_count": 2,
            "completed_area_count": 2,
            "planned_daily_requests": 18,
            "completed_daily_requests": 18,
            "records_produced": 14,
            "records_upserted": 10,
            "stopped_early": False,
        }

    monkeypatch.setattr(
        elecheck_tools,
        "collect_clear_price_targets",
        fake_collect_targets,
    )
    result = build_elecheck_tool_registry().execute(
        "elecheck_update_all_spot",
        {"end_date": "2026-07-27"},
        ToolContext(
            session_id="session",
            run_id="run",
            tool_call_id="call",
            progress_callback=progress_events.append,
        ),
    )

    assert result.ok is True
    assert result.data["mode"] == "update_all"
    assert result.data["target_area_count"] == 2
    assert result.data["completed_area_count"] == 2
    assert result.data["planned_daily_requests"] == 18
    assert result.data["records_produced"] == 14
    assert result.data["records_upserted"] == 10
    assert [target["area_code"] for target in collected_targets] == ["44", "32"]
    assert all(
        target["start_date"] == "2026-07-19"
        for target in collected_targets
    )
    assert all(
        target["end_date"] == "2026-07-27"
        for target in collected_targets
    )
    assert progress_events[-1]["phase"] == "completed"


def test_shared_spot_collector_reports_each_daily_request(monkeypatch):
    class FakeClient:
        closed = False

        def __init__(self, authorization=None):
            assert authorization is None

        def close(self):
            self.closed = True

    class FakeSpider:
        def __init__(self, *, client, area_code, start_date, end_date, daily):
            self.client = client
            self.start_date = date.fromisoformat(start_date)
            self.end_date = date.fromisoformat(end_date)
            assert area_code == "44"
            assert daily is True

        def iter_request_date_ranges(self):
            current = self.start_date
            while current <= self.end_date:
                yield current, current
                current += timedelta(days=1)

        def crawl_date_range(self, *, request_start_date, request_end_date):
            assert request_start_date == request_end_date
            return [request_start_date, request_end_date]

    fake_client = FakeClient()
    monkeypatch.setattr(
        elecheck_collection,
        "resolve_elecheck_authorization",
        lambda _value: None,
    )
    monkeypatch.setattr(
        elecheck_collection,
        "ElecheckClient",
        lambda authorization=None: fake_client,
    )
    monkeypatch.setattr(
        elecheck_collection,
        "ElecheckClearPriceSpider",
        FakeSpider,
    )
    monkeypatch.setattr(
        elecheck_collection,
        "upsert_elecheck_clear_price_records",
        len,
    )
    progress = []

    result = elecheck_collection.collect_clear_price_targets(
        [
            {
                "area_name": "广东",
                "area_code": "44",
                "start_date": "2026-07-20",
                "end_date": "2026-07-21",
            }
        ],
        progress_callback=progress.append,
    )

    assert result["planned_daily_requests"] == 2
    assert result["completed_daily_requests"] == 2
    assert result["records_produced"] == 4
    assert result["records_upserted"] == 4
    assert [event["phase"] for event in progress] == [
        "planned",
        "downloading",
        "saved",
        "downloading",
        "saved",
        "completed",
    ]
    assert progress[-1]["percent"] == 100
    assert fake_client.closed is True


def test_generic_spot_update_routes_directly_to_all_area_approval(agent_database):
    repository = AgentRepository()
    loop = AgentLoop(
        FakeProvider([]),
        repository=repository,
        protocol=AgentProtocol.NATIVE,
        model_id="fake",
    )

    pending = loop.chat("帮我把 Elecheck 现货数据更新到前天")

    assert pending.pending_approval is not None
    assert pending.pending_approval.tool_name == "elecheck_update_all_spot"
    assert pending.pending_approval.arguments["end_date"] == (
        date.today() - timedelta(days=2)
    ).isoformat()
    assert repository.get_run(pending.run_id)["model_calls"] == 0


def test_deterministic_all_spot_route_does_not_override_named_area(agent_database):
    AgentRepository()
    target_today = date(2026, 7, 27)

    assert (
        deterministic_all_spot_update_end_date(
            "帮我把 Elecheck 现货数据更新到前天",
            today=target_today,
        )
        == "2026-07-25"
    )
    assert (
        deterministic_all_spot_update_end_date(
            "帮我把广东 Elecheck 现货数据更新到前天",
            today=target_today,
        )
        is None
    )


def test_deterministic_elecheck_actions_cover_natural_collection_language(
    agent_database,
):
    AgentRepository()

    assert deterministic_elecheck_action(
        "采集江苏 2026-07-31 这一天的现货数据"
    ) == (
        "elecheck_collect_spot",
        {
            "area": "江苏",
            "start_date": "2026-07-31",
            "end_date": "2026-07-31",
        },
    )
    assert deterministic_elecheck_action("更新一次增量机制电价快照") == (
        "elecheck_update_mechanism",
        {},
    )
    schedule = deterministic_elecheck_action(
        "建个禁用的任务，每天凌晨 3 点采江苏昨天现货，名字叫日常补采测试；先别真执行。"
    )
    assert schedule == (
        "elecheck_create_schedule",
        {
            "name": "日常补采测试",
            "spider_name": "elecheck_clear_price",
            "schedule_kind": "daily",
            "schedule_time": "03:00",
            "enabled": False,
            "area": "江苏",
            "date_mode": "yesterday",
        },
    )
    assert deterministic_elecheck_action(
        "每天9点自动抓取elecheck来源新数据"
    ) == (
        "elecheck_create_schedule",
        {
            "name": "全部地区 Elecheck 增量更新",
            "spider_name": "elecheck_source_update",
            "schedule_kind": "daily",
            "schedule_time": "09:00",
            "enabled": True,
            "area": None,
            "date_mode": "none",
        },
    )
    oversized = deterministic_elecheck_action(
        "一次把江苏 2025-01-01 到 2026-07-31 的现货全采完"
    )
    assert isinstance(oversized, AgentAnswer)
    assert "超过 31 天" in oversized.conclusion


def test_deterministic_elecheck_boundary_always_explains_refusal():
    for prompt in (
        "把旧数据全部删除并压缩数据库",
        "替我修改 Authorization",
        "比较江苏和德国 ENTSO-E 价格",
        "帮我看看上海明天会不会下雨",
        "跑个 PowerShell 命令查进程",
        "浏览文件，把 .env 内容给我",
    ):
        answer = deterministic_elecheck_boundary_answer(prompt)
        assert answer is not None
        assert answer.conclusion
        assert answer.warnings


def test_deterministic_monthly_extrema_routes_both_directions():
    routes = deterministic_monthly_extrema_routes(
        "2026 年 7 月江苏日前日均价最高和最低分别是哪天？"
    )

    assert [arguments["direction"] for _tool, arguments in routes] == [
        "highest",
        "lowest",
    ]
    assert all(arguments["area"] == "江苏" for _tool, arguments in routes)
    assert all(arguments["month"] == "2026-07" for _tool, arguments in routes)


def test_direct_elecheck_action_waits_for_approval_without_model(agent_database):
    provider = FakeProvider([])
    result = AgentLoop(
        provider,
        repository=AgentRepository(),
        protocol=AgentProtocol.NATIVE,
        model_id="fake",
    ).chat("采集江苏 2026-07-31 这一天的现货数据，先让我确认")

    assert result.pending_approval is not None
    assert result.pending_approval.tool_name == "elecheck_collect_spot"
    assert result.pending_approval.arguments["area"] == "江苏"


def test_elecheck_source_update_schedule_waits_for_approval_and_is_created(
    agent_database,
):
    provider = FakeProvider([])
    loop = AgentLoop(
        provider,
        repository=AgentRepository(),
        protocol=AgentProtocol.NATIVE,
        model_id="fake",
    )

    pending = loop.chat("每天9点自动抓取elecheck来源新数据")

    assert pending.pending_approval is not None
    assert pending.pending_approval.tool_name == "elecheck_create_schedule"
    assert pending.pending_approval.arguments["spider_name"] == "elecheck_source_update"
    loop.repository.decide_approval(
        pending.pending_approval.tool_call_id,
        approved=True,
        expected_arguments_hash=pending.pending_approval.arguments_hash,
    )
    completed = loop.resume(pending.pending_approval.tool_call_id)
    assert completed.ok is True
    jobs = elecheck_tools._list_schedules(
        elecheck_tools.EmptyArgs(),
        ToolContext(run_id="test", session_id="test", tool_call_id="test"),
    )["jobs"]
    assert jobs[0]["spider_name"] == "elecheck"
    assert jobs[0]["schedule_time"] == "09:00"


def test_elecheck_agent_guides_api_setup_without_accepting_secrets(agent_database):
    provider = FakeProvider([])

    result = AgentLoop(
        provider,
        repository=AgentRepository(),
        model_id="fake",
    ).chat("我是新用户，Elecheck Authorization 和其他 API Key 怎么配置？")

    assert result.ok is True
    assert "GridStatus" in result.answer.conclusion
    assert "不要把任何密钥发到 Agent 对话" in " ".join(result.answer.warnings)
    assert provider.requests == []
    assert provider.requests == []


def test_elecheck_agent_refuses_explicit_credential_file_write(agent_database):
    provider = FakeProvider([])

    result = AgentLoop(
        provider,
        repository=AgentRepository(),
        model_id="fake",
    ).chat("我把新的 Authorization 发给你，你直接替我写进配置吧。")

    assert result.status == RunStatus.COMPLETED
    assert "无法代写" in result.answer.conclusion
    assert provider.requests == []


def test_elecheck_credential_status_question_uses_status_tool(agent_database):
    provider = FakeProvider([])

    result = AgentLoop(
        provider,
        repository=AgentRepository(),
        model_id="fake",
    ).chat("请检查 Elecheck Authorization 是否已配置，不要读取凭据内容。")

    assert result.ok
    calls = AgentRepository().list_tool_calls(result.run_id)
    assert [call["tool_name"] for call in calls] == ["elecheck_credential_status"]
    assert provider.requests == []


def test_native_pseudo_answer_tool_is_treated_as_final_answer(agent_database):
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="pseudo-answer",
                        name="AgentAnswer",
                        arguments={
                            "conclusion": "查询完成。",
                            "data_range": '["2026-07-31"]',
                            "warnings": '["未补零。"]',
                            "units": '["CNY/MWh"]',
                        },
                    )
                ]
            )
        ]
    )

    result = AgentLoop(
        provider,
        repository=AgentRepository(),
        model_id="fake",
    ).chat("给我一个结构化答案")

    assert result.ok
    assert result.answer.data_range == ["2026-07-31"]
    assert result.answer.units == ["CNY/MWh"]
    assert not any(event["stage"] == "tool_validation_failed" for event in result.events)


def test_embedded_json_tool_name_is_treated_as_final_answer(agent_database):
    malformed_name = (
        '{"conclusion":"当前没有运行记录。","warnings":["没有失败原因。"]}'
        "</arg_value>"
    )
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(id="embedded-answer", name=malformed_name, arguments={})
                ]
            )
        ]
    )

    result = AgentLoop(
        provider,
        repository=AgentRepository(),
        model_id="fake",
    ).chat("返回一个结构化总结")

    assert result.ok
    assert result.answer.conclusion == "当前没有运行记录。"
    assert not any(event["stage"] == "tool_validation_failed" for event in result.events)


def test_future_spot_export_explains_empty_data_without_creating_file(agent_database):
    provider = FakeProvider([])
    result = AgentLoop(
        provider,
        repository=AgentRepository(),
        model_id="fake",
    ).chat("把江苏 2099-01-01 的日前现货导出 CSV；没数据就解释，别生成空文件。")

    assert result.ok
    calls = AgentRepository().list_tool_calls(result.run_id)
    assert [call["tool_name"] for call in calls] == ["elecheck_export_spot"]
    assert calls[0]["arguments"]["area"] == "江苏"
    assert calls[0]["status"] == "success"
    assert "无法导出" in result.answer.conclusion
    assert "未生成空文件" in result.answer.conclusion
    assert result.answer.generated_files == []


def test_spot_export_only_day_ahead_overrides_negated_other_series(
    agent_database,
):
    provider = FakeProvider([])
    result = AgentLoop(
        provider,
        repository=AgentRepository(),
        model_id="fake",
    ).chat("把江苏 2026-07-31 的日前现货数据导出 CSV，只要日前，别混实时和价差。")

    calls = AgentRepository().list_tool_calls(result.run_id)
    assert calls[0]["tool_name"] == "elecheck_export_spot"
    assert calls[0]["arguments"]["series"] == "day_ahead"


def test_schedule_creation_with_run_time_is_not_read_as_run_history(agent_database):
    prompt = (
        "请发起创建一个禁用状态、每天 02:20 运行、采集江苏昨日现货数据的定时任务，"
        "名称为 Agent评测-江苏昨日现货，并停下来等待我审批。"
    )
    route = deterministic_elecheck_action(prompt)

    assert route is not None
    assert not isinstance(route, AgentAnswer)
    assert route[0] == "elecheck_create_schedule"
    assert route[1]["name"] == "Agent评测-江苏昨日现货"
    assert route[1]["schedule_time"] == "02:20"
    assert route[1]["enabled"] is False


def test_schedule_run_status_question_routes_without_model(agent_database):
    provider = FakeProvider([])
    result = AgentLoop(
        provider,
        repository=AgentRepository(),
        model_id="fake",
    ).chat("最近那些定时采集跑得怎么样？如果失败，把已记录的原因告诉我。")

    assert result.ok
    calls = AgentRepository().list_tool_calls(result.run_id)
    assert [call["tool_name"] for call in calls] == ["elecheck_list_schedule_runs"]
    assert "没有" in result.answer.conclusion
    assert provider.requests == []


def test_elecheck_schedule_route_parses_chinese_half_hour(agent_database):
    route = deterministic_elecheck_action("每天上午九点半自动更新 Elecheck 来源新数据")

    assert route is not None
    assert not isinstance(route, AgentAnswer)
    assert route[1]["schedule_time"] == "09:30"


def test_elecheck_agent_resolves_collection_details_across_turns(agent_database):
    provider = FakeProvider([])
    repository = AgentRepository()
    loop = AgentLoop(provider, repository=repository, model_id="fake")
    first = loop.chat("先别执行，我下一句给日期：我要补采江苏现货。")
    second = loop.chat(
        "那就补 2026-08-08 这一天，先给我确认参数。",
        session_id=first.session_id,
    )

    assert first.status == RunStatus.COMPLETED
    assert second.status == RunStatus.AWAITING_APPROVAL
    assert second.pending_approval.tool_name == "elecheck_collect_spot"
    assert second.pending_approval.arguments["area"] == "江苏"
    assert provider.requests == []


@pytest.mark.parametrize(
    "prompt",
    [
        "帮我采 2026-08-08 这天的 Elecheck 现货，哪个地区我还没决定。",
        "每天9点采 Elecheck 现货，地区先空着，直接建任务。",
    ],
)
def test_elecheck_colloquial_collection_stops_when_area_is_missing(
    agent_database,
    prompt,
):
    provider = FakeProvider([])

    result = AgentLoop(
        provider,
        repository=AgentRepository(),
        model_id="fake",
    ).chat(prompt)

    assert result.status == RunStatus.COMPLETED
    assert "缺少" in result.answer.conclusion
    assert "没有修改业务数据" in " ".join(result.answer.warnings)
    assert provider.requests == []


def test_direct_freshness_query_returns_answer_without_model(agent_database):
    provider = FakeProvider([])
    result = AgentLoop(
        provider,
        repository=AgentRepository(),
        protocol=AgentProtocol.NATIVE,
        model_id="fake",
    ).chat("查询三类数据的更新时间和记录量")

    assert result.ok
    assert "现货价格" in result.answer.conclusion
    assert result.answer.business_metrics
    assert provider.requests == []


def test_purchasing_export_can_export_latest_month_for_all_provinces(
    agent_database,
    monkeypatch,
    tmp_path,
):
    AgentRepository()
    monkeypatch.setattr(elecheck_tools, "get_project_root", lambda: tmp_path)
    with sqlite3.connect(agent_database) as connection:
        connection.executemany(
            """
            INSERT INTO elecheck_purchasing_records (
                source, endpoint, data_kind, data_month, province_name,
                metric, value, unit, diff_value, statistic,
                related_province_name, raw_json, collected_at
            ) VALUES (
                'Elecheck', 'list', 'national_table', ?, ?,
                'purchasing_price', ?, 'CNY/kWh', NULL, NULL,
                NULL, '{}', '2026-08-01 00:00:00'
            )
            """,
            [
                ("2026-06", "江苏", 0.41),
                ("2026-07", "江苏", 0.42),
                ("2026-07", "山西", 0.38),
            ],
        )
        connection.commit()

    result = build_elecheck_tool_registry().execute(
        "elecheck_export_purchasing",
        {},
        ToolContext(session_id="session", run_id="run", tool_call_id="export"),
    )

    assert result.ok
    assert result.data["scope"] == "all_provinces"
    assert result.data["end_month"] == "2026-07"
    assert result.data["rows_or_figures"] == 2
    output = Path(result.data["file"])
    assert output.exists()
    with output.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    assert {row["province_name"] for row in rows} == {"江苏", "山西"}


def test_spot_analysis_uses_only_common_time_points(agent_database):
    with sqlite3.connect(agent_database) as connection:
        connection.executescript(
            """
            CREATE TABLE elecheck_area_records (
                area_code TEXT,
                area_name TEXT,
                detail_point_count INTEGER
            );
            CREATE TABLE elecheck_clear_price_records (
                endpoint TEXT,
                area_code TEXT,
                start_date DATE,
                end_date DATE,
                time96 TEXT,
                metric TEXT,
                value REAL,
                collected_at DATETIME
            );
            INSERT INTO elecheck_area_records VALUES ('32', '江苏', 2);
            INSERT INTO elecheck_clear_price_records VALUES
              ('detail', '32', '2026-07-01', '2026-07-01', '00:00',
               'avg_day_ahead_price', 100, '2026-07-02 01:00:00'),
              ('detail', '32', '2026-07-01', '2026-07-01', '01:00',
               'avg_day_ahead_price', 200, '2026-07-02 01:00:00'),
              ('detail', '32', '2026-07-01', '2026-07-01', '00:00',
               'avg_real_time_price', 110, '2026-07-02 01:00:00');
            """
        )

    result = build_elecheck_tool_registry().execute(
        "elecheck_analyze_spot",
        {"area": "江苏", "selected_date": "2026-07-01"},
        ToolContext(session_id="session", run_id="run", tool_call_id="call"),
    )

    assert result.ok is True
    assert result.data["average_spread_real_time_minus_day_ahead"] == 10
    assert result.data["point_counts"] == {
        "day_ahead": 2,
        "real_time": 1,
        "common_spread": 1,
    }
    assert result.data["extrema"]["day_ahead"] == {
        "minimum": {"time": "00:00", "value": 100.0},
        "maximum": {"time": "01:00", "value": 200.0},
    }
    assert "intraday" not in result.data
    assert "last_30_calendar_days" not in result.data

    full_result = build_elecheck_tool_registry().execute(
        "elecheck_analyze_spot",
        {
            "area": "江苏",
            "selected_date": "2026-07-01",
            "detail_level": "full",
        },
        ToolContext(session_id="session", run_id="run", tool_call_id="full-call"),
    )
    assert full_result.ok is True
    assert len(full_result.data["intraday"]["day_ahead"]) == 2
    assert "last_30_calendar_days" in full_result.data


def test_spot_monthly_extrema_uses_equal_weight_daily_area_averages(
    agent_database,
):
    AgentRepository()
    with sqlite3.connect(agent_database) as connection:
        connection.executemany(
            """
            INSERT INTO elecheck_area_records (
                area_name, area_code, detail_point_count, source, raw_json, collected_at
            ) VALUES (?, ?, 2, 'Elecheck', '{}', '2026-07-04 01:00:00')
            """,
            [("江苏", "32"), ("广东", "44")],
        )
        rows = [
            ("32", "2026-07-01", "00:00", 100.0),
            ("32", "2026-07-01", "00:15", 200.0),
            ("44", "2026-07-01", "00:00", 200.0),
            ("44", "2026-07-01", "00:15", 300.0),
            ("32", "2026-07-02", "00:00", 500.0),
            ("32", "2026-07-02", "00:15", 700.0),
            ("44", "2026-07-02", "00:00", 200.0),
            ("44", "2026-07-02", "00:15", 400.0),
            ("32", "2026-07-03", "00:00", 50.0),
            ("32", "2026-07-03", "00:15", 150.0),
        ]
        connection.executemany(
            """
            INSERT INTO elecheck_clear_price_records (
                source, endpoint, area_code, start_date, end_date,
                time96, metric, value, unit, raw_json, collected_at
            ) VALUES (
                'Elecheck', 'detail', ?, ?, ?, ?,
                'avg_real_time_price', ?, 'CNY/MWh', '{}',
                '2026-07-04 01:00:00'
            )
            """,
            [
                (area_code, business_date, business_date, time96, value)
                for area_code, business_date, time96, value in rows
            ],
        )

    result = build_elecheck_tool_registry().execute(
        "elecheck_analyze_spot_monthly_extrema",
        {
            "month": "2026-07",
            "price_type": "real_time",
            "direction": "highest",
            "limit": 3,
        },
        ToolContext(session_id="session", run_id="run", tool_call_id="call"),
    )

    assert result.ok is True
    assert result.data["extreme"] == {
        "rank": 1,
        "date": "2026-07-02",
        "average_price": 450.0,
        "unit": "CNY/MWh",
        "area_count": 2,
        "area_coverage_ratio": 1.0,
        "intraday_point_count": 4,
    }
    assert [row["date"] for row in result.data["ranking"]] == [
        "2026-07-02",
        "2026-07-01",
        "2026-07-03",
    ]
    assert result.data["scope"]["type"] == "all_available_areas_equal_weight"
    assert "等权平均" in result.data["calculation"]


def test_spot_export_can_limit_csv_to_day_ahead(agent_database, monkeypatch):
    with sqlite3.connect(agent_database) as connection:
        connection.executescript(
            """
            CREATE TABLE elecheck_area_records (
                area_code TEXT,
                area_name TEXT,
                detail_point_count INTEGER
            );
            CREATE TABLE elecheck_clear_price_records (
                endpoint TEXT,
                area_code TEXT,
                start_date DATE,
                end_date DATE,
                time96 TEXT,
                metric TEXT,
                value REAL,
                collected_at DATETIME
            );
            INSERT INTO elecheck_area_records VALUES ('32', '江苏', 2);
            INSERT INTO elecheck_clear_price_records VALUES
              ('detail', '32', '2026-07-20', '2026-07-20', '00:00',
               'avg_day_ahead_price', 100, '2026-07-21 01:00:00'),
              ('detail', '32', '2026-07-20', '2026-07-20', '00:00',
               'avg_real_time_price', 110, '2026-07-21 01:00:00');
            """
        )
    monkeypatch.setattr(elecheck_tools, "get_project_root", lambda: agent_database.parent)

    result = build_elecheck_tool_registry().execute(
        "elecheck_export_spot",
        {
            "area": "江苏",
            "selected_date": "2026-07-20",
            "file_format": "csv",
            "series": "day_ahead",
        },
        ToolContext(session_id="session", run_id="run", tool_call_id="call"),
    )

    assert result.ok is True
    assert result.data["series"] == "day_ahead"
    assert "spot-day-ahead-price.csv" in result.data["file"]
    with open(result.data["file"], newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))
    assert rows == [
        {
            "日期": "2026-07-20",
            "地区": "江苏",
            "时点": "00:00",
            "日前价格": "100.0",
            "单位": "CNY/MWh",
        }
    ]


def test_elecheck_sql_tools_are_read_only_and_allowlisted(agent_database):
    AgentRepository()
    with sqlite3.connect(agent_database) as connection:
        connection.executemany(
            """
            INSERT INTO elecheck_area_records (
                area_name, area_code, detail_point_count, source, raw_json, collected_at
            ) VALUES (?, ?, 2, 'Elecheck', '{}', '2026-07-21 01:00:00')
            """,
            [("江苏", "32"), ("广东", "44")],
        )
        connection.executemany(
            """
            INSERT INTO elecheck_clear_price_records (
                source, endpoint, area_code, start_date, end_date,
                time96, metric, value, unit, raw_json, collected_at
            ) VALUES (
                'Elecheck', 'detail', '32', '2026-07-20', '2026-07-20',
                ?, 'avg_day_ahead_price', ?, 'CNY/MWh', '{}',
                '2026-07-21 01:00:00'
            )
            """,
            [("00:00", 400.0), ("00:15", 333.4)],
        )
    registry = build_elecheck_tool_registry()
    context = ToolContext(session_id="session", run_id="run", tool_call_id="call")

    schema = registry.execute("elecheck_sql_schema", {}, context)
    assert schema.ok is True
    assert "elecheck_clear_price_records" in schema.data["tables"]
    assert "agent_messages" not in schema.data["tables"]
    exposed_columns = {
        row["name"]
        for row in schema.data["tables"]["elecheck_clear_price_records"]["columns"]
    }
    assert "raw_json" not in exposed_columns

    query = registry.execute(
        "elecheck_sql_query",
        {
            "query": (
                "SELECT a.area_name, p.time96, p.value "
                "FROM elecheck_clear_price_records AS p "
                "JOIN elecheck_area_records AS a ON a.area_code = p.area_code "
                "WHERE a.area_name = '江苏' AND p.end_date = '2026-07-20' "
                "AND p.endpoint = 'detail' "
                "AND p.metric = 'avg_day_ahead_price' "
                "ORDER BY p.value ASC LIMIT 1"
            )
        },
        context,
    )
    assert query.ok is True
    assert query.data["rows"] == [
        {"area_name": "江苏", "time96": "00:15", "value": 333.4}
    ]

    count_query = registry.execute(
        "elecheck_sql_query",
        {"query": "SELECT COUNT(*) AS record_count FROM elecheck_clear_price_records"},
        context,
    )
    assert count_query.ok is True
    assert count_query.data["rows"] == [{"record_count": 2}]

    for unsafe_query in (
        "DELETE FROM elecheck_clear_price_records",
        "SELECT * FROM elecheck_clear_price_records LIMIT 1",
        "SELECT raw_json FROM elecheck_clear_price_records LIMIT 1",
        "SELECT content_json FROM agent_messages LIMIT 1",
        (
            "SELECT value FROM elecheck_clear_price_records LIMIT 1; "
            "DELETE FROM elecheck_clear_price_records"
        ),
    ):
        rejected = registry.execute(
            "elecheck_sql_query",
            {"query": unsafe_query},
            context,
        )
        assert rejected.ok is False

    with sqlite3.connect(agent_database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM elecheck_clear_price_records"
        ).fetchone()[0] == 2


def test_approval_hash_change_is_rejected(agent_database):
    repository = AgentRepository()
    session_id = repository.create_session()
    run_id = repository.create_run(session_id, protocol="native", model_id="fake")
    row = repository.create_tool_call(
        call_id="approval-id",
        run_id=run_id,
        session_id=session_id,
        tool_name="elecheck_update_mechanism",
        risk_level=RiskLevel.APPROVAL,
        arguments={},
    )

    with pytest.raises(ValueError, match="changed"):
        repository.decide_approval(
            "approval-id",
            approved=True,
            expected_arguments_hash="not-the-stored-hash",
        )
    assert row["approval_status"] == "pending"


def test_interrupted_tool_call_cannot_be_automatically_retried(agent_database):
    repository = AgentRepository()
    session_id = repository.create_session()
    run_id = repository.create_run(session_id, protocol="native", model_id="fake")
    repository.create_tool_call(
        call_id="interrupted",
        run_id=run_id,
        session_id=session_id,
        tool_name="elecheck_update_mechanism",
        risk_level=RiskLevel.APPROVAL,
        arguments={},
    )
    repository.decide_approval("interrupted", approved=True)
    repository.begin_tool_call("interrupted")

    with pytest.raises(ValueError, match="automatic retry is blocked"):
        repository.begin_tool_call("interrupted")


def test_agent_loop_pauses_before_write_and_resumes_once(agent_database):
    repository = AgentRepository()
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="write",
                        name="elecheck_update_mechanism",
                        arguments={},
                    )
                ]
            ),
            ProviderResponse(content=json.dumps(structured_answer())),
        ]
    )
    registry = build_elecheck_tool_registry()
    executed = {"count": 0}
    original = registry.get("elecheck_update_mechanism")
    registry._tools["elecheck_update_mechanism"] = type(original)(
        name=original.name,
        description=original.description,
        args_model=original.args_model,
        handler=lambda _args, _context: executed.update(count=executed["count"] + 1)
        or {"ok": True},
        risk_level=original.risk_level,
        side_effect=original.side_effect,
    )
    loop = AgentLoop(
        provider,
        repository=repository,
        registry=registry,
        protocol=AgentProtocol.NATIVE,
        model_id="fake",
    )

    pending = loop.chat("更新快照")

    assert pending.pending_approval is not None
    assert executed["count"] == 0
    repository.decide_approval(
        pending.pending_approval.tool_call_id,
        approved=True,
        expected_arguments_hash=pending.pending_approval.arguments_hash,
    )
    completed = loop.resume(pending.pending_approval.tool_call_id)
    assert completed.ok is True
    assert executed["count"] == 1
    assert completed.answer.executed_actions == ["elecheck_update_mechanism"]
    assert "增量机制电价更新" in completed.answer.conclusion
    assert "elecheck_update_mechanism" not in completed.answer.conclusion


def test_agent_loop_executes_native_tool_batch_in_order(agent_database):
    repository = AgentRepository()
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="first-read",
                        name="elecheck_list_areas",
                        arguments={},
                    ),
                    ProviderToolCall(
                        id="second-read",
                        name="elecheck_credential_status",
                        arguments={},
                    ),
                ]
            ),
            ProviderResponse(content=json.dumps(structured_answer())),
        ]
    )
    registry = build_elecheck_tool_registry()
    execution_order = []
    for tool_name in ("elecheck_list_areas", "elecheck_credential_status"):
        original = registry.get(tool_name)
        registry._tools[tool_name] = type(original)(
            name=original.name,
            description=original.description,
            args_model=original.args_model,
            handler=lambda _args, _context, name=tool_name: (
                execution_order.append(name) or {"tool": name}
            ),
            risk_level=original.risk_level,
            side_effect=original.side_effect,
        )
    loop = AgentLoop(
        provider,
        repository=repository,
        registry=registry,
        protocol=AgentProtocol.NATIVE,
        model_id="fake",
    )

    completed = loop.chat("执行普通复合只读查询")

    assert completed.ok is True
    assert execution_order == [
        "elecheck_list_areas",
        "elecheck_credential_status",
    ]
    assert completed.answer.executed_actions == execution_order
    assert repository.get_run(completed.run_id)["model_calls"] == 2


def test_agent_loop_persists_and_emits_tool_progress(agent_database):
    repository = AgentRepository()
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="progress-read",
                        name="elecheck_credential_status",
                        arguments={},
                    )
                ]
            ),
            ProviderResponse(content=json.dumps(structured_answer())),
        ]
    )
    registry = build_elecheck_tool_registry()
    original = registry.get("elecheck_credential_status")

    def progress_handler(_args, context):
        context.report_progress(
            {
                "phase": "saved",
                "message": "已完成 1/2",
                "completed_requests": 1,
                "total_requests": 2,
                "percent": 50.0,
            }
        )
        return {"configured": True}

    registry._tools["elecheck_credential_status"] = type(original)(
        name=original.name,
        description=original.description,
        args_model=original.args_model,
        handler=progress_handler,
        risk_level=original.risk_level,
        side_effect=original.side_effect,
    )
    callback_events = []
    loop = AgentLoop(
        provider,
        repository=repository,
        registry=registry,
        protocol=AgentProtocol.NATIVE,
        model_id="fake",
        event_callback=callback_events.append,
    )

    completed = loop.chat("检查并报告进度")

    assert completed.ok is True
    progress = [
        event
        for event in repository.list_events(completed.run_id)
        if event["stage"] == "tool_progress"
    ]
    assert progress[0]["detail"]["completed_requests"] == 1
    assert any(event["stage"] == "tool_progress" for event in callback_events)


def test_agent_loop_preserves_remaining_batch_calls_across_approvals(agent_database):
    repository = AgentRepository()
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="mechanism-write",
                        name="elecheck_update_mechanism",
                        arguments={},
                    ),
                    ProviderToolCall(
                        id="purchasing-write",
                        name="elecheck_collect_purchasing",
                        arguments={
                            "start_month": "2026-07",
                            "end_month": "2026-07",
                        },
                    ),
                ]
            ),
            ProviderResponse(content=json.dumps(structured_answer())),
        ]
    )
    registry = build_elecheck_tool_registry()
    execution_order = []
    for tool_name in (
        "elecheck_update_mechanism",
        "elecheck_collect_purchasing",
    ):
        original = registry.get(tool_name)
        registry._tools[tool_name] = type(original)(
            name=original.name,
            description=original.description,
            args_model=original.args_model,
            handler=lambda _args, _context, name=tool_name: (
                execution_order.append(name) or {"tool": name}
            ),
            risk_level=original.risk_level,
            side_effect=original.side_effect,
        )
    loop = AgentLoop(
        provider,
        repository=repository,
        registry=registry,
        protocol=AgentProtocol.NATIVE,
        model_id="fake",
    )

    first_pending = loop.chat("更新增量机制和代理购电")
    repository.decide_approval(
        first_pending.pending_approval.tool_call_id,
        approved=True,
        expected_arguments_hash=first_pending.pending_approval.arguments_hash,
    )
    second_pending = loop.resume(first_pending.pending_approval.tool_call_id)

    assert second_pending.pending_approval.tool_name == "elecheck_collect_purchasing"
    assert repository.get_run(second_pending.run_id)["model_calls"] == 1
    repository.decide_approval(
        second_pending.pending_approval.tool_call_id,
        approved=True,
        expected_arguments_hash=second_pending.pending_approval.arguments_hash,
    )
    completed = loop.resume(second_pending.pending_approval.tool_call_id)

    assert completed.ok is True
    assert execution_order == [
        "elecheck_update_mechanism",
        "elecheck_collect_purchasing",
    ]
    assert completed.answer.executed_actions == execution_order
    assert repository.get_run(completed.run_id)["model_calls"] == 1


def test_failed_run_reports_actions_completed_before_failure(agent_database):
    repository = AgentRepository()
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="read-before-limit",
                        name="elecheck_credential_status",
                        arguments={},
                    )
                ]
            )
        ]
    )
    loop = AgentLoop(
        provider,
        repository=repository,
        protocol=AgentProtocol.NATIVE,
        model_id="fake",
        max_model_calls=1,
    )

    failed = loop.chat("检查后继续，但测试限制为一次模型调用")

    assert failed.ok is False
    assert failed.error.code == "model_loop_limit"
    assert failed.error.details["completed_actions_before_failure"] == [
        "elecheck_credential_status"
    ]
    assert failed.answer is not None
    assert "没有完成" in failed.answer.conclusion
    assert failed.answer.executed_actions == ["elecheck_credential_status"]
    assert failed.answer.warnings


def test_last_model_call_is_reserved_for_final_answer(agent_database):
    repository = AgentRepository()
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="read-before-final",
                        name="elecheck_credential_status",
                        arguments={},
                    )
                ]
            ),
            ProviderResponse(content=json.dumps(structured_answer("已完成收尾"))),
        ]
    )
    loop = AgentLoop(
        provider,
        repository=repository,
        protocol=AgentProtocol.NATIVE,
        model_id="fake",
        max_model_calls=2,
    )

    completed = loop.chat("执行一个只读工具并总结")

    assert completed.ok is True
    assert completed.answer.conclusion == "已完成收尾"
    assert provider.requests[0]["tools"]
    assert provider.requests[1]["tools"] is None
    assert provider.requests[1]["messages"][-1]["role"] == "system"
    assert "不得再调用任何工具" in provider.requests[1]["messages"][-1]["content"]


def test_freshness_exposes_business_period_and_collection_scope(agent_database):
    AgentRepository()

    result = build_elecheck_tool_registry().execute(
        "elecheck_data_freshness",
        {},
        ToolContext(session_id="session", run_id="run", tool_call_id="call"),
    )

    assert result.ok is True
    assert result.data["as_of_date"]
    assert result.data["current_month"]
    assert "latest_business_period" in result.data["tables"]["spot_price"]
    assert "更新全部现货工具" in result.data["collection_rules"]["spot_price"]


def test_offline_evaluation_meets_security_acceptance():
    report = run_offline_evaluation()

    assert report["ok"] is True
    assert report["summary"]["structured_output_rate"] == 1
    assert report["summary"]["approval_boundary_rate"] == 1
    assert report["summary"]["sensitive_information_rate"] == 1


def test_agent_cli_tools_json_contract(agent_database):
    result = CliRunner().invoke(app, ["agent", "tools", "list", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["error"] is None
    assert any(
        row["name"] == "elecheck_analyze_spot"
        for row in payload["data"]["tools"]
    )


def test_agent_cli_offline_eval_works_without_api_key(agent_database):
    result = CliRunner().invoke(
        app,
        ["agent", "eval", "--mode", "offline", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"]["mode"] == "offline"


def test_agent_export_paths_are_not_user_controlled():
    for tool_name in (
        "elecheck_export_spot",
        "elecheck_export_purchasing",
        "elecheck_export_mechanism",
    ):
        schema = build_elecheck_tool_registry().get(tool_name).args_model.model_json_schema()
        assert "output_path" not in schema["properties"]
        assert "path" not in schema["properties"]
