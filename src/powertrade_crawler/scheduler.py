from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import desc

from powertrade_crawler.credentials import get_project_root
from powertrade_crawler.metrics import rebuild_dashboard_daily_metrics, run_database_maintenance
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


VALID_JOB_TYPES = {"crawl", "metrics", "maintenance"}
VALID_DATE_MODES = {"none", "yesterday", "last-7-days", "last-30-days", "custom"}
VALID_SCHEDULE_KINDS = {"daily", "weekly", "monthly"}
DATE_SEMANTICS_EXCLUSIVE = "exclusive"
DATE_SEMANTICS_INCLUSIVE_DAILY = "inclusive-daily"
DATE_SEMANTICS_NONE = "none"


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
        session.commit()
        session.refresh(row)
        return detach_job(row)


def create_default_job_templates() -> int:
    templates = [
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
            "output": {"error_type": type(exc).__name__},
        }
    finish_job_run(
        run_id=run_id,
        status=result["status"],
        message=result["message"],
        records_written=result.get("records_written"),
        output=result.get("output") or {},
    )
    return {"job_id": job.id, "run_id": run_id, **result}


def scheduled_run_exit_code(status: str) -> int:
    return 1 if status == "failed" else 0


def execute_job(job: ScheduledJobRow) -> dict[str, Any]:
    start, end = resolve_job_date_window(job)
    params = parse_params_json(job.params_json)
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
        return {
            "status": "success",
            "message": f"{job.spider_name} produced {produced} records and upserted {written}.",
            "records_written": written,
            "output": {"produced": produced, "spider_name": job.spider_name},
        }
    raise ValueError(f"Unsupported job type: {job.job_type}")


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
    if schedule_kind not in VALID_SCHEDULE_KINDS:
        raise ValueError(f"Unsupported schedule kind: {schedule_kind}.")
    if not re.fullmatch(r"[0-2][0-9]:[0-5][0-9]", schedule_time):
        raise ValueError("schedule_time must use HH:MM format.")
    hour = int(schedule_time[:2])
    if hour > 23:
        raise ValueError("schedule_time hour must be between 00 and 23.")
    if date_mode not in VALID_DATE_MODES:
        raise ValueError(f"Unsupported date mode: {date_mode}.")
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


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
