from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import pytest

import powertrade_crawler.market_agent.rag as rag_module
from powertrade_crawler.config import get_settings
from powertrade_crawler.market_agent.data_tools import build_market_tool_registry
from powertrade_crawler.market_agent.loop import candidate_tool_names
from powertrade_crawler.market_agent.loop import MarketAgentLoop
from powertrade_crawler.market_agent.rag import (
    RAG_EMBEDDING_DIM,
    RAG_MODEL_VERSION,
    RagBuildCancelled,
    RagIndexService,
    RagError,
    RagModelUnavailable,
    blob_to_vector,
    chunk_knowledge_text,
    collect_whitelisted_documents,
    ensure_rag_schema,
    normalize_knowledge_text,
    resolve_sqlite_path,
    vector_to_blob,
)
from powertrade_crawler.market_agent.schemas import MarketAgentAnswer


class FakeEmbedder:
    model_version = RAG_MODEL_VERSION
    dimension = RAG_EMBEDDING_DIM

    def __init__(self) -> None:
        self.document_calls = 0

    @staticmethod
    def _vector(text: str) -> np.ndarray:
        vector = np.zeros(RAG_EMBEDDING_DIM, dtype=np.float32)
        for character in text.lower():
            vector[ord(character) % RAG_EMBEDDING_DIM] += 1.0
        if not vector.any():
            vector[0] = 1.0
        return vector / np.linalg.norm(vector)

    def embed_documents(self, texts):
        self.document_calls += len(texts)
        return np.vstack([self._vector(text) for text in texts])

    def embed_query(self, text):
        return self._vector(text)


def create_corpus_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE gzpec_news_records (
            id INTEGER PRIMARY KEY,
            category TEXT,
            title TEXT,
            url TEXT,
            publish_date TEXT,
            news_type TEXT,
            content_json TEXT
        )
        """
    )
    connection.executemany(
        """
        INSERT INTO gzpec_news_records
        (id, category, title, url, publish_date, news_type, content_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                1,
                "政策规则",
                "绿证交易规则说明",
                "https://example.test/green",
                "2026-01-02",
                "green_certificate",
                json.dumps(
                    [{"block_type": "text", "text": "绿证交易要求市场主体完成注册和交易申报。"}],
                    ensure_ascii=False,
                ),
            ),
            (
                2,
                "市场研究",
                "现货市场结算说明",
                "https://example.test/spot",
                "2025-05-06",
                "spot_market",
                json.dumps(
                    [{"block_type": "text", "text": "现货市场按业务日期组织结算。"}],
                    ensure_ascii=False,
                ),
            ),
        ],
    )
    # A forbidden table deliberately contains a unique secret-like token.
    connection.execute("CREATE TABLE agent_sessions (id INTEGER, content TEXT)")
    connection.execute(
        "INSERT INTO agent_sessions VALUES (1, 'FORBIDDEN_SESSION_TOKEN_9f3c')"
    )
    connection.commit()
    connection.close()


@pytest.fixture
def rag_service(tmp_path: Path) -> tuple[RagIndexService, FakeEmbedder]:
    database_path = tmp_path / "rag.db"
    create_corpus_database(database_path)
    embedder = FakeEmbedder()
    return (
        RagIndexService(
            database_path=database_path,
            resource_root=tmp_path,
            embedder=embedder,
        ),
        embedder,
    )


def test_chunking_is_stable_clean_and_bounded() -> None:
    text = ("第一段包含中文空格。\r\n\r\n" + "第二段很长。" * 180).replace("中文", "中\u3000文")
    first = chunk_knowledge_text(text)
    second = chunk_knowledge_text(text)
    assert first == second
    assert all(0 < len(item) <= 650 for item in first)
    assert "\r" not in "".join(first)
    assert normalize_knowledge_text("甲\u3000乙") == "甲 乙"


def test_vector_blob_round_trip_is_512_dimensional_and_normalized() -> None:
    raw = np.arange(1, RAG_EMBEDDING_DIM + 1, dtype=np.float32)
    restored = blob_to_vector(vector_to_blob(raw))
    assert restored.shape == (RAG_EMBEDDING_DIM,)
    assert np.linalg.norm(restored) == pytest.approx(1.0, abs=1e-6)


