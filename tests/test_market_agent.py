import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from powertrade_crawler.cli import app
from powertrade_crawler.config import get_settings
from powertrade_crawler.market_agent.data_tools import (
    CompareSeriesArgs,
    ExportResultArgs,
    GetNewsArticleArgs,
    QuerySeriesArgs,
    SearchNewsArgs,
    SeriesSpec,
    build_market_tool_registry,
    compare_series,
    export_result,
    get_gzpec_article,
    list_datasets,
    ListDatasetsArgs,
    query_series,
    search_gzpec_news,
)
from powertrade_crawler.market_agent.evaluation import run_offline_evaluation
from powertrade_crawler.market_agent.gui import MarketAgentApp
from powertrade_crawler.market_agent.loop import MarketAgentLoop, candidate_tool_names
from powertrade_crawler.market_agent.provider import (
    FakeProvider,
    LLMProvider,
    ProviderProtocolError,
    ProviderTimeoutError,
)
from powertrade_crawler.market_agent.repository import (
    MAX_CONTEXT_CHARS,
    MAX_CONTEXT_MESSAGES,
    MarketAgentRepository,
)
from powertrade_crawler.market_agent.schemas import (
    AgentProtocol,
    MarketAgentAnswer,
    ProviderResponse,
    ProviderToolCall,
    RiskLevel,
    RunStatus,
)
from powertrade_crawler.market_agent.tools import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
)
from powertrade_crawler.models import (
    ElecheckClearPriceRecord,
    ElecheckMechanismElectricityPriceRecord,
    ElecheckPurchasingRecord,
    ElexonRecord,
    EntsoeRecord,
    GridStatusDatasetMetadataRecord,
    GridStatusRecord,
    GzpecNewsRecord,
    MarketRecord,
    NewsContentBlock,
)
from powertrade_crawler.storage import (
    upsert_elexon_records,
    upsert_entsoe_records,
    upsert_elecheck_clear_price_records,
    upsert_elecheck_mechanism_electricity_price_records,
    upsert_elecheck_purchasing_records,
    upsert_gridstatus_dataset_metadata_records,
    upsert_gridstatus_records,
    upsert_gzpec_news_records,
    upsert_records,
)


@pytest.fixture
def market_database(monkeypatch, tmp_path):
    db_path = tmp_path / "market-agent.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    MarketAgentRepository()
    yield db_path
    get_settings.cache_clear()


