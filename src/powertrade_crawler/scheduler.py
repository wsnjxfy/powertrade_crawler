from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from typing import BinaryIO, Iterator

from sqlalchemy import desc
from sqlalchemy.exc import IntegrityError

from powertrade_crawler.config import get_settings
from powertrade_crawler.credentials import get_project_root
from powertrade_crawler.elecheck_collection import (
    clear_price_area_targets,
    clear_price_latest_daily_dates_by_area,
)
from powertrade_crawler.metrics import rebuild_dashboard_daily_metrics, run_database_maintenance
from powertrade_crawler.network_errors import NetworkFailure
from powertrade_crawler.models import (
    ElecheckClearPriceRecord,
    ElecheckMechanismElectricityPriceRecord,
    ElecheckPurchasingProvinceRecord,
    ElecheckPurchasingRecord,
    ElexonRecord,
    EntsoeRecord,
    GridStatusDatasetMetadataRecord,
    GridStatusRecord,
    GzpecNewsRecord,
    MarketRecord,
)
from powertrade_crawler.registry import get_spider, list_spiders
from powertrade_crawler.spiders.elexon import get_elexon_request_config
from powertrade_crawler.spiders.entsoe import ENTSOE_BIDDING_ZONES
from powertrade_crawler.storage import (
    ScheduledJobRow,
    ScheduledJobRunRow,
    get_session,
    upsert_elexon_records,
    upsert_entsoe_records,
    upsert_elecheck_clear_price_records,
    upsert_elecheck_mechanism_electricity_price_records,
    upsert_elecheck_purchasing_province_records,
    upsert_elecheck_purchasing_records,
    upsert_gridstatus_dataset_metadata_records,
    upsert_gridstatus_records,
    upsert_gzpec_news_records,
    upsert_records,
)


VALID_JOB_TYPES = {"crawl", "source_update", "metrics", "maintenance"}
VALID_DATE_MODES = {"none", "yesterday", "last-7-days", "last-30-days", "custom"}
VALID_SCHEDULE_KINDS = {"daily", "weekly", "monthly"}
SOURCE_UPDATE_SOURCES = {"elecheck", "entsoe", "elexon", "gridstatus", "gzpec"}
SOURCE_UPDATE_LABELS = {
    "elecheck": "Elecheck",
    "entsoe": "ENTSO-E",
    "elexon": "Elexon",
    "gridstatus": "GridStatus",
    "gzpec": "广州电力交易中心",
}
SOURCE_UPDATE_DESCRIPTIONS = {
    "elecheck": "各地区现货增量、近两月代理购电、增量机制电价",
    "entsoe": "指定竞价区的日前价格、实际负荷、按类型实际发电",
    "elexon": "系统价格、需求、风电预测、燃料发电、互联线潮流",
    "gridstatus": "数据集目录及 CAISO、PJM、NYISO 当前数据",
    "gzpec": "公开信息列表与文章正文更新",
}
DATE_SEMANTICS_EXCLUSIVE = "exclusive"
DATE_SEMANTICS_INCLUSIVE_DAILY = "inclusive-daily"
DATE_SEMANTICS_NONE = "none"


class ScheduledJobAlreadyRunning(RuntimeError):
    pass


