import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Event

import pytest
from typer.testing import CliRunner

from powertrade_crawler.cli import app
from powertrade_crawler.config import get_settings
from powertrade_crawler.scheduler import (
    build_source_update_plan,
    build_scheduled_spider_kwargs,
    build_windows_task_command,
    create_source_update_job,
    create_default_job_templates,
    create_scheduled_job,
    delete_scheduled_job,
    list_recent_job_runs,
    list_elecheck_source_update_areas,
    list_scheduled_jobs,
    parse_params_json,
    resolve_job_date_window,
    run_scheduled_job,
    sanitize_message,
    scheduled_run_exit_code,
    synchronize_windows_task_state,
    execute_source_update,
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

    assert created == 9
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


def test_windows_task_state_sync_clears_externally_deleted_trigger(
    tmp_path: Path,
    monkeypatch,
):
    prepare_db(tmp_path, monkeypatch)
    job = create_scheduled_job(
        name="externally removed",
        job_type="metrics",
        schedule_time="02:00",
        date_mode="last-30-days",
    )
    with get_session() as session:
        row = session.get(ScheduledJobRow, job.id)
        row.windows_task_name = "PowertradeCrawler_external"
        session.commit()

    completed = subprocess.CompletedProcess(
        args=["schtasks"],
        returncode=1,
        stdout="ERROR: The system cannot find the file specified.",
        stderr="",
    )
    monkeypatch.setattr("powertrade_crawler.scheduler.sys.platform", "win32")
    monkeypatch.setattr("powertrade_crawler.scheduler.subprocess.run", lambda *_a, **_k: completed)

    assert synchronize_windows_task_state(job.id) == "missing"
    assert list_scheduled_jobs()[0].windows_task_name is None


def test_windows_task_state_sync_does_not_clear_unknown_query_failure(
    tmp_path: Path,
    monkeypatch,
):
    prepare_db(tmp_path, monkeypatch)
    job = create_scheduled_job(
        name="query denied",
        job_type="metrics",
        schedule_time="02:00",
        date_mode="last-30-days",
    )
    with get_session() as session:
        row = session.get(ScheduledJobRow, job.id)
        row.windows_task_name = "PowertradeCrawler_denied"
        session.commit()

    completed = subprocess.CompletedProcess(
        args=["schtasks"],
        returncode=1,
        stdout="",
        stderr="Access is denied.",
    )
    monkeypatch.setattr("powertrade_crawler.scheduler.sys.platform", "win32")
    monkeypatch.setattr("powertrade_crawler.scheduler.subprocess.run", lambda *_a, **_k: completed)

    assert synchronize_windows_task_state(job.id) == "unknown"
    assert list_scheduled_jobs()[0].windows_task_name == "PowertradeCrawler_denied"


def test_scheduled_run_exit_code_marks_incomplete_updates_as_process_errors():
    assert scheduled_run_exit_code("failed") == 1
    assert scheduled_run_exit_code("partial") == 1
    assert scheduled_run_exit_code("success") == 0
    assert scheduled_run_exit_code("skipped") == 0


def test_same_scheduled_job_cannot_run_concurrently(tmp_path: Path, monkeypatch):
    prepare_db(tmp_path, monkeypatch)
    job = create_scheduled_job(
        name="exclusive job",
        job_type="metrics",
        schedule_time="02:00",
        date_mode="last-30-days",
    )
    entered = Event()
    release = Event()
    executions = 0

    def fake_execute(_job):
        nonlocal executions
        executions += 1
        entered.set()
        assert release.wait(timeout=5)
        return {
            "status": "success",
            "message": "done",
            "records_written": 1,
            "output": {},
        }

    monkeypatch.setattr("powertrade_crawler.scheduler.execute_job", fake_execute)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run_scheduled_job, job.id)
        assert entered.wait(timeout=5)
        second = pool.submit(run_scheduled_job, job.id)
        second_result = second.result(timeout=5)
        release.set()
        first_result = first.result(timeout=5)

    assert executions == 1
    assert first_result["status"] == "success"
    assert second_result["status"] == "skipped"
    assert second_result["output"]["skip_reason"] == "already_running"
    assert second_result["output"]["data_modified"] is False


@pytest.mark.parametrize(
    ("source", "params", "expected_spiders"),
    [
        (
            "entsoe",
            {"area": "DE-LU"},
            {
                "entsoe_day_ahead_prices",
                "entsoe_actual_total_load",
                "entsoe_actual_generation_by_type",
            },
        ),
        (
            "elexon",
            {},
            {
                "elexon_system_prices",
                "elexon_initial_demand_outturn",
                "elexon_wind_generation_forecast",
                "elexon_generation_by_fuel_half_hourly",
                "elexon_interconnector_flows",
            },
        ),
        (
            "gridstatus",
            {},
            {
                "gridstatus_datasets",
                "gridstatus_caiso_fuel_mix",
                "gridstatus_pjm_lmp_day_ahead_hourly_pseg",
                "gridstatus_nyiso_fuel_mix_updates",
            },
        ),
        ("gzpec", {}, {"gzpec-news-combined"}),
    ],
)
def test_source_update_plans_cover_current_safe_datasets(source, params, expected_spiders):
    plan = build_source_update_plan(source, params=params, today=date(2026, 8, 7))

    assert {step["spider_name"] for step in plan} == expected_spiders
    assert "gridstatus_ercot_load" not in {step["spider_name"] for step in plan}


