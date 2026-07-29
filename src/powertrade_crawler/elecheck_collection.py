from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from powertrade_crawler.clients.elecheck import ElecheckClient
from powertrade_crawler.elecheck_auth import (
    resolve_elecheck_authorization,
    set_elecheck_authorization_for_current_process,
)
from powertrade_crawler.spiders.elecheck import ElecheckClearPriceSpider
from powertrade_crawler.storage import upsert_elecheck_clear_price_records


ProgressCallback = Callable[[dict[str, Any]], None]


def clear_price_latest_daily_dates_by_area(db_path: Path) -> dict[str, str]:
    if not _table_exists(db_path, "elecheck_clear_price_records"):
        return {}
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT area_code, MAX(start_date) AS latest_date
            FROM elecheck_clear_price_records
            WHERE start_date = end_date
            GROUP BY area_code
            """
        ).fetchall()
    return {
        str(area_code): str(latest_date)
        for area_code, latest_date in rows
        if area_code and latest_date
    }


def clear_price_area_targets(db_path: Path) -> list[dict[str, str | None]]:
    if not _table_exists(db_path, "elecheck_area_records"):
        return []
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT area_name, area_code, earliest_clear_price_date
            FROM elecheck_area_records
            ORDER BY id
            """
        ).fetchall()
    return [
        {
            "area_name": str(area_name),
            "area_code": str(area_code),
            "earliest_clear_price_date": (
                str(earliest_clear_price_date)
                if earliest_clear_price_date
                else None
            ),
        }
        for area_name, area_code, earliest_clear_price_date in rows
    ]