def create_scheduled_job(
    *,
    name: str,
    job_type: str,
    spider_name: str | None = None,
    schedule_kind: str = "daily",
    schedule_time: str = "02:00",
    date_mode: str = "none",
    start_date: date | None = None,
    end_date: date | None = None,
    enabled: bool = True,
    params: dict[str, Any] | None = None,
) -> ScheduledJobRow:
    validate_job_fields(
        name=name,
        job_type=job_type,
        spider_name=spider_name,
        schedule_kind=schedule_kind,
        schedule_time=schedule_time,
        date_mode=date_mode,
        start_date=start_date,
        end_date=end_date,
        params=params,
    )
    now = utc_now_naive()
    with get_session() as session:
        row = ScheduledJobRow(
            name=name.strip(),
            job_type=job_type,
            spider_name=spider_name.strip() if spider_name else None,
            schedule_kind=schedule_kind,
            schedule_time=schedule_time,
            date_mode=date_mode,
            start_date=start_date,
            end_date=end_date,
            enabled=enabled,
            params_json=json.dumps(params or {}, ensure_ascii=False),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise ValueError(f"Scheduled job name already exists: {name.strip()}") from exc
        session.refresh(row)
        return detach_job(row)


def create_source_update_job(
    *,
    source: str,
    schedule_time: str = "09:00",
    area: str | None = None,
    name: str | None = None,
    schedule_kind: str = "daily",
    enabled: bool = True,
    max_elecheck_days: int = 7,
) -> ScheduledJobRow:
    """Create one safe, source-level incremental update job."""
    normalized_source = source.strip().lower()
    if normalized_source not in SOURCE_UPDATE_SOURCES:
        available = ", ".join(sorted(SOURCE_UPDATE_SOURCES))
        raise ValueError(f"Unknown source update preset: {source}. Available: {available}.")
    params: dict[str, Any] = {}
    if normalized_source == "entsoe":
        params["area"] = (area or "DE-LU").strip().upper()
    elif normalized_source == "elecheck":
        if area and area.strip() not in {"全部", "all", "ALL", "*"}:
            params["area"] = area.strip()
        params["max_days"] = max_elecheck_days
    elif area:
        raise ValueError(f"{SOURCE_UPDATE_LABELS[normalized_source]} source updates do not use area.")
    display_name = name or (
        f"{SOURCE_UPDATE_LABELS[normalized_source]} 每日增量更新 {schedule_time}"
    )
    return create_scheduled_job(
        name=display_name,
        job_type="source_update",
        spider_name=normalized_source,
        schedule_kind=schedule_kind,
        schedule_time=schedule_time,
        date_mode="none",
        enabled=enabled,
        params=params,
    )


def list_elecheck_source_update_areas() -> list[str]:
    return [
        str(area["area_name"])
        for area in clear_price_area_targets(scheduler_sqlite_path())
        if area.get("area_name")
    ]


def create_default_job_templates() -> int:
    templates = [
        {
            "name": "模板：每日 Elecheck 增量更新",
            "job_type": "source_update",
            "spider_name": "elecheck",
            "schedule_kind": "daily",
            "schedule_time": "09:00",
            "date_mode": "none",
            "enabled": False,
            "params": {"max_days": 7},
        },
        {
            "name": "模板：每日 ENTSO-E 德国核心数据更新",
            "job_type": "source_update",
            "spider_name": "entsoe",
            "schedule_kind": "daily",
            "schedule_time": "09:05",
            "date_mode": "none",
            "enabled": False,
            "params": {"area": "DE-LU"},
        },
        {
            "name": "模板：每日 Elexon 核心数据更新",
            "job_type": "source_update",
            "spider_name": "elexon",
            "schedule_kind": "daily",
            "schedule_time": "09:10",
            "date_mode": "none",
            "enabled": False,
            "params": {},
        },
        {
            "name": "模板：每日 GridStatus 核心数据更新",
            "job_type": "source_update",
            "spider_name": "gridstatus",
            "schedule_kind": "daily",
            "schedule_time": "09:15",
            "date_mode": "none",
            "enabled": False,
            "params": {},
        },
        {
            "name": "模板：每日广州交易中心公开信息更新",
            "job_type": "source_update",
            "spider_name": "gzpec",
            "schedule_kind": "daily",
            "schedule_time": "09:20",
            "date_mode": "none",
            "enabled": False,
            "params": {},
        },
        {
            "name": "模板：每日重建最近30天指标",
            "job_type": "metrics",
            "schedule_kind": "daily",
            "schedule_time": "02:30",
            "date_mode": "last-30-days",
            "enabled": False,
            "params": {},
        },
        {
            "name": "模板：每周数据库维护",
            "job_type": "maintenance",
            "schedule_kind": "weekly",
            "schedule_time": "03:30",
            "date_mode": "none",
            "enabled": False,
            "params": {"analyze": True, "vacuum": True},
        },
        {
            "name": "模板：ENTSO-E 昨日德国负荷",
            "job_type": "crawl",
            "spider_name": "entsoe_actual_total_load",
            "schedule_kind": "daily",
            "schedule_time": "02:00",
            "date_mode": "yesterday",
            "enabled": False,
            "params": {"area": "DE-LU"},
        },
        {
            "name": "模板：Elexon 最近7天负荷补采",
            "job_type": "crawl",
            "spider_name": "elexon_initial_demand_outturn",
            "schedule_kind": "daily",
            "schedule_time": "02:10",
            "date_mode": "last-7-days",
            "enabled": False,
            "params": {},
        },
    ]
    created = 0
    with get_session() as session:
        existing_names = {row.name for row in session.query(ScheduledJobRow.name).all()}
    for template in templates:
        if template["name"] in existing_names:
            continue
        create_scheduled_job(**template)
        created += 1
    return created


def list_scheduled_jobs() -> list[ScheduledJobRow]:
    with get_session() as session:
        rows = session.query(ScheduledJobRow).order_by(ScheduledJobRow.id).all()
        return [detach_job(row) for row in rows]


def get_scheduled_job(job_id: int) -> ScheduledJobRow:
    with get_session() as session:
        row = session.get(ScheduledJobRow, job_id)
        if row is None:
            raise ValueError(f"Unknown scheduled job id: {job_id}")
        return detach_job(row)


def set_scheduled_job_enabled(job_id: int, enabled: bool) -> ScheduledJobRow:
    with get_session() as session:
        row = session.get(ScheduledJobRow, job_id)
        if row is None:
            raise ValueError(f"Unknown scheduled job id: {job_id}")
        row.enabled = enabled
        row.updated_at = utc_now_naive()
        session.commit()
        session.refresh(row)
        return detach_job(row)


def delete_scheduled_job(job_id: int, *, remove_windows_task: bool = False) -> None:
    job = get_scheduled_job(job_id)
    if job.windows_task_name:
        if not remove_windows_task:
            raise ValueError(
                "This job is installed in Windows Task Scheduler. "
                "Uninstall the Windows task before deleting the local job."
            )
        try:
            uninstall_windows_task(job_id)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                "Could not remove the Windows scheduled task; "
                "the local job definition was kept."
            ) from exc

    with get_session() as session:
        row = session.get(ScheduledJobRow, job_id)
        if row is None:
            raise ValueError(f"Unknown scheduled job id: {job_id}")
        session.delete(row)
        session.commit()