def test_relative_database_path_uses_writable_working_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///data/powertrade.db")
    get_settings.cache_clear()
    try:
        assert resolve_sqlite_path() == (tmp_path / "data" / "powertrade.db")
    finally:
        get_settings.cache_clear()


def test_status_and_search_do_not_create_missing_database(tmp_path: Path) -> None:
    database_path = tmp_path / "missing" / "powertrade.db"
    service = RagIndexService(
        database_path=database_path,
        resource_root=tmp_path,
        embedder=FakeEmbedder(),
    )

    assert service.status()["status"] == "not_built"
    result = service.search("绿证规则")

    assert result["retrieval_mode"] == "unavailable"
    assert result["hits"] == []
    assert not database_path.exists()
    assert not database_path.parent.exists()


def test_rebuild_and_hybrid_search(rag_service) -> None:
    service, _embedder = rag_service
    status = service.rebuild()
    result = service.search("绿证交易规则", top_k=5)
    assert status["status"] == "ready"
    assert status["document_count"] >= 2
    assert result["retrieval_mode"] == "hybrid"
    assert result["hits"][0]["title"] == "绿证交易规则说明"
    assert result["hits"][0]["citation_id"].startswith("K-")
    assert result["hits"][0]["untrusted_external_content"] is True


def test_lexical_rerank_expands_bilingual_terms_and_prefers_exact_entities() -> None:
    query = "AESO 未来13天负荷预测"
    target = {
        "title": "AESO Load Forecast：业务说明",
        "text": "提供未来13天的需求预测。",
    }
    unrelated = {
        "title": "周前总负荷预测：业务说明",
        "text": "ENTSO-E 周前负荷预测。",
    }

    lexical_query = RagIndexService._lexical_query(query)

    assert '"load"' in lexical_query
    assert '"forecast"' in lexical_query
    assert RagIndexService._lexical_relevance_score(
        query, target
    ) > RagIndexService._lexical_relevance_score(query, unrelated)
    assert RagIndexService._exact_title_boost(query, target) > 0
    assert RagIndexService._exact_title_boost(query, unrelated) == 0


def test_lexical_rerank_prefers_complete_numeric_period_in_title() -> None:
    query = "2026年第18周绿证交易行情"
    exact = {"title": "绿证交易每周行情一览（2026年第18周）"}
    partial = {"title": "绿证交易每周行情一览（2026年第17周）"}

    assert RagIndexService._exact_title_boost(query, exact) > 0
    assert RagIndexService._exact_title_boost(query, partial) == 0


def test_lexical_rerank_prefers_explanations_over_market_roundups() -> None:
    query = "广州绿证交易规则是什么"
    explanation = {"title": "绿证制度与交易办法解读"}
    roundup = {"title": "绿证交易每周行情一览"}

    assert RagIndexService._explanation_intent_adjustment(
        query, explanation
    ) > 0
    assert RagIndexService._explanation_intent_adjustment(query, roundup) < 0


def test_incremental_update_reuses_unchanged_embeddings(rag_service) -> None:
    service, embedder = rag_service
    service.rebuild()
    calls_after_rebuild = embedder.document_calls
    result = service.update()
    assert embedder.document_calls == calls_after_rebuild
    assert result["reused_embedding_count"] == result["chunk_count"]