@pytest.fixture
def seeded_market_database(market_database):
    collected_at = datetime(2026, 7, 20, 8, 0, tzinfo=UTC).replace(tzinfo=None)
    upsert_elecheck_clear_price_records(
        [
            ElecheckClearPriceRecord(
                endpoint="detail",
                area_code="140000000000",
                start_date=date(2026, 7, 1),
                end_date=date(2026, 7, 1),
                time96="00:00",
                metric="avg_day_ahead_price",
                value=100,
                unit="CNY/MWh",
                currency="CNY",
                collected_at=collected_at,
            ),
            ElecheckClearPriceRecord(
                endpoint="detail",
                area_code="140000000000",
                start_date=date(2026, 7, 1),
                end_date=date(2026, 7, 1),
                time96="01:00",
                metric="avg_day_ahead_price",
                value=200,
                unit="CNY/MWh",
                currency="CNY",
                collected_at=collected_at,
            ),
            ElecheckClearPriceRecord(
                endpoint="detail",
                area_code="140000000000",
                start_date=date(2026, 7, 1),
                end_date=date(2026, 7, 1),
                time96="00:00",
                metric="avg_real_time_price",
                value=120,
                unit="CNY/MWh",
                currency="CNY",
                collected_at=collected_at,
            ),
            ElecheckClearPriceRecord(
                endpoint="detail",
                area_code="140000000000",
                start_date=date(2026, 7, 1),
                end_date=date(2026, 7, 1),
                time96="01:00",
                metric="avg_real_time_price",
                value=180,
                unit="CNY/MWh",
                currency="CNY",
                collected_at=collected_at,
            ),
            ElecheckClearPriceRecord(
                endpoint="detail",
                area_code="140000000000",
                start_date=date(2026, 7, 3),
                end_date=date(2026, 7, 3),
                time96="00:00",
                metric="avg_day_ahead_price",
                value=300,
                unit="CNY/MWh",
                currency="CNY",
                collected_at=collected_at,
            ),
        ]
    )
    upsert_elecheck_purchasing_records(
        [
            ElecheckPurchasingRecord(
                endpoint="list",
                data_kind="national_table",
                data_month="2026-06",
                province_name="山西",
                metric="purchasing_price",
                value=0.45,
                collected_at=collected_at,
            ),
            ElecheckPurchasingRecord(
                endpoint="list",
                data_kind="national_table",
                data_month="2026-06",
                province_name="山东",
                metric="purchasing_price",
                value=0.55,
                collected_at=collected_at,
            ),
        ]
    )
    upsert_elecheck_mechanism_electricity_price_records(
        [
            ElecheckMechanismElectricityPriceRecord(
                region_name="山西",
                category="风电",
                price=0.32,
                clear_price=0.28,
                collected_at=collected_at,
            )
        ]
    )
    upsert_records(
        [
            MarketRecord(
                source="ENTSO-E Transparency Platform",
                market="day_ahead",
                region="DE-LU",
                trade_date=date(2026, 7, 1),
                metric="price_position_001",
                value=999,
                unit="EUR/MWh",
                currency="EUR",
                collected_at=collected_at,
            ),
            MarketRecord(
                source="ENTSO-E Transparency Platform",
                market="day_ahead",
                region="DE-LU",
                trade_date=date(2026, 7, 1),
                metric="price_position_002",
                value=120,
                unit="EUR/MWh",
                currency="EUR",
                collected_at=collected_at,
            ),
        ]
    )
    upsert_entsoe_records(
        [
            EntsoeRecord(
                dataset="entsoe_day_ahead_prices",
                category="market",
                title_en="Day-ahead Energy Prices",
                title_zh="日前电价",
                row_key="modern-price-1",
                area="DE-LU",
                interval_start_utc="2026-07-01T00:00:00Z",
                interval_end_utc="2026-07-01T01:00:00Z",
                position=1,
                value=100,
                value_field="price.amount",
                unit="EUR/MWh",
                currency="EUR",
                collected_at=collected_at,
            )
        ]
    )
    upsert_elexon_records(
        [
            ElexonRecord(
                dataset="elexon_system_prices",
                category="market",
                title_en="System Prices",
                title_zh="结算系统价格",
                endpoint="/balancing/settlement/system-prices/{settlement_date}",
                row_key="elexon-price-1",
                settlement_date="2026-07-01",
                settlement_period=1,
                metric="system_sell_price",
                value=80,
                value_field="systemSellPrice",
                unit="GBP/MWh",
                currency="GBP",
                collected_at=collected_at,
            ),
            ElexonRecord(
                dataset="elexon_system_prices",
                category="market",
                title_en="System Prices",
                title_zh="结算系统价格",
                endpoint="/balancing/settlement/system-prices/{settlement_date}",
                row_key="elexon-price-2",
                settlement_date="2026-07-01",
                settlement_period=2,
                metric="system_sell_price",
                value=100,
                value_field="systemSellPrice",
                unit="GBP/MWh",
                currency="GBP",
                collected_at=collected_at,
            ),
        ]
    )
    upsert_gridstatus_dataset_metadata_records(
        [
            GridStatusDatasetMetadataRecord(
                dataset_id="test_load",
                name="Test load",
                status="active",
                is_published=True,
                time_index_column="interval_start_utc",
                all_columns=[
                    {
                        "name": "load",
                        "data_type": "number",
                        "is_numeric": True,
                    }
                ],
                collected_at=collected_at,
            )
        ]
    )
    upsert_gridstatus_records(
        [
            GridStatusRecord(
                request_name="market_agent_gridstatus_test_load_all",
                request_type="dataset_query",
                dataset="test_load",
                row_key="grid-1",
                interval_start_utc="2026-07-01T00:00:00Z",
                raw={"load": 1000},
                collected_at=collected_at,
            ),
            GridStatusRecord(
                request_name="market_agent_gridstatus_test_load_all",
                request_type="dataset_query",
                dataset="test_load",
                row_key="grid-2",
                interval_start_utc="2026-07-01T01:00:00Z",
                raw={"load": 1200},
                collected_at=collected_at,
            ),
        ]
    )
    upsert_gzpec_news_records(
        [
            GzpecNewsRecord(
                source="广州电力交易中心",
                category="市场研究",
                title="广东电力现货市场分析",
                url="http://www.gzpec.cn/test.html",
                publish_date=date(2026, 7, 1),
                index_url="http://www.gzpec.cn/news/scyj/index.html",
                news_type="spot_market",
                content_blocks=[
                    NewsContentBlock(
                        sequence=1,
                        block_type="text",
                        text="现货市场运行平稳。",
                    )
                ],
                collected_at=collected_at,
            )
        ]
    )
    return market_database