def list_recent_job_runs(limit: int = 50) -> list[ScheduledJobRunRow]:
    with get_session() as session:
        rows = (
            session.query(ScheduledJobRunRow)
            .order_by(desc(ScheduledJobRunRow.started_at), desc(ScheduledJobRunRow.id))
            .limit(limit)
            .all()
        )
        return [detach_run(row) for row in rows]


def run_scheduled_job(job_id: int, *, force: bool = False) -> dict[str, Any]:
    try:
        with scheduled_job_lock(job_id):
            return _run_scheduled_job_locked(job_id, force=force)
    except ScheduledJobAlreadyRunning:
        started_at = utc_now_naive()
        run_id = create_job_run(job_id=job_id, started_at=started_at)
        result = {
            "status": "skipped",
            "message": "同一个定时任务正在运行，本次触发已跳过，未重复采集或修改业务数据。",
            "records_written": 0,
            "output": {
                "skip_reason": "already_running",
                "data_modified": False,
            },
        }
        finish_job_run(
            run_id=run_id,
            status=result["status"],
            message=result["message"],
            records_written=0,
            output=result["output"],
        )
        return {"job_id": job_id, "run_id": run_id, **result}


def _run_scheduled_job_locked(job_id: int, *, force: bool) -> dict[str, Any]:
    job = get_scheduled_job(job_id)
    started_at = utc_now_naive()
    run_id = create_job_run(job_id=job.id, started_at=started_at)
    try:
        if not job.enabled and not force:
            result = {
                "status": "skipped",
                "message": "Job is disabled.",
                "records_written": 0,
                "output": {},
            }
        else:
            result = execute_job(job)
    except Exception as exc:
        result = {
            "status": "failed",
            "message": sanitize_message(str(exc)),
            "records_written": 0,
            "output": failure_output(exc, data_modified=False),
        }
    finish_job_run(
        run_id=run_id,
        status=result["status"],
        message=result["message"],
        records_written=result.get("records_written"),
        output=result.get("output") or {},
    )
    return {"job_id": job.id, "run_id": run_id, **result}


@contextmanager
def scheduled_job_lock(job_id: int) -> Iterator[None]:
    """Use an OS file lock so separate GUI/Task Scheduler processes cannot overlap."""
    path = scheduler_lock_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        ensure_lock_byte(handle)
        try:
            acquire_file_lock(handle)
        except OSError as exc:
            raise ScheduledJobAlreadyRunning(
                f"Scheduled job {job_id} is already running."
            ) from exc
        try:
            yield
        finally:
            release_file_lock(handle)
    finally:
        handle.close()


def scheduler_lock_path(job_id: int) -> Path:
    return scheduler_sqlite_path().parent / ".scheduler_locks" / f"job_{job_id}.lock"


def ensure_lock_byte(handle: BinaryIO) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)


def acquire_file_lock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def release_file_lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def scheduled_run_exit_code(status: str) -> int:
    return 1 if status in {"failed", "partial"} else 0