def test_source_scoped_update_copies_unrequested_sources_unchanged(
    rag_service,
    monkeypatch,
) -> None:
    service, _embedder = rag_service
    service.rebuild()
    with sqlite3.connect(service.database_path) as connection:
        before = connection.execute(
            """
            SELECT document_id, title FROM rag_documents
            WHERE generation = (SELECT active_generation FROM rag_index_state)
              AND source = 'elecheck'
            ORDER BY document_id LIMIT 1
            """
        ).fetchone()
    assert before is not None
    original_collect = rag_module.collect_whitelisted_documents

    def changed_collect(connection, *, resource_root=None):
        documents = original_collect(connection, resource_root=resource_root)
        return [
            replace(
                document,
                title=f"CHANGED {document.title}",
                text=f"CHANGED {document.text}",
            )
            if document.source == "elecheck"
            else document
            for document in documents
        ]

    monkeypatch.setattr(rag_module, "collect_whitelisted_documents", changed_collect)

    targeted = service.update(sources=["gzpec"])
    with sqlite3.connect(service.database_path) as connection:
        after_targeted = connection.execute(
            """
            SELECT title FROM rag_documents
            WHERE generation = ? AND document_id = ?
            """,
            (targeted["active_generation"], before[0]),
        ).fetchone()[0]
    assert after_targeted == before[1]

    full = service.update()
    with sqlite3.connect(service.database_path) as connection:
        after_full = connection.execute(
            """
            SELECT title FROM rag_documents
            WHERE generation = ? AND document_id = ?
            """,
            (full["active_generation"], before[0]),
        ).fetchone()[0]
    assert after_full == f"CHANGED {before[1]}"