def context(session_id="session", run_id="run", call_id="call"):
    return ToolContext(
        session_id=session_id,
        run_id=run_id,
        tool_call_id=call_id,
    )


def final_answer(conclusion="完成"):
    return MarketAgentAnswer(conclusion=conclusion).model_dump(mode="json")


def test_market_agent_tables_are_isolated(market_database):
    with sqlite3.connect(market_database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "market_agent_settings",
        "market_agent_sessions",
        "market_agent_messages",
        "market_agent_runs",
        "market_agent_tool_calls",
        "market_agent_events",
    } <= tables
    assert MarketAgentRepository().get_config()["model_id"] == "smart-auto"


def test_market_agent_run_persists_free_router_route_metadata(market_database):
    repository = MarketAgentRepository()
    session_id = repository.create_session()
    run_id = repository.create_run(
        session_id,
        protocol="native",
        model_id="smart-auto",
    )

    repository.update_run(
        run_id,
        router_provider="free-route",
        upstream_model="upstream/model",
        router_alert_count=2,
    )

    run = repository.get_run(run_id)
    assert run["router_provider"] == "free-route"
    assert run["upstream_model"] == "upstream/model"
    assert run["router_alert_count"] == 2


def test_market_agent_package_does_not_import_original_agent():
    package = Path(__file__).parents[1] / "src" / "powertrade_crawler" / "market_agent"
    for path in package.glob("*.py"):
        assert "powertrade_crawler.agent" not in path.read_text(encoding="utf-8")


def test_registry_has_only_controlled_capabilities():
    registry = build_market_tool_registry()
    assert len(registry.names()) == 15
    assert not {
        "sql",
        "shell",
        "delete_data",
        "set_credential",
        "schedule",
    } & set(registry.names())
    for name in registry.names():
        expected = RiskLevel.APPROVAL if name.startswith("market_collect_") else RiskLevel.AUTO
        assert registry.get(name).risk_level == expected
    assert candidate_tool_names("分析山西日前现货价格", registry) == [
        "market_analyze_elecheck_spot"
    ]
    assert candidate_tool_names("更新 Elexon 英国数据", registry) == [
        "market_collect_elexon"
    ]
    assert candidate_tool_names("查询 ENTSO-E 德国日前价格", registry) == [
        "market_query_series"
    ]
    assert candidate_tool_names("查广州电力交易中心现货市场公开信息", registry) == [
        "market_search_gzpec_news",
        "market_get_gzpec_article",
    ]


def test_elecheck_query_uses_database_values_and_keeps_missing_day(
    seeded_market_database,
):
    payload = query_series(
        QuerySeriesArgs(
            source="elecheck",
            dataset="elecheck_spot",
            metric="avg_day_ahead_price",
            area="山西",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 3),
            aggregation="daily",
        ),
        context(),
    )
    points = payload["series"]["points"]
    assert points == [
        {"period": "2026-07-01", "value": 150.0, "group": None},
        {"period": "2026-07-03", "value": 300.0, "group": None},
    ]
    assert all(point["period"] != "2026-07-02" for point in points)
    assert payload["completeness"] == [
        {
            "name": "elecheck_spot 结果覆盖",
            "actual": 2,
            "expected": 3,
            "ratio": pytest.approx(2 / 3),
            "description": "按查询业务日期的自然日数量计算；缺失日期不补零。",
        }
    ]


def test_entsoe_modern_record_wins_over_legacy_position(seeded_market_database):
    payload = query_series(
        QuerySeriesArgs(
            source="entsoe",
            dataset="entsoe_day_ahead_prices",
            area="DE-LU",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 1),
            aggregation="daily",
        ),
        context(),
    )
    assert payload["series"]["points"][0]["value"] == pytest.approx(110)
    assert "market_records" in " ".join(payload["warnings"])


