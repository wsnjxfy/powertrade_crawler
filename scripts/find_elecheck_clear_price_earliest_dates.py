from __future__ import annotations

import argparse
from datetime import date
from time import sleep

from powertrade_crawler.clients.elecheck import ElecheckClient, ElecheckUnauthorizedError
from powertrade_crawler.elecheck_auth import (
    cache_elecheck_authorization,
    resolve_elecheck_authorization,
)
from powertrade_crawler.storage import (
    ElecheckAreaRecordRow,
    get_session,
    init_db,
    update_elecheck_area_earliest_clear_price_date,
)

from find_elecheck_clear_price_earliest_date import (
    find_earliest_available,
    find_latest_available_on_or_before,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Find earliest available Elecheck clear price dates for all areas and "
            "write them into elecheck_area_records.earliest_clear_price_date."
        ),
    )
    parser.add_argument(
        "--end-date",
        default=date.today().isoformat(),
        help="Known/latest date to start probing from, default: today.",
    )
    parser.add_argument(
        "--lower-bound",
        default="2020-01-01",
        help="Earliest date the search is allowed to test, default: 2020-01-01.",
    )
    parser.add_argument(
        "--authorization",
        help="Raw Elecheck authorization JWT. If omitted, .auth/credentials.json is used.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.2,
        help="Delay between probes to avoid hammering the endpoint, default: 0.2.",
    )
    parser.add_argument(
        "--between-areas-sleep-seconds",
        type=float,
        default=1.0,
        help="Delay between areas, default: 1.0.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip areas that already have earliest_clear_price_date.",
    )
    parser.add_argument(
        "--area",
        action="append",
        default=[],
        help="Limit to one or more area names. Can be passed multiple times.",
    )
    parser.add_argument(
        "--area-code",
        action="append",
        default=[],
        help="Limit to one or more area codes. Can be passed multiple times.",
    )
    return parser.parse_args()


def load_areas(
    *,
    area_names: list[str],
    area_codes: list[str],
    skip_existing: bool,
) -> list[ElecheckAreaRecordRow]:
    with get_session() as session:
        query = session.query(ElecheckAreaRecordRow).order_by(ElecheckAreaRecordRow.id)
        if area_names:
            query = query.filter(ElecheckAreaRecordRow.area_name.in_(area_names))
        if area_codes:
            query = query.filter(ElecheckAreaRecordRow.area_code.in_(area_codes))
        if skip_existing:
            query = query.filter(ElecheckAreaRecordRow.earliest_clear_price_date.is_(None))
        rows = query.all()
        return [
            ElecheckAreaRecordRow(
                area_name=row.area_name,
                area_code=row.area_code,
                earliest_clear_price_date=row.earliest_clear_price_date,
            )
            for row in rows
        ]


def main() -> int:
    args = parse_args()
    end_date = date.fromisoformat(args.end_date)
    lower_bound = date.fromisoformat(args.lower_bound)
    if lower_bound > end_date:
        raise ValueError("--lower-bound must be before or equal to --end-date.")

    init_db()
    areas = load_areas(
        area_names=args.area,
        area_codes=args.area_code,
        skip_existing=args.skip_existing,
    )
    if not areas:
        print("No areas to probe.")
        return 0

    authorization = resolve_elecheck_authorization(args.authorization)
    if args.authorization and authorization:
        cache_elecheck_authorization(authorization)

    total_probes = 0
    failures: list[tuple[str, str, str]] = []
    client = ElecheckClient(authorization=authorization)
    try:
        for index, area in enumerate(areas, start=1):
            print()
            print(f"=== [{index}/{len(areas)}] {area.area_name} {area.area_code} ===")
            try:
                known_available, probe_count = find_latest_available_on_or_before(
                    client=client,
                    area_code=area.area_code,
                    end_date=end_date,
                    lower_bound=lower_bound,
                    sleep_seconds=args.sleep_seconds,
                )
                earliest, probe_count = find_earliest_available(
                    client=client,
                    area_code=area.area_code,
                    known_available=known_available,
                    lower_bound=lower_bound,
                    sleep_seconds=args.sleep_seconds,
                    probe_count=probe_count,
                )
            except ElecheckUnauthorizedError:
                raise
            except Exception as exc:
                failures.append((area.area_name, area.area_code, str(exc)))
                print(f"FAILED: {exc}")
            else:
                update_elecheck_area_earliest_clear_price_date(area.area_code, earliest)
                total_probes += probe_count
                print(f"UPDATED: {area.area_name} {area.area_code} -> {earliest.isoformat()}")

            if index < len(areas):
                sleep(args.between_areas_sleep_seconds)
    except ElecheckUnauthorizedError:
        print("Elecheck returned 401. Provide --authorization or refresh the cached token.")
        return 2
    finally:
        client.close()

    print()
    print("Batch finished.")
    print(f"areas_total: {len(areas)}")
    print(f"areas_failed: {len(failures)}")
    print(f"probes_total: {total_probes}")
    if failures:
        print()
        print("Failures:")
        for area_name, area_code, message in failures:
            print(f"- {area_name} {area_code}: {message}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
