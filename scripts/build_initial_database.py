from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import text

from powertrade_crawler.config import get_settings
from powertrade_crawler.metrics import rebuild_dashboard_daily_metrics
from powertrade_crawler.storage import get_engine, init_db


DEFAULT_SOURCE = Path("data/powertrade.db")
DEFAULT_OUTPUT = Path("resources/initial/powertrade.initial.db")
DEFAULT_MANIFEST = Path("resources/initial/powertrade.initial.manifest.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a small, secret-free demo database for the packaged application."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def quoted_columns(connection: sqlite3.Connection, table_name: str) -> list[str]:
    columns = [
        str(row[1])
        for row in connection.execute(f'PRAGMA main.table_info("{table_name}")')
        if str(row[1]) != "id"
    ]
    if not columns:
        raise RuntimeError(f"Initial database table is missing: {table_name}")
    return columns


def copy_rows(
    connection: sqlite3.Connection,
    table_name: str,
    *,
    where_sql: str = "1=1",
) -> int:
    columns = quoted_columns(connection, table_name)
    column_sql = ", ".join(f'"{column}"' for column in columns)
    before = connection.total_changes
    connection.execute(
        f'INSERT OR IGNORE INTO main."{table_name}" ({column_sql}) '
        f'SELECT {column_sql} FROM source."{table_name}" WHERE {where_sql}'
    )
    return connection.total_changes - before


def create_schema(output_path: Path) -> None:
    os.environ["DATABASE_URL"] = f"sqlite:///{output_path.resolve().as_posix()}"
    get_settings.cache_clear()
    init_db()


def copy_demo_rows(source_path: Path, output_path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with sqlite3.connect(output_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("ATTACH DATABASE ? AS source", (str(source_path.resolve()),))

        counts["market_records"] = copy_rows(
            connection,
            "market_records",
            where_sql="source = 'ENTSO-E Transparency Platform'",
        )
        counts["entsoe_records"] = copy_rows(connection, "entsoe_records")
        counts["elexon_records"] = copy_rows(connection, "elexon_records")
        counts["gridstatus_dataset_metadata"] = copy_rows(
            connection,
            "gridstatus_dataset_metadata",
        )
        counts["gridstatus_records"] = copy_rows(
            connection,
            "gridstatus_records",
            where_sql="""
                id IN (
                    SELECT id
                    FROM (
                        SELECT id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY request_name
                                   ORDER BY COALESCE(record_time_utc, interval_start_utc) DESC,
                                            id DESC
                               ) AS sample_rank
                        FROM source.gridstatus_records
                        WHERE request_name <> 'gridstatus_datasets'
                    )
                    WHERE sample_rank <= 500
                )
            """,
        )
        counts["gzpec_news_records"] = copy_rows(connection, "gzpec_news_records")
        counts["elecheck_clear_price_records"] = copy_rows(
            connection,
            "elecheck_clear_price_records",
            where_sql="""
                (
                    area_code = '320000000000'
                    AND start_date = end_date
                    AND start_date IN (
                        SELECT start_date
                        FROM source.elecheck_clear_price_records
                        WHERE area_code = '320000000000' AND start_date = end_date
                        GROUP BY start_date
                        ORDER BY start_date DESC
                        LIMIT 3
                    )
                )
                OR (
                    area_code IN ('140000000000', '330000000000')
                    AND start_date = end_date
                    AND start_date = (
                        SELECT MAX(latest.start_date)
                        FROM source.elecheck_clear_price_records AS latest
                        WHERE latest.area_code = source.elecheck_clear_price_records.area_code
                              AND latest.start_date = latest.end_date
                    )
                )
            """,
        )
        counts["elecheck_purchasing_records"] = copy_rows(
            connection,
            "elecheck_purchasing_records",
            where_sql="""
                data_month IN (
                    SELECT data_month
                    FROM source.elecheck_purchasing_records
                    GROUP BY data_month
                    ORDER BY data_month DESC
                    LIMIT 3
                )
            """,
        )
        counts["elecheck_purchasing_area_records"] = copy_rows(
            connection,
            "elecheck_purchasing_area_records",
        )
        counts["elecheck_mechanism_electricity_price_records"] = copy_rows(
            connection,
            "elecheck_mechanism_electricity_price_records",
        )
        connection.commit()
        connection.execute("DETACH DATABASE source")
    return counts


def database_table_counts() -> dict[str, int]:
    tables = [
        "market_records",
        "entsoe_records",
        "elexon_records",
        "gridstatus_records",
        "gridstatus_dataset_metadata",
        "gzpec_news_records",
        "elecheck_clear_price_records",
        "elecheck_area_records",
        "elecheck_purchasing_records",
        "elecheck_purchasing_area_records",
        "elecheck_mechanism_electricity_price_records",
        "dashboard_daily_metrics",
    ]
    with get_engine().connect() as connection:
        return {
            table_name: int(
                connection.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one()
            )
            for table_name in tables
        }


def finalize_database(output_path: Path) -> None:
    rebuild_dashboard_daily_metrics(date(2015, 1, 1), date(2030, 1, 1))
    with sqlite3.connect(output_path) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("ANALYZE")
        connection.execute("VACUUM")


def write_manifest(
    manifest_path: Path,
    output_path: Path,
    table_counts: dict[str, int],
) -> None:
    source_counts = {
        "entsoe": table_counts["market_records"] + table_counts["entsoe_records"],
        "elexon": table_counts["elexon_records"],
        "gridstatus": table_counts["gridstatus_records"],
        "gridstatus_dataset_catalog": table_counts["gridstatus_dataset_metadata"],
        "elecheck": sum(
            table_counts[name]
            for name in (
                "elecheck_clear_price_records",
                "elecheck_area_records",
                "elecheck_purchasing_records",
                "elecheck_purchasing_area_records",
                "elecheck_mechanism_electricity_price_records",
            )
        ),
        "gzpec": table_counts["gzpec_news_records"],
    }
    payload = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "purpose": "Lightweight packaged demo database",
        "database_file": output_path.name,
        "database_bytes": output_path.stat().st_size,
        "contains_credentials": False,
        "source_counts": source_counts,
        "table_counts": table_counts,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    if args.output.exists():
        if not args.force:
            raise FileExistsError(f"Output already exists: {args.output}; pass --force")
        args.output.unlink()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    create_schema(args.output)
    copied = copy_demo_rows(args.source, args.output)
    finalize_database(args.output)
    table_counts = database_table_counts()
    write_manifest(args.manifest, args.output, table_counts)
    print(json.dumps({"copied": copied, "final": table_counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