def test_elexon_and_gridstatus_query_dynamic_metrics(seeded_market_database):
    elexon = query_series(
        QuerySeriesArgs(
            source="elexon",
            dataset="elexon_system_prices",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 1),
        ),
        context(),
    )
    assert elexon["series"]["points"][0]["value"] == pytest.approx(90)
    gridstatus = query_series(
        QuerySeriesArgs(
            source="gridstatus",
            dataset="test_load",
            metric="load",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 1),
        ),
        context(),
    )
    assert gridstatus["series"]["points"][0]["value"] == pytest.approx(1100)
    assert gridstatus["completeness"][0]["expected"] is None
    catalog = list_datasets(
        ListDatasetsArgs(source="gridstatus", search="test_load"),
        context(),
    )
    assert catalog["datasets"][0]["local_record_count"] == 2
    assert catalog["datasets"][0]["local_earliest"] == "2026-07-01"
    entsoe_catalog = list_datasets(
        ListDatasetsArgs(source="entsoe"),
        context(),
    )
    assert len(entsoe_catalog["datasets"]) == 4
    assert all(item["source"] == "entsoe" for item in entsoe_catalog["datasets"])
    elexon_catalog = list_datasets(
        ListDatasetsArgs(source="elexon"),
        context(),
    )
    assert len(elexon_catalog["datasets"]) == 5
    assert elexon_catalog["catalog_summaries"] == [
        {
            "source": "elexon",
            "display_name": "Elexon Insights API",
            "returned_count": 5,
            "agent_supported_count": 5,
            "project_catalog_count": 17,
            "scope_note": (
                "多数据源 Agent 当前支持 5 个 Elexon 数据集；"
                "项目完整 Elexon 接口目录共 17 项。"
            ),
            "lookup_hint": (
                "可在“Elexon 英国”页签查看完整接口目录，或运行 "
                "powertrade elexon-datasets。"
            ),
        }
    ]
    with pytest.raises(ValueError, match="numeric column"):
        query_series(
            QuerySeriesArgs(
                source="gridstatus",
                dataset="test_load",
                metric="unknown",
            ),
            context(),
        )


def test_gzpec_search_and_article_read_local_content(seeded_market_database):
    search = search_gzpec_news(
        SearchNewsArgs(keyword="现货", news_type="spot_market"),
        context(),
    )
    assert search["records"][0]["title"] == "广东电力现货市场分析"
    article = get_gzpec_article(
        GetNewsArticleArgs(url=search["records"][0]["url"]),
        context(),
    )
    assert article["article"]["content_blocks"][0]["text"] == "现货市场运行平稳。"


def test_strict_comparison_blocks_incompatible_market_prices(
    seeded_market_database,
):
    payload = compare_series(
        CompareSeriesArgs(
            series=[
                SeriesSpec(
                    source="entsoe",
                    dataset="entsoe_day_ahead_prices",
                    area="DE-LU",
                    start_date=date(2026, 7, 1),
                    end_date=date(2026, 7, 1),
                ),
                SeriesSpec(
                    source="elexon",
                    dataset="elexon_system_prices",
                    start_date=date(2026, 7, 1),
                    end_date=date(2026, 7, 1),
                ),
            ]
        ),
        context(),
    )
    comparison = payload["comparison"]
    assert comparison["status"] == "side_by_side_only"
    assert comparison["differences"] == []
    assert any("currency" in reason for reason in comparison["blocking_reasons"])


def test_compatible_series_comparison_calculates_difference(
    seeded_market_database,
):
    payload = compare_series(
        CompareSeriesArgs(
            series=[
                SeriesSpec(
                    source="elecheck",
                    dataset="elecheck_spot",
                    metric="avg_day_ahead_price",
                    area="山西",
                    start_date=date(2026, 7, 1),
                    end_date=date(2026, 7, 1),
                ),
                SeriesSpec(
                    source="elecheck",
                    dataset="elecheck_spot",
                    metric="avg_real_time_price",
                    area="山西",
                    start_date=date(2026, 7, 1),
                    end_date=date(2026, 7, 1),
                ),
            ]
        ),
        context(),
    )
    comparison = payload["comparison"]
    assert comparison["status"] == "comparable"
    assert comparison["differences"][0]["value"] == pytest.approx(0)