def test_elecheck_source_update_bootstraps_one_day_and_then_catches_up_bounded(
    monkeypatch,
):
    areas = [
        {
            "area_name": "江苏",
            "area_code": "320000000000",
            "earliest_clear_price_date": "2024-01-01",
        }
    ]
    monkeypatch.setattr("powertrade_crawler.scheduler.clear_price_area_targets", lambda _path: areas)
    monkeypatch.setattr(
        "powertrade_crawler.scheduler.clear_price_latest_daily_dates_by_area",
        lambda _path: {},
    )

    fresh_plan = build_source_update_plan(
        "elecheck",
        params={"max_days": 7},
        today=date(2026, 8, 7),
    )
    fresh_spot = fresh_plan[0]
    assert fresh_spot["kwargs"]["start_date"] == "2026-08-06"
    assert fresh_spot["kwargs"]["end_date"] == "2026-08-06"
    assert {step["spider_name"] for step in fresh_plan[-2:]} == {
        "elecheck_purchasing_national_range",
        "elecheck_mechanism_electricity_price",
    }

    monkeypatch.setattr(
        "powertrade_crawler.scheduler.clear_price_latest_daily_dates_by_area",
        lambda _path: {"320000000000": "2026-07-01"},
    )
    catch_up_plan = build_source_update_plan(
        "elecheck",
        params={"max_days": 7},
        today=date(2026, 8, 7),
    )
    catch_up_spot = catch_up_plan[0]
    assert catch_up_spot["kwargs"]["start_date"] == "2026-06-30"
    assert catch_up_spot["kwargs"]["end_date"] == "2026-07-06"


def test_source_update_continues_after_one_step_failure(monkeypatch):
    monkeypatch.setattr(
        "powertrade_crawler.scheduler.build_source_update_plan",
        lambda *_args, **_kwargs: [
            {"label": "first", "spider_name": "one", "kwargs": {}},
            {"label": "second", "spider_name": "two", "kwargs": {}},
        ],
    )

    def fake_run(spider_name, _kwargs):
        if spider_name == "one":
            raise RuntimeError("network timeout securityToken=do-not-log")
        return 3, 4

    monkeypatch.setattr("powertrade_crawler.scheduler.run_spider_and_upsert", fake_run)

    result = execute_source_update("elexon")

    assert result["status"] == "partial"
    assert result["records_written"] == 3
    assert result["output"]["success_count"] == 1
    assert "do-not-log" not in result["output"]["steps"][0]["error"]


def test_source_update_distinguishes_no_new_data_from_failure(monkeypatch):
    monkeypatch.setattr(
        "powertrade_crawler.scheduler.build_source_update_plan",
        lambda *_args, **_kwargs: [
            {"label": "empty", "spider_name": "one", "kwargs": {}},
            {"label": "empty too", "spider_name": "two", "kwargs": {}},
        ],
    )
    monkeypatch.setattr(
        "powertrade_crawler.scheduler.run_spider_and_upsert",
        lambda _spider, _kwargs: (0, 0),
    )

    result = execute_source_update("elexon")

    assert result["status"] == "no_data"
    assert result["output"]["no_data_count"] == 2
    assert result["output"]["failure_count"] == 0
    assert result["output"]["data_modified"] is False
    assert all(step["status"] == "no_data" for step in result["output"]["steps"])


def test_create_daily_source_update_job_uses_nine_am_and_windows_command(
    tmp_path: Path,
    monkeypatch,
):
    prepare_db(tmp_path, monkeypatch)

    job = create_source_update_job(source="elecheck", schedule_time="09:00")

    assert job.job_type == "source_update"
    assert job.spider_name == "elecheck"
    assert job.date_mode == "none"
    assert job.enabled is True
    command = build_windows_task_command(job)
    assert command[command.index("/SC") + 1] == "DAILY"
    assert command[command.index("/ST") + 1] == "09:00"
    areas = list_elecheck_source_update_areas()
    assert "江苏" in areas
    assert len(areas) >= 20


def test_schedule_rejects_credentials_and_duplicate_names(tmp_path: Path, monkeypatch):
    prepare_db(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="Credentials are not allowed"):
        create_scheduled_job(
            name="unsafe",
            job_type="crawl",
            spider_name="gridstatus_datasets",
            date_mode="none",
            params={"nested": {"api_key": "secret"}},
        )

    create_source_update_job(source="gzpec", name="same name")
    with pytest.raises(ValueError, match="already exists"):
        create_source_update_job(source="gzpec", name="same name")


def test_source_update_cli_creates_safe_local_job(tmp_path: Path, monkeypatch):
    prepare_db(tmp_path, monkeypatch)

    result = CliRunner().invoke(
        app,
        [
            "schedule-create-source",
            "elexon",
            "--schedule-time",
            "09:00",
            "--local-only",
        ],
    )

    assert result.exit_code == 0
    assert "Created source update job" in result.stdout
    assert "Local task only" in result.stdout
    assert list_scheduled_jobs()[0].spider_name == "elexon"
