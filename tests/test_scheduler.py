import subprocess
from datetime import date
from pathlib import Path

import pytest

from powertrade_crawler.config import get_settings
from powertrade_crawler.scheduler import (
    build_scheduled_spider_kwargs,
    build_windows_task_command,
    create_default_job_templates,
    create_scheduled_job,
    delete_scheduled_job,
    list_recent_job_runs,
    list_scheduled_jobs,
    parse_params_json,
    resolve_job_date_window,
    run_scheduled_job,
    sanitize_message,
    scheduled_run_exit_code,
)
from powertrade_crawler.storage import ScheduledJobRow, get_session, init_db


def prepare_db(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "powertrade.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    init_db()


def test_scheduled_job_crud_and_relative_window(tmp_path: Path, monkeypatch):
    prepare_db(tmp_path, monkeypatch)

    job = create_scheduled_job(
        name="metrics test",
        job_type="metrics",
        schedule_kind="daily",
        schedule_time="02:00",
        date_mode="last-7-days",
        enabled=True,
    )

    assert list_scheduled_jobs()[0].name == "metrics test"
    start, end = resolve_job_date_window(job, today=date(2026, 7, 13))
    assert start == date(2026, 7, 6)
    assert end == date(2026, 7, 13)


def test_scheduled_job_run_logs_metrics_result(tmp_path: Path, monkeypatch):
    prepare_db(tmp_path, monkeypatch)

    def fake_rebuild(start_date, end_date):
        assert start_date == date(2026, 1, 1)
        assert end_date == date(2026, 1, 3)
        return 4

    monkeypatch.setattr("powertrade_crawler.scheduler.rebuild_dashboard_daily_metrics", fake_rebuild)
    job = create_scheduled_job(
        name="metrics custom",
        job_type="metrics",
        schedule_kind="daily",
        schedule_time="02:00",
        date_mode="custom",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 3),
        enabled=True,
    )

    result = run_scheduled_job(job.id)

    assert result["status"] == "success"
    assert result["records_written"] == 4
    runs = list_recent_job_runs()
    assert runs[0].job_id == job.id
    assert runs[0].status == "success"
    assert "Rebuilt 4" in runs[0].message


def test_disabled_scheduled_job_is_skipped(tmp_path: Path, monkeypatch):
    prepare_db(tmp_path, monkeypatch)
    job = create_scheduled_job(
        name="disabled job",
        job_type="metrics",
        schedule_kind="daily",
        schedule_time="02:00",
        date_mode="last-30-days",
        enabled=False,
    )

    result = run_scheduled_job(job.id)

    assert result["status"] == "skipped"
    assert list_recent_job_runs()[0].status == "skipped"


def test_windows_task_command_uses_headless_schedule_run(tmp_path: Path, monkeypatch):
    prepare_db(tmp_path, monkeypatch)
    job = create_scheduled_job(
        name="daily metrics",
        job_type="metrics",
        schedule_kind="daily",
        schedule_time="02:15",
        date_mode="last-30-days",
        enabled=True,
    )

    command = build_windows_task_command(job)

    assert command[:2] == ["schtasks", "/Create"]
    assert "/TN" in command
    assert "/TR" in command
    invocation = command[command.index("/TR") + 1]
    assert "desktop_launcher.py" in invocation
    assert "--headless" in invocation
    assert "schedule-run" in invocation
    assert str(job.id) in invocation
    assert command[command.index("/SC") + 1] == "DAILY"
    assert command[command.index("/ST") + 1] == "02:15"


def test_default_templates_are_created_disabled(tmp_path: Path, monkeypatch):
    prepare_db(tmp_path, monkeypatch)

    created = create_default_job_templates()

    assert created == 4
    assert all(not job.enabled for job in list_scheduled_jobs())


def test_params_json_and_message_sanitizing():
    assert parse_params_json('{"area": "DE-LU"}') == {"area": "DE-LU"}
    assert "secret" not in sanitize_message("failed securityToken=secret&documentType=A65")


def test_elecheck_clear_price_converts_exclusive_window_to_inclusive_daily_kwargs():
    kwargs = build_scheduled_spider_kwargs(
        "elecheck_clear_price",
        params={"area_code": "320000000000", "daily": False},
        start=date(2026, 7, 12),
        end=date(2026, 7, 13),
    )

    assert kwargs == {
        "area_code": "320000000000",
        "daily": True,
        "start_date": "2026-07-12",
        "end_date": "2026-07-12",
    }


def test_entsoe_keeps_exclusive_scheduled_window():
    kwargs = build_scheduled_spider_kwargs(
        "entsoe_actual_total_load",
        params={"area": "DE-LU"},
        start=date(2026, 7, 12),
        end=date(2026, 7, 13),
    )

    assert kwargs == {
        "area": "DE-LU",
        "start_date": "2026-07-12",
        "end_date": "2026-07-13",
    }


def test_crawl_job_rejects_date_window_for_non_date_spider(tmp_path: Path, monkeypatch):
    prepare_db(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="date_mode=none"):
        create_scheduled_job(
            name="purchasing range",
            job_type="crawl",
            spider_name="elecheck_purchasing_national_range",
            schedule_kind="daily",
            schedule_time="02:00",
            date_mode="last-30-days",
        )


def test_delete_installed_job_requires_or_removes_windows_task(tmp_path: Path, monkeypatch):
    prepare_db(tmp_path, monkeypatch)
    job = create_scheduled_job(
        name="installed metrics",
        job_type="metrics",
        schedule_kind="daily",
        schedule_time="02:00",
        date_mode="last-30-days",
    )
    with get_session() as session:
        row = session.get(ScheduledJobRow, job.id)
        row.windows_task_name = "PowertradeCrawler_test"
        session.commit()

    with pytest.raises(ValueError, match="Uninstall the Windows task"):
        delete_scheduled_job(job.id)
    assert len(list_scheduled_jobs()) == 1

    def fail_uninstall(_job_id):
        raise subprocess.CalledProcessError(1, ["schtasks", "/Delete"])

    monkeypatch.setattr(
        "powertrade_crawler.scheduler.uninstall_windows_task",
        fail_uninstall,
    )
    with pytest.raises(RuntimeError, match="local job definition was kept"):
        delete_scheduled_job(job.id, remove_windows_task=True)
    assert len(list_scheduled_jobs()) == 1

    removed_job_ids = []
    monkeypatch.setattr(
        "powertrade_crawler.scheduler.uninstall_windows_task",
        lambda job_id: removed_job_ids.append(job_id),
    )
    delete_scheduled_job(job.id, remove_windows_task=True)

    assert removed_job_ids == [job.id]
    assert list_scheduled_jobs() == []


def test_scheduled_run_exit_code_marks_only_failures_as_process_errors():
    assert scheduled_run_exit_code("failed") == 1
    assert scheduled_run_exit_code("success") == 0
    assert scheduled_run_exit_code("skipped") == 0