def test_export_is_limited_to_market_agent_session_directory(
    seeded_market_database,
    monkeypatch,
    tmp_path,
):
    from powertrade_crawler.market_agent import data_tools

    monkeypatch.setattr(data_tools, "project_root", lambda: tmp_path)
    payload = export_result(
        ExportResultArgs(
            series=[
                SeriesSpec(
                    source="elecheck",
                    dataset="elecheck_spot",
                    area="山西",
                    start_date=date(2026, 7, 1),
                    end_date=date(2026, 7, 1),
                )
            ],
            formats=["csv"],
        ),
        context(session_id="safe-session", call_id="export-call"),
    )
    path = Path(payload["generated_files"][0])
    assert path.is_file()
    assert path.parent == tmp_path / "exports" / "market_agent" / "safe-session"


def test_repository_trims_only_model_context(market_database):
    repository = MarketAgentRepository()
    session_id = repository.create_session()
    for index in range(MAX_CONTEXT_MESSAGES + 5):
        repository.add_message(
            session_id,
            role="user",
            content={"content": f"{index}-" + ("x" * 2000)},
        )
    assert len(repository.get_session_record(session_id)["messages"]) == 25
    messages = repository.model_messages(session_id)
    assert len(messages) <= MAX_CONTEXT_MESSAGES
    assert sum(len(item["content"]) for item in messages) <= MAX_CONTEXT_CHARS
    assert messages[-1]["content"].startswith("24-")


def test_repository_redacts_secrets_from_messages_events_and_titles(market_database):
    repository = MarketAgentRepository()
    session_id = repository.create_session()
    secret = "secret-value-must-not-persist"
    repository.add_message(
        session_id,
        role="user",
        content={"content": f"Authorization: Bearer {secret}"},
    )
    run_id = repository.create_run(session_id, protocol="native", model_id="fake")
    repository.add_event(
        run_id,
        session_id,
        "test",
        {"api_key": secret, "message": f"Bearer {secret}"},
    )
    raw = b"".join(
        path.read_bytes()
        for path in market_database.parent.glob(f"{market_database.name}*")
    )
    assert secret.encode() not in raw
    assert "***" in repository.get_session_record(session_id)["title"]


def test_deterministic_overview_uses_no_model_call_and_closes_provider(
    seeded_market_database,
):
    provider = FakeProvider([])
    repository = MarketAgentRepository()
    loop = MarketAgentLoop(
        provider,
        repository=repository,
        close_provider_after_run=True,
    )
    result = loop.chat("查看五个来源的数据更新时间")
    assert result.ok
    assert repository.get_run(result.run_id)["model_calls"] == 0
    assert provider.requests == []
    assert provider.closed
    assert len(result.answer.data_sources) == 5


def test_program_replaces_unsupported_model_number(seeded_market_database):
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="query-1",
                        name="market_query_series",
                        arguments={
                            "source": "elecheck",
                            "dataset": "elecheck_spot",
                            "metric": "avg_day_ahead_price",
                            "area": "山西",
                            "start_date": "2026-07-01",
                            "end_date": "2026-07-01",
                        },
                    )
                ]
            ),
            ProviderResponse(
                content=json.dumps(
                    final_answer("均值为 999 CNY/MWh。"),
                    ensure_ascii=False,
                )
            ),
        ]
    )
    result = MarketAgentLoop(
        provider,
        repository=MarketAgentRepository(),
        model_id="fake",
    ).chat("查询山西日前价格")
    assert result.ok
    assert "999" not in result.answer.conclusion
    assert any("未通过校验" in item for item in result.answer.warnings)
    assert result.answer.business_metrics


class ApprovalArgs(QuerySeriesArgs):
    pass


def approval_registry(counter):
    registry = ToolRegistry()

    def handler(_args, _context):
        counter["calls"] += 1
        return {
            "facts": [
                {
                    "fact_id": "approved_written",
                    "label": "实际写入记录数",
                    "value": 3,
                    "unit": "条",
                    "source": "测试来源",
                    "dataset": "test",
                    "time_basis": "测试运行",
                    "calculation": "测试处理器",
                }
            ],
            "data_sources": ["测试来源"],
            "executed_actions": ["market_collect_test"],
        }

    registry.register(
        ToolDefinition(
            name="market_collect_test",
            description="测试审批工具",
            args_model=ApprovalArgs,
            handler=handler,
            risk_level=RiskLevel.APPROVAL,
            side_effect="写入测试业务表。",
        )
    )
    return registry


