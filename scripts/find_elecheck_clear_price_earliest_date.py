from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
from time import sleep

from powertrade_crawler.clients.elecheck import ElecheckClient, ElecheckUnauthorizedError
from powertrade_crawler.elecheck_auth import (
    cache_elecheck_authorization,
    resolve_elecheck_authorization,
)
from powertrade_crawler.storage import resolve_elecheck_area_code


@dataclass
class ProbeResult:
    probe_date: date
    available: bool
    row_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Find the earliest available Elecheck clear price date with exponential "
            "backoff plus binary search."
        ),
    )
    area_group = parser.add_mutually_exclusive_group(required=True)
    area_group.add_argument("--area", help="Area alias, for example 江苏.")
    area_group.add_argument("--area-code", help="Elecheck area code, for example 320000000000.")
    parser.add_argument(
        "--end-date",
        default=date.today().isoformat(),
        help="Known/latest date to start probing from, default: today.",
    )
    parser.add_argument(
        "--lower-bound",
        default="2025-01-01",
        help="Earliest date the search is allowed to test, default: 2025-01-01.",
    )
    parser.add_argument(
        "--authorization",
        help="Raw Elecheck authorization JWT. If omitted, .env/cache will be used.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.2,
        help="Delay between probes to avoid hammering the endpoint, default: 0.2.",
    )
    return parser.parse_args()


def probe_date(client: ElecheckClient, area_code: str, probe_day: date) -> ProbeResult:
    day_text = probe_day.isoformat()
    rows = client.fetch_clear_price_detail(
        area_code=area_code,
        start_date=day_text,
        end_date=day_text,
    )
    useful_rows = [
        row
        for row in rows
        if row.get("avgDayAheadPrice") not in (None, "")
        or row.get("avgRealTimePrice") not in (None, "")
    ]
    return ProbeResult(
        probe_date=probe_day,
        available=bool(useful_rows),
        row_count=len(useful_rows),
    )


def print_probe(index: int, result: ProbeResult) -> None:
    status = "OK" if result.available else "EMPTY"
    print(f"[{index:02d}] {result.probe_date.isoformat()} -> {status} ({result.row_count} rows)")


def find_latest_available_on_or_before(
    client: ElecheckClient,
    area_code: str,
    end_date: date,
    lower_bound: date,
    sleep_seconds: float,
) -> tuple[date, int]:
    probe_count = 0
    current = end_date
    while current >= lower_bound:
        probe_count += 1
        result = probe_date(client, area_code, current)
        print_probe(probe_count, result)
        if result.available:
            return current, probe_count
        current -= timedelta(days=1)
        sleep(sleep_seconds)
    raise RuntimeError(f"No available data found between {lower_bound} and {end_date}.")


def find_earliest_available(
    client: ElecheckClient,
    area_code: str,
    known_available: date,
    lower_bound: date,
    sleep_seconds: float,
    probe_count: int,
) -> tuple[date, int]:
    step_days = 1
    high = known_available
    low_invalid = lower_bound - timedelta(days=1)

    while True:
        candidate = high - timedelta(days=step_days)
        if candidate < lower_bound:
            candidate = lower_bound

        probe_count += 1
        result = probe_date(client, area_code, candidate)
        print_probe(probe_count, result)
        sleep(sleep_seconds)

        if result.available:
            high = candidate
            if candidate == lower_bound:
                return candidate, probe_count
            step_days *= 2
            continue

        low_invalid = candidate
        break

    while (high - low_invalid).days > 1:
        midpoint = low_invalid + timedelta(days=(high - low_invalid).days // 2)
        probe_count += 1
        result = probe_date(client, area_code, midpoint)
        print_probe(probe_count, result)
        sleep(sleep_seconds)

        if result.available:
            high = midpoint
        else:
            low_invalid = midpoint

    return high, probe_count


def main() -> int:
    args = parse_args()
    area_code = args.area_code or resolve_elecheck_area_code(args.area)
    end_date = date.fromisoformat(args.end_date)
    lower_bound = date.fromisoformat(args.lower_bound)
    if lower_bound > end_date:
        raise ValueError("--lower-bound must be before or equal to --end-date.")

    authorization = resolve_elecheck_authorization(args.authorization)
    if args.authorization and authorization:
        cache_elecheck_authorization(authorization)

    client = ElecheckClient(authorization=authorization)
    try:
        known_available, probe_count = find_latest_available_on_or_before(
            client=client,
            area_code=area_code,
            end_date=end_date,
            lower_bound=lower_bound,
            sleep_seconds=args.sleep_seconds,
        )
        earliest, probe_count = find_earliest_available(
            client=client,
            area_code=area_code,
            known_available=known_available,
            lower_bound=lower_bound,
            sleep_seconds=args.sleep_seconds,
            probe_count=probe_count,
        )
    except ElecheckUnauthorizedError:
        print("Elecheck returned 401. Provide --authorization or refresh the cached token.")
        return 2
    finally:
        client.close()

    print()
    print(f"area_code: {area_code}")
    print(f"earliest_available_date: {earliest.isoformat()}")
    print(f"probes: {probe_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
