from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from powertrade_crawler.market_agent.rag import RagIndexService, runtime_root


def default_case_path() -> Path:
    source_path = (
        runtime_root()
        / "src"
        / "powertrade_crawler"
        / "market_agent"
        / "data"
        / "rag_eval_cases.json"
    )
    if source_path.is_file():
        return source_path
    return (
        runtime_root()
        / "powertrade_crawler"
        / "market_agent"
        / "data"
        / "rag_eval_cases.json"
    )


def run_rag_evaluation(
    service: RagIndexService | None = None,
    *,
    case_path: Path | None = None,
) -> dict[str, Any]:
    service = service or RagIndexService()
    path = case_path or default_case_path()
    cases = json.loads(path.read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []
    reciprocal_ranks: list[float] = []
    successful_retrievals = 0
    filter_correct = 0
    valid_citations = 0
    total_citations = 0
    false_positive_empty = 0
    empty_cases = 0
    for case in cases:
        result = service.search(
            case["query"],
            sources=case.get("sources"),
            content_types=case.get("content_types"),
            start_date=(
                date.fromisoformat(case["start_date"])
                if case.get("start_date")
                else None
            ),
            end_date=(
                date.fromisoformat(case["end_date"])
                if case.get("end_date")
                else None
            ),
            top_k=5,
        )
        hits = result["hits"]
        expected_empty = bool(case.get("expect_empty"))
        rank = 0
        if expected_empty:
            empty_cases += 1
            if hits:
                false_positive_empty += 1
        else:
            for index, hit in enumerate(hits, start=1):
                source_ok = not case.get("expected_source") or (
                    hit.get("source") == case["expected_source"]
                )
                title_token = str(case.get("title_contains") or "")
                title_ok = not title_token or title_token.lower() in str(
                    hit.get("title") or ""
                ).lower()
                if source_ok and title_ok:
                    rank = index
                    break
            if rank:
                successful_retrievals += 1
                reciprocal_ranks.append(1.0 / rank)
            else:
                reciprocal_ranks.append(0.0)

        filters_ok = all(
            (not case.get("sources") or hit.get("source") in case["sources"])
            and (
                not case.get("content_types")
                or hit.get("content_type") in case["content_types"]
            )
            for hit in hits
        )
        if filters_ok:
            filter_correct += 1
        for hit in hits:
            total_citations += 1
            if str(hit.get("citation_id") or "").startswith("K-"):
                valid_citations += 1
        results.append(
            {
                "id": case["id"],
                "query": case["query"],
                "passed": (not hits if expected_empty else rank > 0) and filters_ok,
                "rank": rank or None,
                "retrieval_mode": result["retrieval_mode"],
                "returned": len(hits),
            }
        )

    positive_count = len(cases) - empty_cases
    recall_at_5 = successful_retrievals / positive_count if positive_count else 1.0
    mrr_at_5 = (
        sum(reciprocal_ranks) / len(reciprocal_ranks)
        if reciprocal_ranks
        else 1.0
    )
    empty_false_positive_rate = (
        false_positive_empty / empty_cases if empty_cases else 0.0
    )
    summary = {
        "case_count": len(cases),
        "passed": sum(1 for item in results if item["passed"]),
        "failed": sum(1 for item in results if not item["passed"]),
        "recall_at_5": round(recall_at_5, 4),
        "mrr_at_5": round(mrr_at_5, 4),
        "filter_accuracy": round(filter_correct / len(cases), 4) if cases else 1.0,
        "citation_validity": (
            round(valid_citations / total_citations, 4) if total_citations else 1.0
        ),
        "empty_false_positive_rate": round(empty_false_positive_rate, 4),
        "targets": {
            "recall_at_5": 0.90,
            "mrr_at_5": 0.80,
            "filter_accuracy": 1.0,
            "citation_validity": 1.0,
            "empty_false_positive_rate_max": 0.05,
        },
    }
    summary["accepted"] = (
        recall_at_5 >= 0.90
        and mrr_at_5 >= 0.80
        and summary["filter_accuracy"] == 1.0
        and summary["citation_validity"] == 1.0
        and empty_false_positive_rate <= 0.05
    )
    return {"summary": summary, "results": results, "case_path": str(path)}