def approval_arguments():
    return {
        "source": "elecheck",
        "dataset": "elecheck_spot",
        "area": "山西",
        "start_date": "2026-07-01",
        "end_date": "2026-07-01",
    }


def test_json_approval_resume_preserves_protocol_and_executes_once(market_database):
    counter = {"calls": 0}
    repository = MarketAgentRepository()
    registry = approval_registry(counter)
    first = FakeProvider(
        [
            ProviderResponse(
                content=json.dumps(
                    {
                        "type": "tool_call",
                        "tool_name": "market_collect_test",
                        "arguments": approval_arguments(),
                        "tool_call_id": "approval-1",
                    }
                ),
                raw_protocol="json",
            )
        ]
    )
    pending = MarketAgentLoop(
        first,
        repository=repository,
        registry=registry,
        protocol=AgentProtocol.JSON,
        model_id="fake",
    ).chat("执行测试采集")
    assert pending.status == RunStatus.AWAITING_APPROVAL
    assert counter["calls"] == 0
    second = FakeProvider(
        [
            ProviderResponse(
                content=json.dumps(
                    {
                        "type": "final",
                        "answer": final_answer("已写入 3 条。"),
                    },
                    ensure_ascii=False,
                ),
                raw_protocol="json",
            )
        ]
    )
    completed = MarketAgentLoop(
        second,
        repository=repository,
        registry=registry,
        protocol=AgentProtocol.NATIVE,
        model_id="fake",
    ).resume_approval(
        pending.pending_approval.tool_call_id,
        approved=True,
        expected_arguments_hash=pending.pending_approval.arguments_hash,
    )
    assert completed.ok
    assert counter["calls"] == 1
    assert repository.get_run(completed.run_id)["protocol"] == "json"


def test_rejected_approval_never_executes_handler(market_database):
    counter = {"calls": 0}
    repository = MarketAgentRepository()
    registry = approval_registry(counter)
    pending = MarketAgentLoop(
        FakeProvider(
            [
                ProviderResponse(
                    tool_calls=[
                        ProviderToolCall(
                            id="approval-reject",
                            name="market_collect_test",
                            arguments=approval_arguments(),
                        )
                    ]
                )
            ]
        ),
        repository=repository,
        registry=registry,
        model_id="fake",
    ).chat("执行测试采集")
    rejected = MarketAgentLoop(
        FakeProvider([]),
        repository=repository,
        registry=registry,
    ).resume_approval(
        pending.pending_approval.tool_call_id,
        approved=False,
        expected_arguments_hash=pending.pending_approval.arguments_hash,
    )
    assert rejected.status == RunStatus.REJECTED
    assert counter["calls"] == 0


def test_duplicate_tool_arguments_reuse_result(market_database):
    counter = {"calls": 0}
    registry = ToolRegistry()

    class NoArgs(MarketAgentAnswer):
        pass

    class ToolArgs(MarketAgentAnswer):
        pass

    def handler(_args, _context):
        counter["calls"] += 1
        return {"facts": [], "data_sources": ["测试"]}

    registry.register(
        ToolDefinition(
            name="market_read_test",
            description="测试只读",
            args_model=ToolArgs,
            handler=handler,
        )
    )
    arguments = {"conclusion": "参数"}
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="read-1",
                        name="market_read_test",
                        arguments=arguments,
                    )
                ]
            ),
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="read-2",
                        name="market_read_test",
                        arguments=arguments,
                    )
                ]
            ),
            ProviderResponse(content=json.dumps(final_answer())),
        ]
    )
    result = MarketAgentLoop(
        provider,
        repository=MarketAgentRepository(),
        registry=registry,
        model_id="fake",
    ).chat("测试重复工具")
    assert result.ok
    assert counter["calls"] == 1
    assert any(event["stage"] == "tool_reused" for event in result.events)