def execute_job(job: ScheduledJobRow) -> dict[str, Any]:
    start, end = resolve_job_date_window(job)
    params = parse_params_json(job.params_json)
    if job.job_type == "source_update":
        if not job.spider_name:
            raise ValueError("source_update jobs require a source name.")
        return execute_source_update(job.spider_name, params=params)
    if job.job_type == "metrics":
        if start is None or end is None:
            start, end = date.today() - timedelta(days=30), date.today()
        count = rebuild_dashboard_daily_metrics(start, end)
        return {
            "status": "success",
            "message": f"Rebuilt {count} dashboard daily metrics.",
            "records_written": count,
            "output": {"start_date": start.isoformat(), "end_date": end.isoformat()},
        }
    if job.job_type == "maintenance":
        statements = run_database_maintenance(
            analyze=bool(params.get("analyze", True)),
            vacuum=bool(params.get("vacuum", False)),
        )
        return {
            "status": "success",
            "message": "Completed database maintenance: " + ", ".join(statements),
            "records_written": len(statements),
            "output": {"statements": statements},
        }
    if job.job_type == "crawl":
        if not job.spider_name:
            raise ValueError("crawl jobs require spider_name.")
        spider_kwargs = build_scheduled_spider_kwargs(
            job.spider_name,
            params=params,
            start=start,
            end=end,
        )
        written, produced = run_spider_and_upsert(job.spider_name, spider_kwargs)
        if produced == 0:
            return {
                "status": "no_data",
                "message": (
                    f"{job.spider_name} 采集请求已完成，但所选范围没有返回记录；"
                    "这不是采集失败，本地业务数据未修改。"
                ),
                "records_written": 0,
                "output": {
                    "produced": 0,
                    "spider_name": job.spider_name,
                    "data_modified": False,
                },
            }
        return {
            "status": "success",
            "message": f"{job.spider_name} produced {produced} records and upserted {written}.",
            "records_written": written,
            "output": {
                "produced": produced,
                "spider_name": job.spider_name,
                "data_modified": written > 0,
            },
        }
    raise ValueError(f"Unsupported job type: {job.job_type}")