def test_source_scoped_update_reports_only_non_empty_write_batches(rag_service) -> None:
    service, _embedder = rag_service
    first = service.rebuild()
    with sqlite3.connect(service.database_path) as connection:
        selected_chunk_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM rag_chunks c
            JOIN rag_documents d
              ON d.generation = c.generation
             AND d.document_id = c.document_id
            WHERE c.generation = ? AND d.source = 'gzpec'
            """,
            (first["active_generation"],),
        ).fetchone()[0]

    progress: list[dict[str, object]] = []
    service.update(sources=["gzpec"], progress_callback=progress.append)

    write_events = [
        item for item in progress if item["stage"] == "生成向量并写入索引"
    ]
    expected_batches = (selected_chunk_count + 63) // 64
    assert len(write_events) == expected_batches
    assert progress[-1]["current"] == progress[-1]["total"]


def test_failed_rebuild_keeps_previous_generation(rag_service) -> None:
    service, _embedder = rag_service
    first = service.rebuild()

    def fail_after_first_batch(item):
        if item["current"]:
            raise RuntimeError("simulated build failure")

    with pytest.raises(RuntimeError, match="simulated"):
        service.rebuild(progress_callback=fail_after_first_batch)
    status = service.status()
    assert status["active_generation"] == first["active_generation"]
    assert service.search("绿证交易规则")["hits"]


def test_cancelled_update_keeps_previous_generation(rag_service) -> None:
    service, _embedder = rag_service
    first = service.rebuild()

    def cancel(item):
        if item["current"]:
            service.request_cancel()

    with pytest.raises(RagBuildCancelled):
        service.rebuild(progress_callback=cancel)
    assert service.status()["active_generation"] == first["active_generation"]


def test_cross_process_build_lock_does_not_corrupt_active_state(rag_service) -> None:
    service, _embedder = rag_service
    first = service.rebuild()
    with sqlite3.connect(service.database_path) as connection:
        connection.execute(
            """
            UPDATE rag_index_state
            SET status = 'building', last_started_at = '2099-01-01T00:00:00+00:00'
            WHERE id = 1
            """
        )
        connection.commit()
    with pytest.raises(RagError, match="跨进程"):
        service.update()
    status = service.status()
    assert status["status"] == "building"
    assert status["active_generation"] == first["active_generation"]


def test_recent_cancel_request_does_not_allow_second_process_build(rag_service) -> None:
    service, _embedder = rag_service
    first = service.rebuild()
    with sqlite3.connect(service.database_path) as connection:
        connection.execute(
            """
            UPDATE rag_index_state
            SET status = 'building', cancel_requested = 1,
                last_started_at = '2099-01-01T00:00:00+00:00'
            WHERE id = 1
            """
        )
        connection.commit()

    with pytest.raises(RagError, match="跨进程"):
        service.update()

    status = service.status()
    assert status["status"] == "building"
    assert status["active_generation"] == first["active_generation"]


@pytest.mark.parametrize("last_started_at", [None, "invalid-timestamp"])
def test_orphaned_build_with_missing_or_invalid_timestamp_is_recovered(
    rag_service,
    last_started_at,
) -> None:
    service, _embedder = rag_service
    first = service.rebuild()
    with sqlite3.connect(service.database_path) as connection:
        connection.execute(
            """
            UPDATE rag_index_state
            SET status = 'building', last_started_at = ?
            WHERE id = 1
            """,
            (last_started_at,),
        )
        connection.commit()

    recovered = service.update()

    assert recovered["status"] == "ready"
    assert recovered["active_generation"] != first["active_generation"]
    assert service.search("绿证交易规则")["hits"]


def test_cleanup_failure_after_switch_keeps_new_generation(
    rag_service,
    monkeypatch,
) -> None:
    service, _embedder = rag_service
    first = service.rebuild()

    def fail_cleanup(_connection, _active_generation):
        raise sqlite3.OperationalError("simulated cleanup failure")

    monkeypatch.setattr(service, "_cleanup_old_generations", fail_cleanup)
    second = service.rebuild()

    assert second["status"] == "ready"
    assert second["active_generation"] != first["active_generation"]
    assert "旧代次清理失败" in second["last_error"]
    assert service.status()["active_generation"] == second["active_generation"]
    assert service.search("绿证交易规则")["hits"]


def test_status_reports_missing_state_row_without_crashing(rag_service) -> None:
    service, _embedder = rag_service
    service.rebuild()
    with sqlite3.connect(service.database_path) as connection:
        connection.execute("DELETE FROM rag_index_state WHERE id = 1")
        connection.commit()

    status = service.status()

    assert status["status"] == "failed"
    assert status["ready"] is False
    assert "状态记录缺失" in status["last_error"]
    result = service.search("绿证交易规则")
    assert result["retrieval_mode"] == "unavailable"
    assert result["hits"] == []


def test_status_detects_active_generation_without_chunks(rag_service) -> None:
    service, _embedder = rag_service
    service.rebuild()
    with sqlite3.connect(service.database_path) as connection:
        connection.execute(
            "UPDATE rag_index_state SET active_generation = 'missing-generation'"
        )
        connection.commit()

    status = service.status()

    assert status["status"] == "failed"
    assert status["ready"] is False
    assert "缺少分块数据" in status["last_error"]
    result = service.search("绿证交易规则")
    assert result["retrieval_mode"] == "unavailable"
    assert result["hits"] == []


def test_model_missing_builds_fts_fallback(tmp_path: Path) -> None:
    database_path = tmp_path / "fallback.db"
    create_corpus_database(database_path)

    def unavailable():
        raise RagModelUnavailable("model intentionally missing")

    service = RagIndexService(
        database_path=database_path,
        resource_root=tmp_path,
        embedder_factory=unavailable,
    )
    assert service.rebuild()["status"] == "degraded"
    result = service.search("绿证交易规则")
    assert result["retrieval_mode"] == "lexical_fallback"
    assert result["hits"]
    assert "降级" in result["warnings"][0]


def test_corrupt_vector_is_skipped_without_breaking_fulltext(rag_service) -> None:
    service, _embedder = rag_service
    service.rebuild()
    with sqlite3.connect(service.database_path) as connection:
        connection.execute(
            "UPDATE rag_chunks SET embedding = X'00' WHERE rowid = (SELECT MIN(rowid) FROM rag_chunks)"
        )
        connection.commit()
    assert service.search("绿证交易规则")["hits"]


def test_read_only_index_can_search_but_cannot_update(rag_service) -> None:
    service, _embedder = rag_service
    service.rebuild()

    class ReadOnlyService(RagIndexService):
        def _connect(self, *, initialize: bool = True):
            connection = super()._connect(initialize=False)
            connection.execute("PRAGMA query_only = ON")
            if initialize:
                ensure_rag_schema(connection)
            return connection

    read_only = ReadOnlyService(
        database_path=service.database_path,
        resource_root=service.resource_root,
        embedder=FakeEmbedder(),
    )
    assert read_only.search("绿证交易规则")["hits"]
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        read_only.update()


def test_source_content_type_and_date_filters(rag_service) -> None:
    service, _embedder = rag_service
    service.rebuild()
    result = service.search(
        "市场结算",
        sources=["gzpec"],
        content_types=["article"],
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
    )
    assert result["hits"]
    assert all(item["source"] == "gzpec" for item in result["hits"])
    assert all(item["content_type"] == "article" for item in result["hits"])
    assert all(item["publish_date"].startswith("2025-") for item in result["hits"])


def test_whitelist_never_reads_agent_session_table(rag_service) -> None:
    service, _embedder = rag_service
    service.rebuild()
    result = service.search("FORBIDDEN_SESSION_TOKEN_9f3c")
    assert all(
        "FORBIDDEN_SESSION_TOKEN_9f3c" not in item["text"]
        for item in result["hits"]
    )
    with sqlite3.connect(service.database_path) as connection:
        connection.row_factory = sqlite3.Row
        documents = collect_whitelisted_documents(
            connection, resource_root=service.resource_root
        )
    assert "FORBIDDEN_SESSION_TOKEN_9f3c" not in " ".join(
        item.text for item in documents
    )


def test_agent_routes_knowledge_without_expanding_permissions() -> None:
    registry = build_market_tool_registry()
    definition = registry.get("market_search_knowledge")
    assert definition.risk_level.value == "auto"
    assert "只读" in definition.side_effect
    assert candidate_tool_names("绿证交易规则是什么意思", registry) == [
        "market_search_knowledge"
    ]
    assert candidate_tool_names("最新绿证新闻", registry)[0] == (
        "market_search_gzpec_news"
    )


def test_agent_citations_are_hydrated_only_from_tool_results() -> None:
    loop = MarketAgentLoop.__new__(MarketAgentLoop)
    payload = {
        "retrieval_mode": "hybrid",
        "hits": [
            {
                "citation_id": "K-valid123456",
                "title": "绿证规则",
                "source": "gzpec",
                "source_label": "广州电力交易中心",
                "content_type": "article",
                "url": "https://example.test/rule",
                "publish_date": "2026-01-01",
                "excerpt": "受控工具返回的资料摘要。",
            }
        ],
        "data_sources": ["广州电力交易中心"],
    }
    answer = loop._ground_answer(  # noqa: SLF001
        MarketAgentAnswer(
            conclusion="带引用的规则结论。",
            citation_ids=["K-valid123456", "K-invented000"],
        ),
        [("market_search_knowledge", payload)],
    )
    assert answer.citation_ids == ["K-valid123456"]
    assert answer.knowledge_citations[0].url == "https://example.test/rule"
    assert answer.retrieval_mode == "hybrid"
    assert any("无效知识引用" in item for item in answer.warnings)
    assert not loop._knowledge_answer_needs_citation(  # noqa: SLF001
        MarketAgentAnswer(
            conclusion="带引用的规则结论。",
            citation_ids=["K-valid123456"],
        ),
        [("market_search_knowledge", payload)],
    )


def test_agent_falls_back_to_source_list_when_model_omits_citations() -> None:
    loop = MarketAgentLoop.__new__(MarketAgentLoop)
    tool_payloads = [
        (
            "market_search_knowledge",
            {
                "retrieval_mode": "lexical_fallback",
                "hits": [
                    {
                        "citation_id": "K-fallback1234",
                        "title": "相关资料",
                        "source": "elexon",
                        "content_type": "dataset_summary",
                        "excerpt": "资料摘要",
                    }
                ],
            },
        )
    ]
    model_answer = MarketAgentAnswer(conclusion="没有引用的知识结论")
    assert loop._knowledge_answer_needs_citation(  # noqa: SLF001
        model_answer,
        tool_payloads,
    )
    answer = loop._ground_answer(  # noqa: SLF001
        model_answer,
        tool_payloads,
    )
    assert "相关资料" in answer.conclusion
    assert "没有引用的知识结论" not in answer.conclusion
    assert answer.citation_ids == ["K-fallback1234"]
    assert answer.retrieval_mode == "lexical_fallback"