class FallbackProvider(LLMProvider):
    def __init__(self):
        self.calls = 0

    def complete(self, *, messages, tools=None, json_mode=False):
        self.calls += 1
        if self.calls == 1:
            raise ProviderProtocolError("native unsupported")
        return ProviderResponse(
            content=json.dumps(
                {
                    "type": "final",
                    "answer": final_answer(),
                }
            ),
            raw_protocol="json",
        )


def test_protocol_fallback_counts_both_requests(market_database):
    provider = FallbackProvider()
    repository = MarketAgentRepository()
    result = MarketAgentLoop(
        provider,
        repository=repository,
        protocol=AgentProtocol.NATIVE,
        model_id="fake",
        allow_protocol_fallback=True,
    ).chat("回答测试问题")
    assert result.ok
    assert provider.calls == 2
    assert repository.get_run(result.run_id)["model_calls"] == 2
    assert repository.get_run(result.run_id)["protocol"] == "json"


class TimeoutProvider(LLMProvider):
    def complete(self, *, messages, tools=None, json_mode=False):
        raise ProviderTimeoutError("temporary timeout")


def test_first_model_timeout_is_reported_as_retryable(market_database):
    repository = MarketAgentRepository()
    result = MarketAgentLoop(
        TimeoutProvider(),
        repository=repository,
        model_id="fake",
    ).chat("回答一个普通问题")
    assert result.status == RunStatus.FAILED
    assert result.error.retryable
    assert repository.get_run(result.run_id)["model_calls"] == 1
    assert any(event["stage"] == "model_timeout" for event in result.events)


class ToolThenTimeoutProvider(LLMProvider):
    def __init__(self):
        self.calls = 0

    def complete(self, *, messages, tools=None, json_mode=False):
        self.calls += 1
        if self.calls == 1:
            return ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="timeout-query",
                        name="market_query_series",
                        arguments={
                            "source": "elecheck",
                            "dataset": "elecheck_spot",
                            "area": "山西",
                            "start_date": "2026-07-01",
                            "end_date": "2026-07-01",
                        },
                    )
                ]
            )
        raise ProviderTimeoutError("final timeout")


def test_final_model_timeout_uses_grounded_tool_facts(seeded_market_database):
    provider = ToolThenTimeoutProvider()
    repository = MarketAgentRepository()
    result = MarketAgentLoop(
        provider,
        repository=repository,
        model_id="fake",
    ).chat("查询山西日前价格")
    assert result.ok
    assert result.answer.business_metrics
    assert any("最终模型请求超时" in item for item in result.answer.warnings)
    assert repository.get_run(result.run_id)["model_calls"] == 2