def execute_source_update(
    source: str,
    *,
    params: dict[str, Any] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Execute every step in a source preset and preserve partial-failure detail."""
    plan = build_source_update_plan(source, params=params or {}, today=today)
    steps: list[dict[str, Any]] = []
    records_written = 0
    failed = 0
    no_data = 0
    for item in plan:
        try:
            written, produced = run_spider_and_upsert(item["spider_name"], item["kwargs"])
            records_written += written
            step_status = "success" if produced else "no_data"
            if not produced:
                no_data += 1
            steps.append(
                {
                    "label": item["label"],
                    "spider_name": item["spider_name"],
                    "status": step_status,
                    "produced": produced,
                    "records_written": written,
                    "data_modified": written > 0,
                }
            )
        except Exception as exc:
            failed += 1
            steps.append({
                "label": item["label"],
                "spider_name": item["spider_name"],
                "status": "failed",
                "produced": 0,
                "records_written": 0,
                "error": sanitize_message(str(exc)),
                **failure_output(exc, data_modified=False),
            })

    label = SOURCE_UPDATE_LABELS[source.strip().lower()]
    succeeded = len(steps) - failed - no_data
    if failed == 0:
        if succeeded:
            status = "success"
            summary = (
                f"{label} 更新完成：{succeeded} 个步骤写入数据，"
                f"{no_data} 个步骤没有新数据"
            )
        else:
            status = "no_data"
            summary = f"{label} 更新完成：{no_data} 个步骤均没有新数据"
    elif succeeded or no_data:
        status = "partial"
        summary = (
            f"{label} 部分更新：{succeeded} 个步骤写入数据，"
            f"{no_data} 个没有新数据，{failed} 个失败"
        )
    else:
        status = "failed"
        summary = f"{label} 更新失败：{failed}/{len(steps)} 个步骤失败"
    return {
        "status": status,
        "message": f"{summary}，写入或更新 {records_written} 条记录。",
        "records_written": records_written,
        "output": {
            "source": source.strip().lower(),
            "steps": steps,
            "step_count": len(steps),
            "success_count": succeeded,
            "no_data_count": no_data,
            "failure_count": failed,
            "data_modified": records_written > 0,
        },
    }


def build_source_update_plan(
    source: str,
    *,
    params: dict[str, Any] | None = None,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Build a bounded, deterministic update plan for one supported source."""
    normalized_source = source.strip().lower()
    if normalized_source not in SOURCE_UPDATE_SOURCES:
        available = ", ".join(sorted(SOURCE_UPDATE_SOURCES))
        raise ValueError(f"Unknown source update preset: {source}. Available: {available}.")
    values = params or {}
    validate_source_update_params(normalized_source, values)
    current_date = today or date.today()
    yesterday = current_date - timedelta(days=1)

    if normalized_source == "elecheck":
        return build_elecheck_source_update_plan(values, today=current_date)
    if normalized_source == "entsoe":
        area = str(values.get("area") or "DE-LU").upper()
        window = {
            "area": area,
            "start_date": yesterday.isoformat(),
            "end_date": current_date.isoformat(),
        }
        return [
            source_update_step("日前价格", "entsoe_day_ahead_prices", window),
            source_update_step("实际总负荷", "entsoe_actual_total_load", window),
            source_update_step("按类型实际发电", "entsoe_actual_generation_by_type", window),
        ]
    if normalized_source == "elexon":
        window = {
            "start_date": (current_date - timedelta(days=2)).isoformat(),
            "end_date": current_date.isoformat(),
        }
        return [
            source_update_step("系统结算价格", "elexon_system_prices", window),
            source_update_step("初始全国需求实绩", "elexon_initial_demand_outturn", window),
            source_update_step("风电发电预测", "elexon_wind_generation_forecast", window),
            source_update_step(
                "半小时燃料类型发电",
                "elexon_generation_by_fuel_half_hourly",
                window,
            ),
            source_update_step("互联线潮流", "elexon_interconnector_flows", window),
        ]
    if normalized_source == "gridstatus":
        return [
            source_update_step("数据集目录", "gridstatus_datasets", {}),
            source_update_step("CAISO 燃料结构", "gridstatus_caiso_fuel_mix", {}),
            source_update_step(
                "PJM 日前节点电价",
                "gridstatus_pjm_lmp_day_ahead_hourly_pseg",
                {},
            ),
            source_update_step("NYISO 燃料结构更新", "gridstatus_nyiso_fuel_mix_updates", {}),
        ]
    return [source_update_step("公开信息", "gzpec-news-combined", {})]


def build_elecheck_source_update_plan(
    params: dict[str, Any],
    *,
    today: date,
) -> list[dict[str, Any]]:
    db_path = scheduler_sqlite_path()
    areas = clear_price_area_targets(db_path)
    if not areas:
        raise ValueError("没有 Elecheck 地区配置。请先初始化数据库。")
    area_filter = str(params.get("area") or "").strip()
    if area_filter:
        areas = [
            area
            for area in areas
            if area_filter in {str(area["area_name"]), str(area["area_code"])}
        ]
        if not areas:
            raise ValueError(f"Unknown Elecheck area: {area_filter}.")

    max_days = int(params.get("max_days", 7))
    latest_dates = clear_price_latest_daily_dates_by_area(db_path)
    target_end = today - timedelta(days=1)
    plan: list[dict[str, Any]] = []
    for area in areas:
        area_name = str(area["area_name"])
        area_code = str(area["area_code"])
        earliest_raw = area.get("earliest_clear_price_date")
        earliest = date.fromisoformat(str(earliest_raw)) if earliest_raw else None
        latest_raw = latest_dates.get(area_code)
        if latest_raw:
            target_start = date.fromisoformat(latest_raw) - timedelta(days=1)
            if earliest is not None:
                target_start = max(target_start, earliest)
            target_start = min(target_start, target_end)
            target_finish = min(
                target_end,
                target_start + timedelta(days=max_days - 1),
            )
        else:
            # A fresh installation bootstraps one complete day instead of silently
            # launching a multi-year history crawl.
            if earliest is not None and earliest > target_end:
                continue
            target_start = target_end
            target_finish = target_end
        plan.append(
            source_update_step(
                f"{area_name}现货",
                "elecheck_clear_price",
                {
                    "area_code": area_code,
                    "start_date": target_start.isoformat(),
                    "end_date": target_finish.isoformat(),
                    "daily": True,
                },
            )
        )

    previous_month_date = today.replace(day=1) - timedelta(days=1)
    plan.extend(
        [
            source_update_step(
                "全国代理购电近两月",
                "elecheck_purchasing_national_range",
                {
                    "start_month": previous_month_date.strftime("%Y-%m"),
                    "end_month": today.strftime("%Y-%m"),
                },
            ),
            source_update_step(
                "增量机制电价",
                "elecheck_mechanism_electricity_price",
                {},
            ),
        ]
    )
    if len(plan) == 2:
        raise ValueError("当前日期之前没有可采集的 Elecheck 现货地区。")
    return plan


def source_update_step(
    label: str,
    spider_name: str,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    return {"label": label, "spider_name": spider_name, "kwargs": dict(kwargs)}


def run_spider_and_upsert(spider_name: str, spider_kwargs: dict[str, Any]) -> tuple[int, int]:
    spider = get_spider(spider_name, **spider_kwargs)
    try:
        records = list(spider.crawl())
    finally:
        spider.close()
    if not records:
        return 0, 0
    if all(isinstance(record, MarketRecord) for record in records):
        written = upsert_records(records)
    elif all(isinstance(record, EntsoeRecord) for record in records):
        written = upsert_entsoe_records(records)
    elif all(isinstance(record, ElexonRecord) for record in records):
        written = upsert_elexon_records(records)
    elif all(isinstance(record, GzpecNewsRecord) for record in records):
        written = upsert_gzpec_news_records(records)
    elif all(isinstance(record, GridStatusRecord) for record in records):
        written = upsert_gridstatus_records(records)
    elif all(isinstance(record, GridStatusDatasetMetadataRecord) for record in records):
        written = upsert_gridstatus_dataset_metadata_records(records)
    elif all(isinstance(record, ElecheckClearPriceRecord) for record in records):
        written = upsert_elecheck_clear_price_records(records)
    elif all(isinstance(record, ElecheckPurchasingProvinceRecord) for record in records):
        written = upsert_elecheck_purchasing_province_records(records)
    elif all(isinstance(record, ElecheckPurchasingRecord) for record in records):
        written = upsert_elecheck_purchasing_records(records)
    elif all(isinstance(record, ElecheckMechanismElectricityPriceRecord) for record in records):
        written = upsert_elecheck_mechanism_electricity_price_records(records)
    else:
        raise ValueError("A scheduled spider must return one record type per crawl.")
    return written, len(records)


def create_job_run(*, job_id: int, started_at: datetime) -> int:
    with get_session() as session:
        row = ScheduledJobRunRow(
            job_id=job_id,
            started_at=started_at,
            finished_at=None,
            status="running",
            message="Running",
            records_written=None,
            output_json="{}",
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return int(row.id)


def finish_job_run(
    *,
    run_id: int,
    status: str,
    message: str,
    records_written: int | None,
    output: dict[str, Any],
) -> None:
    with get_session() as session:
        row = session.get(ScheduledJobRunRow, run_id)
        if row is None:
            raise ValueError(f"Unknown scheduled run id: {run_id}")
        row.finished_at = utc_now_naive()
        row.status = status
        row.message = sanitize_message(message)
        row.records_written = records_written
        row.output_json = json.dumps(output, ensure_ascii=False)
        session.commit()


def resolve_job_date_window(
    job: ScheduledJobRow,
    *,
    today: date | None = None,
) -> tuple[date | None, date | None]:
    today = today or date.today()
    if job.date_mode == "none":
        return None, None
    if job.date_mode == "yesterday":
        return today - timedelta(days=1), today
    if job.date_mode == "last-7-days":
        return today - timedelta(days=7), today
    if job.date_mode == "last-30-days":
        return today - timedelta(days=30), today
    if job.date_mode == "custom":
        if job.start_date is None or job.end_date is None:
            raise ValueError("custom date mode requires start_date and end_date.")
        if job.end_date <= job.start_date:
            raise ValueError("custom end_date must be later than start_date.")
        return job.start_date, job.end_date
    raise ValueError(f"Unsupported date mode: {job.date_mode}")


def build_scheduled_spider_kwargs(
    spider_name: str,
    *,
    params: dict[str, Any],
    start: date | None,
    end: date | None,
) -> dict[str, Any]:
    kwargs = dict(params)
    if start is None and end is None:
        return kwargs
    if start is None or end is None or end <= start:
        raise ValueError("Scheduled crawl date windows require start < end.")

    semantics = scheduled_spider_date_semantics(spider_name)
    if semantics == DATE_SEMANTICS_INCLUSIVE_DAILY:
        kwargs["start_date"] = start.isoformat()
        kwargs["end_date"] = (end - timedelta(days=1)).isoformat()
        kwargs["daily"] = True
        return kwargs
    if semantics == DATE_SEMANTICS_EXCLUSIVE:
        kwargs["start_date"] = start.isoformat()
        kwargs["end_date"] = end.isoformat()
        return kwargs
    raise ValueError(
        f"{spider_name} does not support scheduled date windows. "
        "Use date_mode=none and provide its source-specific parameters instead."
    )


def scheduled_spider_date_semantics(spider_name: str) -> str:
    if spider_name == "elecheck_clear_price":
        return DATE_SEMANTICS_INCLUSIVE_DAILY
    if spider_name.startswith("entsoe_"):
        return DATE_SEMANTICS_EXCLUSIVE
    if spider_name.startswith("elexon_"):
        config = get_elexon_request_config(spider_name)
        if config.get("time_mode") == "snapshot":
            return DATE_SEMANTICS_NONE
        return DATE_SEMANTICS_EXCLUSIVE
    return DATE_SEMANTICS_NONE


def build_windows_task_command(job: ScheduledJobRow) -> list[str]:
    task_name = job.windows_task_name or default_windows_task_name(job)
    return [
        "schtasks",
        "/Create",
        "/TN",
        task_name,
        "/TR",
        subprocess.list2cmdline(runtime_invocation(job.id)),
        "/SC",
        job.schedule_kind.upper(),
        "/ST",
        job.schedule_time,
        "/F",
    ]


def build_windows_task_delete_command(job: ScheduledJobRow) -> list[str]:
    task_name = job.windows_task_name or default_windows_task_name(job)
    return ["schtasks", "/Delete", "/TN", task_name, "/F"]


def install_windows_task(job_id: int) -> str:
    if sys.platform != "win32":
        raise RuntimeError("Windows Task Scheduler is only available on Windows.")
    job = get_scheduled_job(job_id)
    task_name = job.windows_task_name or default_windows_task_name(job)
    command = build_windows_task_command(job)
    subprocess.run(command, check=True, cwd=get_project_root())
    with get_session() as session:
        row = session.get(ScheduledJobRow, job_id)
        if row is None:
            raise ValueError(f"Unknown scheduled job id: {job_id}")
        row.windows_task_name = task_name
        row.updated_at = utc_now_naive()
        session.commit()
    return task_name


def uninstall_windows_task(job_id: int) -> str:
    if sys.platform != "win32":
        raise RuntimeError("Windows Task Scheduler is only available on Windows.")
    job = get_scheduled_job(job_id)
    task_name = job.windows_task_name or default_windows_task_name(job)
    subprocess.run(build_windows_task_delete_command(job), check=True, cwd=get_project_root())
    with get_session() as session:
        row = session.get(ScheduledJobRow, job_id)
        if row is None:
            raise ValueError(f"Unknown scheduled job id: {job_id}")
        row.windows_task_name = None
        row.updated_at = utc_now_naive()
        session.commit()
    return task_name


def query_windows_task_status(job: ScheduledJobRow) -> str:
    """Return installed/missing/unknown without trusting the local database flag."""
    if not job.windows_task_name:
        return "not_installed"
    if sys.platform != "win32":
        return "unavailable"
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", job.windows_task_name],
        check=False,
        cwd=get_project_root(),
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return "installed"
    output = f"{result.stdout}\n{result.stderr}".lower()
    missing_markers = (
        "cannot find",
        "not exist",
        "does not exist",
        "找不到",
        "不存在",
    )
    if any(marker in output for marker in missing_markers):
        return "missing"
    return "unknown"


def synchronize_windows_task_state(job_id: int) -> str:
    """Clear stale local installation metadata after an external task deletion."""
    job = get_scheduled_job(job_id)
    status = query_windows_task_status(job)
    if status != "missing":
        return status
    with get_session() as session:
        row = session.get(ScheduledJobRow, job_id)
        if row is None:
            raise ValueError(f"Unknown scheduled job id: {job_id}")
        row.windows_task_name = None
        row.updated_at = utc_now_naive()
        session.commit()
    return status


def runtime_invocation(job_id: int) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--headless", "schedule-run", str(job_id)]
    launcher_path = project_root() / "desktop_launcher.py"
    return [
        sys.executable,
        str(launcher_path),
        "--headless",
        "schedule-run",
        str(job_id),
    ]


def default_windows_task_name(job: ScheduledJobRow) -> str:
    return f"PowertradeCrawler_{job.id}_{slugify(job.name)}"[:240]


def validate_job_fields(
    *,
    name: str,
    job_type: str,
    spider_name: str | None,
    schedule_kind: str,
    schedule_time: str,
    date_mode: str,
    start_date: date | None,
    end_date: date | None,
    params: dict[str, Any] | None = None,
) -> None:
    normalized_spider_name = spider_name.strip() if spider_name else ""
    if not name.strip():
        raise ValueError("Scheduled job name cannot be empty.")
    if job_type not in VALID_JOB_TYPES:
        raise ValueError(f"Unsupported job type: {job_type}.")
    if job_type == "crawl" and not normalized_spider_name:
        raise ValueError("crawl jobs require spider_name.")
    if job_type == "crawl" and normalized_spider_name not in set(list_spiders()):
        raise ValueError(f"Unknown spider: {normalized_spider_name}.")
    if job_type == "source_update" and normalized_spider_name not in SOURCE_UPDATE_SOURCES:
        raise ValueError(
            "source_update jobs require one of: "
            + ", ".join(sorted(SOURCE_UPDATE_SOURCES))
            + "."
        )
    if schedule_kind not in VALID_SCHEDULE_KINDS:
        raise ValueError(f"Unsupported schedule kind: {schedule_kind}.")
    if not re.fullmatch(r"[0-2][0-9]:[0-5][0-9]", schedule_time):
        raise ValueError("schedule_time must use HH:MM format.")
    hour = int(schedule_time[:2])
    if hour > 23:
        raise ValueError("schedule_time hour must be between 00 and 23.")
    if date_mode not in VALID_DATE_MODES:
        raise ValueError(f"Unsupported date mode: {date_mode}.")
    if job_type == "source_update" and date_mode != "none":
        raise ValueError("source_update jobs calculate safe incremental windows; date_mode must be none.")
    if (
        job_type == "crawl"
        and date_mode != "none"
        and scheduled_spider_date_semantics(normalized_spider_name) == DATE_SEMANTICS_NONE
    ):
        raise ValueError(
            f"{normalized_spider_name} does not support scheduled date windows. "
            "Use date_mode=none and provide its source-specific parameters instead."
        )
    if date_mode == "custom" and (start_date is None or end_date is None):
        raise ValueError("custom date mode requires start_date and end_date.")
    if start_date is not None and end_date is not None and end_date <= start_date:
        raise ValueError("end_date must be later than start_date.")
    validate_no_credentials(params or {})
    if job_type == "source_update":
        validate_source_update_params(normalized_spider_name, params or {})


def validate_source_update_params(source: str, params: dict[str, Any]) -> None:
    allowed_keys = {
        "elecheck": {"area", "max_days"},
        "entsoe": {"area"},
        "elexon": set(),
        "gridstatus": set(),
        "gzpec": set(),
    }[source]
    unknown = sorted(set(params) - allowed_keys)
    if unknown:
        raise ValueError(
            f"Unsupported {source} source update parameters: {', '.join(unknown)}."
        )
    if source == "entsoe":
        area = str(params.get("area") or "").strip().upper()
        if not area:
            raise ValueError("ENTSO-E source updates require a bidding area.")
        if area not in ENTSOE_BIDDING_ZONES:
            raise ValueError(f"Unknown ENTSO-E bidding area: {area}.")
    if source == "elecheck":
        try:
            max_days = int(params.get("max_days", 7))
        except (TypeError, ValueError) as exc:
            raise ValueError("Elecheck max_days must be an integer from 1 to 31.") from exc
        if not 1 <= max_days <= 31:
            raise ValueError("Elecheck max_days must be an integer from 1 to 31.")


def validate_no_credentials(value: Any, *, path: str = "params") -> None:
    """Keep every credential out of persisted scheduled task parameters."""
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key).strip().lower().replace("-", "_")
            if (
                "authorization" in key
                or "password" in key
                or "credential" in key
                or "secret" in key
                or "token" in key
                or key.endswith("api_key")
            ):
                raise ValueError(
                    f"Credentials are not allowed in scheduled task parameters ({path}.{raw_key}). "
                    "Use the API configuration guide instead."
                )
            validate_no_credentials(child, path=f"{path}.{raw_key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            validate_no_credentials(child, path=f"{path}[{index}]")


def parse_params_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("params_json must be a JSON object.") from exc
    if not isinstance(payload, dict):
        raise ValueError("params_json must be a JSON object.")
    return payload


def parse_optional_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def sanitize_message(message: str) -> str:
    sanitized = re.sub(r"(securityToken=)[^&\s]+", r"\1***", message)
    sanitized = re.sub(r"(authorization['\"]?\s*[:=]\s*['\"]?)[^,'\"\s]+", r"\1***", sanitized)
    return sanitized


def failure_output(exc: Exception, *, data_modified: bool | None) -> dict[str, Any]:
    output: dict[str, Any] = {
        "error_type": type(exc).__name__,
        "data_modified": data_modified,
    }
    if isinstance(exc, NetworkFailure):
        output.update(exc.as_dict())
    return output


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip())
    return slug.strip("_") or "job"


def detach_job(row: ScheduledJobRow) -> ScheduledJobRow:
    return ScheduledJobRow(
        id=row.id,
        name=row.name,
        job_type=row.job_type,
        spider_name=row.spider_name,
        schedule_kind=row.schedule_kind,
        schedule_time=row.schedule_time,
        date_mode=row.date_mode,
        start_date=row.start_date,
        end_date=row.end_date,
        enabled=row.enabled,
        params_json=row.params_json,
        windows_task_name=row.windows_task_name,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def detach_run(row: ScheduledJobRunRow) -> ScheduledJobRunRow:
    return ScheduledJobRunRow(
        id=row.id,
        job_id=row.job_id,
        started_at=row.started_at,
        finished_at=row.finished_at,
        status=row.status,
        message=row.message,
        records_written=row.records_written,
        output_json=row.output_json,
    )


def project_python_command() -> str:
    return subprocess.list2cmdline([sys.executable, "-m", "powertrade_crawler.cli"])


def project_root() -> Path:
    return get_project_root()


def scheduler_sqlite_path() -> Path:
    database_url = get_settings().database_url
    if not database_url.startswith("sqlite:///"):
        raise ValueError("Source update presets currently require the local SQLite database.")
    raw_path = database_url.replace("sqlite:///", "", 1)
    path = Path(raw_path)
    return path if path.is_absolute() else project_root() / path


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