def build_clear_price_full_coverage_targets(
    db_path: Path,
    end_date: str,
    *,
    latest_dates: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    parsed_end_date = date.fromisoformat(end_date)
    areas = clear_price_area_targets(db_path)
    if not areas:
        raise ValueError("没有可采集的地区。请先运行 powertrade init-db。")

    if latest_dates is None:
        latest_dates = clear_price_latest_daily_dates_by_area(db_path)
    targets: list[dict[str, str]] = []
    missing_earliest = []
    skipped_after_end = []
    for area in areas:
        area_name = str(area["area_name"])
        area_code = str(area["area_code"])
        earliest_date = area["earliest_clear_price_date"]
        if not earliest_date:
            missing_earliest.append(f"{area_name}({area_code})")
            continue
        if area_code in latest_dates:
            target_start = date.fromisoformat(latest_dates[area_code]) - timedelta(
                days=1
            )
            earliest = date.fromisoformat(str(earliest_date))
            if target_start < earliest:
                target_start = earliest
        else:
            target_start = date.fromisoformat(str(earliest_date))
        if target_start > parsed_end_date:
            skipped_after_end.append(f"{area_name}({area_code})")
            continue
        targets.append(
            {
                "area_name": area_name,
                "area_code": area_code,
                "start_date": target_start.isoformat(),
                "end_date": parsed_end_date.isoformat(),
            }
        )

    if missing_earliest:
        preview = "、".join(missing_earliest[:8])
        suffix = "..." if len(missing_earliest) > 8 else ""
        raise ValueError(f"以下地区缺少最早可用日期，无法全覆盖采集：{preview}{suffix}")
    if not targets:
        preview = "、".join(skipped_after_end[:8])
        suffix = "..." if len(skipped_after_end) > 8 else ""
        raise ValueError(
            f"没有可采集的地区。以下地区最早可用日期晚于结束日期：{preview}{suffix}"
        )
    return targets


def collect_clear_price_targets(
    targets: list[dict[str, str]],
    *,
    is_cancelled: Callable[[], bool] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    if not targets:
        raise ValueError("没有可执行的 Elecheck 现货采集目标。")
    cancelled = is_cancelled or (lambda: False)
    report = progress_callback or (lambda _detail: None)
    resolved_authorization = resolve_elecheck_authorization("")
    if resolved_authorization:
        set_elecheck_authorization_for_current_process(resolved_authorization)
    client = ElecheckClient(authorization=resolved_authorization)
    try:
        area_jobs = []
        for target in targets:
            spider = ElecheckClearPriceSpider(
                client=client,
                area_code=target["area_code"],
                start_date=target["start_date"],
                end_date=target["end_date"],
                daily=True,
            )
            area_jobs.append((target, spider, list(spider.iter_request_date_ranges())))

        total_requests = sum(
            len(request_ranges)
            for _target, _spider, request_ranges in area_jobs
        )
        completed_requests = 0
        completed_areas = 0
        records_produced = 0
        records_upserted = 0
        report(
            {
                "phase": "planned",
                "message": (
                    f"准备更新 {len(area_jobs)} 个地区，共 {total_requests} 个逐日请求"
                ),
                "area_count": len(area_jobs),
                "completed_area_count": 0,
                "total_requests": total_requests,
                "completed_requests": 0,
                "records_produced": 0,
                "records_upserted": 0,
                "percent": 0.0,
            }
        )

        stopped_early = False
        for area_index, (target, spider, request_ranges) in enumerate(
            area_jobs,
            start=1,
        ):
            if cancelled():
                stopped_early = True
                break
            area_completed = True
            for request_start_date, request_end_date in request_ranges:
                if cancelled():
                    stopped_early = True
                    area_completed = False
                    break
                date_label = _format_date_range(
                    request_start_date.isoformat(),
                    request_end_date.isoformat(),
                )
                report(
                    {
                        "phase": "downloading",
                        "message": f"正在下载 {target['area_name']} {date_label}",
                        "area_name": target["area_name"],
                        "area_code": target["area_code"],
                        "area_index": area_index,
                        "area_count": len(area_jobs),
                        "request_start_date": request_start_date.isoformat(),
                        "request_end_date": request_end_date.isoformat(),
                        "total_requests": total_requests,
                        "completed_requests": completed_requests,
                        "records_produced": records_produced,
                        "records_upserted": records_upserted,
                        "percent": (
                            completed_requests / total_requests * 100
                            if total_requests
                            else 0.0
                        ),
                    }
                )
                records = list(
                    spider.crawl_date_range(
                        request_start_date=request_start_date,
                        request_end_date=request_end_date,
                    )
                )
                records_produced += len(records)
                records_upserted += upsert_elecheck_clear_price_records(records)
                completed_requests += 1
                report(
                    {
                        "phase": "saved",
                        "message": f"已保存 {target['area_name']} {date_label}",
                        "area_name": target["area_name"],
                        "area_code": target["area_code"],
                        "area_index": area_index,
                        "area_count": len(area_jobs),
                        "request_start_date": request_start_date.isoformat(),
                        "request_end_date": request_end_date.isoformat(),
                        "total_requests": total_requests,
                        "completed_requests": completed_requests,
                        "records_produced": records_produced,
                        "records_upserted": records_upserted,
                        "percent": (
                            completed_requests / total_requests * 100
                            if total_requests
                            else 100.0
                        ),
                    }
                )
            if area_completed:
                completed_areas += 1

        report(
            {
                "phase": "stopped" if stopped_early else "completed",
                "message": (
                    "已结束并保存已下载数据"
                    if stopped_early
                    else "全部地区现货数据更新完成"
                ),
                "area_count": len(area_jobs),
                "completed_area_count": completed_areas,
                "total_requests": total_requests,
                "completed_requests": completed_requests,
                "records_produced": records_produced,
                "records_upserted": records_upserted,
                "percent": (
                    completed_requests / total_requests * 100
                    if total_requests
                    else 100.0
                ),
            }
        )
        return {
            "target_area_count": len(area_jobs),
            "completed_area_count": completed_areas,
            "planned_daily_requests": total_requests,
            "completed_daily_requests": completed_requests,
            "records_produced": records_produced,
            "records_upserted": records_upserted,
            "stopped_early": stopped_early,
        }
    finally:
        client.close()


def _table_exists(db_path: Path, table_name: str) -> bool:
    if not db_path.exists():
        return False
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
    return row is not None


def _format_date_range(start_date: str, end_date: str) -> str:
    return start_date if start_date == end_date else f"{start_date} 至 {end_date}"