class StopProvider(LLMProvider):
    def __init__(self, repository):
        self.repository = repository

    def complete(self, *, messages, tools=None, json_mode=False):
        with sqlite3.connect(get_settings().database_url.removeprefix("sqlite:///")) as connection:
            actual_run_id = connection.execute(
                "SELECT id FROM market_agent_runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()[0]
        self.repository.request_stop(actual_run_id)
        return ProviderResponse(content=json.dumps(final_answer()))


def test_stop_requested_during_model_call_stops_before_tool_or_answer(market_database):
    repository = MarketAgentRepository()
    result = MarketAgentLoop(
        StopProvider(repository),
        repository=repository,
        model_id="fake",
    ).chat("执行一个普通分析")
    assert result.status == RunStatus.STOPPED
    assert result.answer is None
    assert repository.get_run(result.run_id)["status"] == "stopped"


def test_elexon_catalog_is_grounded_and_rendered_without_model_call(market_database):
    provider = FakeProvider([])
    result = MarketAgentLoop(
        provider,
        repository=MarketAgentRepository(),
        model_id="fake",
    ).chat("英国数据有哪些数据集？告诉我该怎么查找")

    assert result.ok
    assert provider.requests == []
    assert {item.dataset for item in result.answer.datasets} == {
        "elexon_system_prices",
        "elexon_initial_demand_outturn",
        "elexon_generation_by_fuel_half_hourly",
        "elexon_wind_generation_forecast",
        "elexon_interconnector_flows",
    }
    assert "当前支持 5 个 Elexon 数据集" in result.answer.conclusion
    assert "完整 Elexon 接口目录共 17 项" in result.answer.conclusion
    assert "powertrade elexon-datasets" in result.answer.conclusion
    assert "未返回可量化业务事实" not in result.answer.conclusion


def test_unrenderable_direct_tool_result_is_sent_to_model(market_database):
    registry = ToolRegistry()

    def opaque_handler(_args, _context):
        return {"opaque_result": {"message": "需要模型解释的结果"}}

    registry.register(
        ToolDefinition(
            name="market_list_datasets",
            description="返回测试结果",
            args_model=ListDatasetsArgs,
            handler=opaque_handler,
        )
    )
    provider = FakeProvider(
        [ProviderResponse(content=json.dumps(final_answer("模型已解释未知结果。")))]
    )
    result = MarketAgentLoop(
        provider,
        repository=MarketAgentRepository(),
        registry=registry,
        model_id="fake",
    ).chat("英国有哪些数据集？")

    assert result.ok
    assert result.answer.conclusion == "模型已解释未知结果。"
    assert len(provider.requests) == 1
    assert "需要模型解释的结果" in json.dumps(
        provider.requests[0]["messages"], ensure_ascii=False
    )
    assert any(event["stage"] == "model_explanation_required" for event in result.events)


def test_news_result_replaces_numeric_only_fallback(seeded_market_database):
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="news-search",
                        name="market_search_gzpec_news",
                        arguments={"keyword": "现货", "news_type": "spot_market"},
                    )
                ]
            ),
            ProviderResponse(
                content=json.dumps(
                    final_answer("请求已完成，未返回可量化业务事实。"),
                    ensure_ascii=False,
                )
            ),
        ]
    )
    result = MarketAgentLoop(
        provider,
        repository=MarketAgentRepository(),
        model_id="fake",
    ).chat("查找广州电力交易中心现货市场公开信息")

    assert result.ok
    assert result.answer.reference_items[0].title == "广东电力现货市场分析"
    assert "广东电力现货市场分析" in result.answer.conclusion
    assert "未返回可量化业务事实" not in result.answer.conclusion


def test_gui_result_format_and_cli_surface(market_database):
    text = MarketAgentApp._format_result(
        {
            "conclusion": "完成。",
            "data_sources": ["GridStatus"],
            "business_metrics": [
                {"name": "负荷均值", "value": 1000, "unit": "MW"}
            ],
            "comparison_status": "side_by_side_only",
            "dataset_catalog_summaries": [
                {
                    "scope_note": (
                        "多数据源 Agent 当前支持 5 个 Elexon 数据集；"
                        "项目完整 Elexon 接口目录共 17 项。"
                    ),
                    "lookup_hint": "运行 powertrade elexon-datasets。",
                }
            ],
            "datasets": [
                {
                    "dataset": "elexon_system_prices",
                    "title": "结算系统价格",
                    "metric": "system_sell_price",
                    "unit": "GBP/MWh",
                    "time_basis": "英国结算日期与结算时段",
                    "local_record_count": 2,
                    "local_earliest": "2026-07-01",
                    "local_latest": "2026-07-02",
                }
            ],
            "warnings": ["口径不同。"],
            "executed_actions": ["market_query_series"],
        }
    )
    assert "结构" not in text
    assert "负荷均值：1000 MW" in text
    assert "仅并列展示" in text
    assert "数据集目录" in text
    assert "结算系统价格（elexon_system_prices）" in text
    assert "当前支持 5 个 Elexon 数据集" in text
    gui_source = (
        Path(__file__).parents[1] / "src" / "powertrade_crawler" / "gui.py"
    ).read_text(encoding="utf-8")
    assert 'notebook.add(market_agent_tab, text="多数据源 Agent")' in gui_source
    assert 'notebook.add(elecheck_tab, text="Elecheck 易能电易查")' in gui_source
    runner = CliRunner()
    help_result = runner.invoke(app, ["market-agent", "--help"])
    assert help_result.exit_code == 0
    assert "doctor" in help_result.stdout
    report = run_offline_evaluation()
    assert report["summary"] == {"total": 15, "passed": 15, "failed": 0}


def test_gui_ctrl_enter_sends_without_inserting_newline():
    app = MarketAgentApp.__new__(MarketAgentApp)
    calls = []
    app.send_message = lambda: calls.append("sent")

    result = app._submit_from_keyboard()

    assert calls == ["sent"]
    assert result == "break"
