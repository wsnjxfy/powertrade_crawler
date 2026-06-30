"""Probe ENTSO-E cross-border dataset availability for common area pairs.

The script intentionally never prints or writes the ENTSO-E security token. It writes
only dataset names, area aliases, EIC codes, row counts, and error/no-data summaries.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from powertrade_crawler.clients.entsoe import EntsoeClient
from powertrade_crawler.spiders.entsoe import ENTSOE_BIDDING_ZONES, load_entsoe_request_configs


DEFAULT_BORDER_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("AT", "CH"),
    ("AT", "CZ"),
    ("AT", "DE-LU"),
    ("AT", "HU"),
    ("AT", "IT-NORTH"),
    ("AT", "SI"),
    ("BE", "FR"),
    ("BE", "GB"),
    ("BE", "NL"),
    ("CH", "DE-LU"),
    ("CH", "FR"),
    ("CH", "IT-NORTH"),
    ("CZ", "DE-LU"),
    ("CZ", "PL"),
    ("CZ", "SK"),
    ("DE-LU", "BE"),
    ("DE-LU", "DK1"),
    ("DE-LU", "DK2"),
    ("DE-LU", "FR"),
    ("DE-LU", "NL"),
    ("DE-LU", "NO2"),
    ("DE-LU", "PL"),
    ("DK1", "DK2"),
    ("DK1", "NO2"),
    ("DK1", "SE3"),
    ("DK2", "SE4"),
    ("EE", "FI"),
    ("EE", "LV"),
    ("ES", "FR"),
    ("ES", "PT"),
    ("FI", "SE1"),
    ("FR", "GB"),
    ("FR", "IT-NORTH"),
    ("FR", "NL"),
    ("FR", "ES"),
    ("HR", "HU"),
    ("HR", "SI"),
    ("HU", "RO"),
    ("HU", "RS"),
    ("HU", "SK"),
    ("IT-NORTH", "SI"),
    ("LT", "LV"),
    ("LT", "PL"),
    ("ME", "RS"),
    ("MK", "RS"),
    ("NL", "GB"),
    ("NO1", "NO2"),
    ("NO1", "NO3"),
    ("NO1", "NO5"),
    ("NO1", "SE3"),
    ("NO2", "NO5"),
    ("NO2", "GB"),
    ("NO3", "NO4"),
    ("NO3", "NO5"),
    ("NO3", "SE2"),
    ("NO4", "SE1"),
    ("PL", "SE4"),
    ("RO", "BG"),
    ("RS", "BA"),
    ("RS", "BG"),
    ("SE1", "SE2"),
    ("SE2", "SE3"),
    ("SE3", "SE4"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe common ENTSO-E cross-border area pairs and write availability JSON."
    )
    parser.add_argument("--start-date", default="2026-06-01", help="UTC start date, YYYY-MM-DD.")
    parser.add_argument("--end-date", default="2026-06-02", help="UTC exclusive end date, YYYY-MM-DD.")
    parser.add_argument(
        "--dataset",
        action="append",
        help="Probe only this dataset name. Repeatable. Defaults to all border datasets.",
    )
    parser.add_argument(
        "--area-scope",
        choices=["candidates", "all"],
        default="candidates",
        help="Use curated candidate borders or all ordered area pairs.",
    )
    parser.add_argument(
        "--max-pairs",
        type=int,
        default=None,
        help="Optional maximum number of directed pairs to probe, useful for smoke tests.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("configs/entsoe/border_availability.json"),
        help="Output JSON path.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=8.0,
        help="Probe HTTP timeout. Short by default so one slow pair does not block the batch.",
    )
    parser.add_argument(
        "--retry-times",
        type=int,
        default=0,
        help="Probe retry count. Zero by default because this is an availability scan.",
    )
    return parser.parse_args()


def parse_cli_date(value: str) -> datetime:
    return datetime.fromisoformat(value)


def directed_pairs(area_scope: str) -> list[tuple[str, str]]:
    if area_scope == "all":
        aliases = sorted(ENTSOE_BIDDING_ZONES)
        return [(source, target) for source in aliases for target in aliases if source != target]

    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str]] = []
    for source, target in DEFAULT_BORDER_CANDIDATES:
        for directed in ((source, target), (target, source)):
            if directed in seen:
                continue
            seen.add(directed)
            pairs.append(directed)
    return pairs


def border_dataset_configs(selected_names: list[str] | None) -> list[dict[str, Any]]:
    configs = [
        config
        for config in load_entsoe_request_configs()
        if config.get("domain_mode") == "border"
    ]
    if not selected_names:
        return configs
    selected = set(selected_names)
    return [config for config in configs if config["name"] in selected]


def probe_pair(
    *,
    client: EntsoeClient,
    config: dict[str, Any],
    source: str,
    target: str,
    period_start: str,
    period_end: str,
) -> dict[str, Any]:
    params = {
        **config["params"],
        "in_Domain": ENTSOE_BIDDING_ZONES[source],
        "out_Domain": ENTSOE_BIDDING_ZONES[target],
        "periodStart": period_start,
        "periodEnd": period_end,
    }
    try:
        rows = client.query_document(params)
    except RuntimeError as exc:
        message = str(exc)
        reason = "no_data" if "No matching data found" in message else "error"
        return {
            "status": reason,
            "in_area": source,
            "out_area": target,
            "in_domain": ENTSOE_BIDDING_ZONES[source],
            "out_domain": ENTSOE_BIDDING_ZONES[target],
            "reason": sanitize_error(message),
        }
    except Exception as exc:
        return {
            "status": "error",
            "in_area": source,
            "out_area": target,
            "in_domain": ENTSOE_BIDDING_ZONES[source],
            "out_domain": ENTSOE_BIDDING_ZONES[target],
            "reason": type(exc).__name__,
        }
    if not rows:
        return {
            "status": "no_data",
            "in_area": source,
            "out_area": target,
            "in_domain": ENTSOE_BIDDING_ZONES[source],
            "out_domain": ENTSOE_BIDDING_ZONES[target],
            "reason": "empty response",
        }
    return {
        "status": "has_data",
        "in_area": source,
        "out_area": target,
        "in_domain": ENTSOE_BIDDING_ZONES[source],
        "out_domain": ENTSOE_BIDDING_ZONES[target],
        "row_count": len(rows),
    }


def sanitize_error(message: str) -> str:
    if len(message) <= 240:
        return message
    return message[:237] + "..."


def main() -> None:
    args = parse_args()
    start = parse_cli_date(args.start_date)
    end = parse_cli_date(args.end_date)
    client = EntsoeClient()
    client.retry_times = args.retry_times
    client.client.timeout = httpx.Timeout(args.timeout_seconds)
    pairs = directed_pairs(args.area_scope)
    if args.max_pairs is not None:
        pairs = pairs[: args.max_pairs]
    period_start = client.format_period(start)
    period_end = client.format_period(end)
    configs = border_dataset_configs(args.dataset)

    if args.output.exists():
        try:
            payload = json.loads(args.output.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
    else:
        payload = {}
    payload.update(
        {
            "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "period_start": args.start_date,
            "period_end": args.end_date,
            "area_scope": args.area_scope,
            "pair_count": len(pairs),
            "note": (
                "has_data/no_data is based on this probe window. ENTSO-E availability can vary "
                "by date, direction, and dataset."
            ),
            "areas": {
                alias: {"eic": eic}
                for alias, eic in sorted(ENTSOE_BIDDING_ZONES.items())
            },
            "datasets": payload.get("datasets", {}),
        }
    )

    try:
        for config in configs:
            name = config["name"]
            print(f"Probing {name} ({len(pairs)} directed pairs)...")
            available: list[dict[str, Any]] = []
            unavailable: list[dict[str, Any]] = []
            errors: list[dict[str, Any]] = []
            for index, (source, target) in enumerate(pairs, start=1):
                result = probe_pair(
                    client=client,
                    config=config,
                    source=source,
                    target=target,
                    period_start=period_start,
                    period_end=period_end,
                )
                status = result.pop("status")
                if status == "has_data":
                    available.append(result)
                elif status == "no_data":
                    unavailable.append(result)
                else:
                    errors.append(result)
                if index % 10 == 0 or index == len(pairs):
                    print(
                        f"  {index}/{len(pairs)} done | "
                        f"has_data={len(available)} no_data={len(unavailable)} errors={len(errors)}"
                    )
            payload["datasets"][name] = {
                "title_zh": config["title_zh"],
                "title_en": config["title_en"],
                "available_pairs": available,
                "unavailable_pairs": unavailable,
                "errors": errors,
            }
    finally:
        client.close()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
