import csv
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import StringIO
from math import ceil
from pathlib import Path
from threading import Event
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from powertrade_crawler.config import get_settings
from powertrade_crawler.credentials import get_credential
from powertrade_crawler.gridstatus_rate_limit import (
    GRIDSTATUS_MAX_REQUESTS_PER_MINUTE,
    GRIDSTATUS_SAFE_INTERVAL_SECONDS,
    gridstatus_request_limiter,
)
from powertrade_crawler.network_errors import (
    NetworkFailure,
    NetworkFailureKind,
    failure_from_exception,
    failure_from_response,
    retry_after_seconds,
)


GRIDSTATUS_QUERY_BASE_URL = "https://api.gridstatus.io/v1/datasets/{dataset_id}/query"
GRIDSTATUS_DEFAULT_PAGE_SIZE = 50_000
GRIDSTATUS_MAX_PAGE_SIZE = 50_000


ProgressCallback = Callable[[dict[str, Any] | str], None]
FetchCsv = Callable[[str], bytes]
FetchJson = Callable[[str], dict[str, Any]]


@dataclass
class DownloadResult:
    dataset_id: str
    output_path: Path
    rows_written: int
    requests_made: int
    intervals_completed: int


class DownloadCancelled(RuntimeError):
    pass


@dataclass
class DownloadControl:
    cancel_event: Event | None = None
    pause_event: Event | None = None

    def raise_if_cancelled(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise DownloadCancelled("Download cancelled. Partial CSV and checkpoint were kept.")

    def wait_if_paused(self, progress: ProgressCallback | None = None) -> None:
        while self.pause_event is not None and self.pause_event.is_set():
            self.raise_if_cancelled()
            if progress:
                progress({"message": "下载已暂停，点击继续后恢复", "status": "paused"})
            time.sleep(0.5)


class RateLimiter:
    def __init__(
        self,
        min_interval_seconds: float = GRIDSTATUS_SAFE_INTERVAL_SECONDS,
        batch_size: int = 50,
        batch_pause_seconds: float = 30.0,
        max_requests_per_minute: int = GRIDSTATUS_MAX_REQUESTS_PER_MINUTE,
        max_requests_per_hour: int = 600,
    ) -> None:
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.batch_size = batch_size
        self.batch_pause_seconds = batch_pause_seconds
        self.max_requests_per_minute = max_requests_per_minute
        self.max_requests_per_hour = max_requests_per_hour
        self.request_count = 0
        self.last_request_at = 0.0
        self.request_times: list[float] = []

    def wait(
        self,
        progress: ProgressCallback | None = None,
        control: DownloadControl | None = None,
    ) -> None:
        control = control or DownloadControl()
        control.raise_if_cancelled()
        control.wait_if_paused(progress)

        now = time.monotonic()
        elapsed = now - self.last_request_at
        if self.last_request_at and elapsed < self.min_interval_seconds:
            sleep_seconds = self.min_interval_seconds - elapsed
            if progress:
                progress(f"限流保护：等待 {sleep_seconds:.1f} 秒后继续请求")
            interruptible_sleep(sleep_seconds, control, progress)

        if (
            self.request_count > 0
            and self.batch_size > 0
            and self.request_count % self.batch_size == 0
            and self.batch_pause_seconds > 0
        ):
            if progress:
                progress(f"限流保护：已请求 {self.request_count} 次，休息 {self.batch_pause_seconds:.0f} 秒")
            interruptible_sleep(self.batch_pause_seconds, control, progress)

        self.wait_for_window_limit(60.0, self.max_requests_per_minute, "分钟", progress, control)
        self.wait_for_window_limit(3600.0, self.max_requests_per_hour, "小时", progress, control)
        self.last_request_at = time.monotonic()
        self.request_count += 1
        self.request_times.append(self.last_request_at)

    def wait_for_window_limit(
        self,
        window_seconds: float,
        max_requests: int,
        label: str,
        progress: ProgressCallback | None,
        control: DownloadControl,
    ) -> None:
        if max_requests <= 0:
            return

        while True:
            control.raise_if_cancelled()
            control.wait_if_paused(progress)
            now = time.monotonic()
            self.request_times = [
                request_time
                for request_time in self.request_times
                if now - request_time < window_seconds
            ]
            if len(self.request_times) < max_requests:
                return

            sleep_seconds = window_seconds - (now - self.request_times[0]) + 0.1
            if progress:
                progress(
                    f"限流保护：已达到每{label} {max_requests} 次请求上限，"
                    f"等待 {sleep_seconds:.1f} 秒"
                )
            interruptible_sleep(sleep_seconds, control, progress)


def build_gridstatus_csv_url(
    dataset_id: str,
    api_key: str,
    limit: int | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    filter_column: str | None = None,
    filter_value: str | None = None,
    download: bool = True,
) -> str:
    params: dict[str, Any] = {
        "return_format": "csv",
        "api_key": api_key,
    }
    if limit is not None:
        params["limit"] = limit
    if download:
        params["download"] = "true"
    if start_time is not None:
        params["start_time"] = format_gridstatus_time(start_time)
    if end_time is not None:
        params["end_time"] = format_gridstatus_time(end_time)
    add_gridstatus_filter_params(params, filter_column, filter_value)
    return f"{GRIDSTATUS_QUERY_BASE_URL.format(dataset_id=dataset_id)}?{urlencode(params)}"


def download_dataset_csv_adaptive(
    metadata: dict[str, Any],
    output_path: Path,
    page_size: int = GRIDSTATUS_DEFAULT_PAGE_SIZE,
    min_interval_seconds: float = GRIDSTATUS_SAFE_INTERVAL_SECONDS,
    batch_size: int = 50,
    batch_pause_seconds: float = 30.0,
    min_window_seconds: float = 1.0,
    window_seconds: float | None = None,
    output_format: str = "csv",
    progress: ProgressCallback | None = None,
    fetch_csv: FetchCsv | None = None,
    fetch_json: FetchJson | None = None,
    control: DownloadControl | None = None,
) -> DownloadResult:
    api_key = get_credential("gridstatus_api_key")
    if not api_key:
        raise ValueError(
            "GridStatus API key is required in .auth/credentials.json."
        )

    dataset_id = str(metadata["dataset_id"])
    page_size = int(metadata.get("download_page_size") or page_size)
    if not 1 <= page_size <= GRIDSTATUS_MAX_PAGE_SIZE:
        raise ValueError(
            f"GridStatus page_size must be between 1 and {GRIDSTATUS_MAX_PAGE_SIZE}."
        )
    filter_column = optional_text(metadata.get("download_filter_column"))
    filter_value = optional_text(metadata.get("download_filter_value"))
    validate_gridstatus_filter(filter_column, filter_value)
    start = parse_gridstatus_time(
        metadata.get("download_start_time_utc") or metadata.get("earliest_available_time_utc")
    )
    end = parse_gridstatus_time(
        metadata.get("download_end_time_utc") or metadata.get("latest_available_time_utc")
    )
    if start is None or end is None or start >= end:
        raise ValueError(
            "This dataset does not have a valid earliest/latest time range, so a complete "
            "non-truncated adaptive download cannot be guaranteed."
        )

    if output_format not in {"csv", "sqlite"}:
        raise ValueError(f"Unsupported output format: {output_format}")

    fetch_json = fetch_json or default_fetch_json
    control = control or DownloadControl()
    limiter = RateLimiter(
        min_interval_seconds=min_interval_seconds,
        batch_size=batch_size,
        batch_pause_seconds=batch_pause_seconds,
    )

    full_start = parse_gridstatus_time(metadata.get("earliest_available_time_utc"))
    full_end = parse_gridstatus_time(metadata.get("latest_available_time_utc"))
    estimated_total_rows = estimate_rows_for_range(metadata, start, end, full_start, full_end)
    effective_window_seconds = max(
        min_window_seconds,
        window_seconds or default_window_seconds(metadata, filtered=bool(filter_column)),
    )
    state_path = checkpoint_path(output_path)
    state = load_checkpoint(state_path)
    primary_keys = parse_primary_keys(metadata.get("primary_key_columns_json"))
    if state and state.get("dataset_id") == dataset_id:
        validate_checkpoint_query(
            state,
            start=start,
            end=end,
            filter_column=filter_column,
            filter_value=filter_value,
            page_size=page_size,
            output_format=output_format,
        )
        window_start = parse_gridstatus_time(state.get("window_start_utc")) or start
        window_end = parse_gridstatus_time(state.get("window_end_utc"))
        if window_end is None or window_end <= window_start:
            window_end = min(end, window_start + timedelta(seconds=effective_window_seconds))
        cursor = state.get("cursor") or ""
        has_next_page = bool(state.get("has_next_page", True))
        requests_made = int(state.get("requests_made", 0))
        intervals_completed = int(state.get("intervals_completed", 0))
        if output_format == "sqlite":
            rows_written = read_sqlite_row_count(output_path)
            seen: set[tuple[Any, ...] | str] = read_sqlite_row_keys(output_path)
        else:
            rows_written, seen = read_csv_row_state(output_path, primary_keys)
        if progress:
            emit_progress(
                progress,
                dataset_id=dataset_id,
                message=f"发现断点文件：已存在 {rows_written} 行，将从上次进度继续下载",
                rows_written=rows_written,
                requests_made=requests_made,
                intervals_pending=pending_interval_count(
                    window_start,
                    window_end,
                    end,
                    effective_window_seconds,
                    has_next_page,
                ),
                estimated_total_rows=estimated_total_rows,
                percent_override=download_time_percent(start, end, window_start),
            )
    else:
        window_start = start
        window_end = min(end, window_start + timedelta(seconds=effective_window_seconds))
        cursor = ""
        has_next_page = True
        requests_made = 0
        intervals_completed = 0
        rows_written = 0
        seen: set[tuple[Any, ...] | str] = set()
        if output_format == "sqlite":
            initialize_sqlite_output(output_path, metadata)

    while window_start < end:
        if not has_next_page:
            intervals_completed += 1
            window_start = window_end
            window_end = min(end, window_start + timedelta(seconds=effective_window_seconds))
            cursor = ""
            has_next_page = True
            continue

        control.raise_if_cancelled()
        control.wait_if_paused(progress)
        if progress:
            emit_progress(
                progress,
                dataset_id=dataset_id,
                message=(
                    "请求 "
                    f"{format_gridstatus_time(window_start)} -> "
                    f"{format_gridstatus_time(window_end)}"
                ),
                rows_written=rows_written,
                requests_made=requests_made,
                intervals_pending=pending_interval_count(
                    window_start,
                    window_end,
                    end,
                    effective_window_seconds,
                    has_next_page,
                ),
                estimated_total_rows=estimated_total_rows,
                percent_override=download_time_percent(start, end, window_start),
            )

        limiter.wait(progress, control)
        control.raise_if_cancelled()
        url = build_gridstatus_json_url(
            dataset_id=dataset_id,
            api_key=api_key,
            page_size=page_size,
            cursor=cursor,
            start_time=window_start,
            end_time=window_end,
            filter_column=filter_column,
            filter_value=filter_value,
        )
        payload = fetch_json(url)
        rows = normalize_json_rows(payload)
        if len(rows) > page_size:
            raise RuntimeError(
                f"GridStatus returned {len(rows)} rows in one page, exceeding requested "
                f"page_size={page_size}. Download stopped to avoid unexpected API usage."
            )
        meta = payload.get("meta") or {}
        has_next_page = bool(meta.get("hasNextPage") or meta.get("has_next_page"))
        cursor = meta.get("cursor") or None
        requests_made += 1
        completed_through = window_start if has_next_page else window_end
        intervals_pending = pending_interval_count(
            window_start,
            window_end,
            end,
            effective_window_seconds,
            has_next_page,
        )

        if progress:
            emit_progress(
                progress,
                dataset_id=dataset_id,
                message=f"返回 {len(rows)} 行；hasNextPage={has_next_page}",
                rows_written=rows_written,
                requests_made=requests_made,
                intervals_pending=intervals_pending,
                estimated_total_rows=estimated_total_rows,
                percent_override=download_time_percent(start, end, completed_through),
            )

        new_rows: list[dict[str, str]] = []
        for row in rows:
            key = (
                row_key_text(row, primary_keys)
                if output_format == "sqlite"
                else row_key(row, primary_keys)
            )
            if key in seen:
                continue
            seen.add(key)
            new_rows.append(row)

        if output_format == "sqlite":
            write_sqlite_rows(output_path, new_rows, primary_keys)
            rows_written += len(new_rows)
        else:
            append_csv_rows(output_path, new_rows)
            rows_written += len(new_rows)
        save_checkpoint(
            state_path,
            dataset_id=dataset_id,
            cursor=cursor,
            has_next_page=has_next_page,
            requests_made=requests_made,
            intervals_completed=intervals_completed,
            window_start=window_start,
            window_end=window_end,
            download_start=start,
            download_end=end,
            filter_column=filter_column,
            filter_value=filter_value,
            page_size=page_size,
            output_format=output_format,
        )
        if progress:
            emit_progress(
                progress,
                dataset_id=dataset_id,
                message=f"已保存断点：当前写入 {rows_written} 行",
                rows_written=rows_written,
                requests_made=requests_made,
                intervals_pending=intervals_pending,
                estimated_total_rows=estimated_total_rows,
                percent_override=download_time_percent(start, end, completed_through),
            )

        if has_next_page and not cursor:
            raise RuntimeError("API indicated hasNextPage=true but did not provide a cursor.")

    if state_path.exists():
        state_path.unlink()
    return DownloadResult(
        dataset_id=dataset_id,
        output_path=output_path,
        rows_written=rows_written,
        requests_made=requests_made,
        intervals_completed=intervals_completed,
    )


def interruptible_sleep(
    seconds: float,
    control: DownloadControl,
    progress: ProgressCallback | None = None,
) -> None:
    deadline = time.monotonic() + seconds
    while True:
        control.raise_if_cancelled()
        control.wait_if_paused(progress)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.5, remaining))


def emit_progress(
    progress: ProgressCallback,
    dataset_id: str,
    message: str,
    rows_written: int,
    requests_made: int,
    intervals_pending: int,
    estimated_total_rows: int | None,
    percent_override: float | None = None,
) -> None:
    if percent_override is not None:
        percent = max(0.0, min(100.0, percent_override))
    elif estimated_total_rows and estimated_total_rows > 0:
        percent = max(0.0, min(99.0, rows_written / estimated_total_rows * 100))
        if intervals_pending == 0:
            percent = 100.0
    else:
        percent = min(95.0, requests_made * 3.0)
        if intervals_pending == 0:
            percent = 100.0
    progress(
        {
            "dataset_id": dataset_id,
            "message": message,
            "rows_written": rows_written,
            "requests_made": requests_made,
            "intervals_pending": intervals_pending,
            "percent": percent,
        }
    )


def download_time_percent(start: datetime, end: datetime, completed_through: datetime) -> float:
    total_seconds = (end - start).total_seconds()
    if total_seconds <= 0:
        return 100.0
    completed_seconds = (completed_through - start).total_seconds()
    return completed_seconds / total_seconds * 100


def pending_interval_count(
    window_start: datetime,
    window_end: datetime,
    download_end: datetime,
    window_seconds: float,
    has_next_page: bool,
) -> int:
    future_seconds = max(0.0, (download_end - window_end).total_seconds())
    future_windows = ceil(future_seconds / window_seconds) if window_seconds > 0 else 0
    return future_windows + (1 if has_next_page and window_start < download_end else 0)


def default_window_seconds(metadata: dict[str, Any], *, filtered: bool = False) -> float:
    frequency = str(metadata.get("data_frequency") or "").upper()
    if filtered:
        if "5_MIN" in frequency or "5 MIN" in frequency:
            return 31 * 24 * 60 * 60
        if "15_MIN" in frequency or "15 MIN" in frequency:
            return 93 * 24 * 60 * 60
        if "HOUR" in frequency:
            return 366 * 24 * 60 * 60
        if "DAY" in frequency or "DAILY" in frequency:
            return 5 * 366 * 24 * 60 * 60
    if "5_MIN" in frequency or "5 MIN" in frequency:
        return 7 * 24 * 60 * 60
    if "15_MIN" in frequency or "15 MIN" in frequency:
        return 14 * 24 * 60 * 60
    if "HOUR" in frequency:
        return 31 * 24 * 60 * 60
    if "DAY" in frequency or "DAILY" in frequency:
        return 366 * 24 * 60 * 60
    return 7 * 24 * 60 * 60


def default_fetch_csv(url: str) -> bytes:
    settings = get_settings()
    for attempt in range(settings.request_retry_times + 1):
        try:
            gridstatus_request_limiter.wait(
                min_interval_seconds=settings.gridstatus_min_interval_seconds,
            )
            with httpx.Client(
                timeout=settings.request_timeout_seconds,
                headers={"User-Agent": settings.user_agent},
                follow_redirects=True,
                trust_env=False,
            ) as client:
                response = client.get(url)
                if response.is_error:
                    failure = failure_from_response(
                        "GridStatus",
                        response,
                        detail=response.text[:240],
                        attempts=attempt + 1,
                    )
                    if failure.retryable and attempt < settings.request_retry_times:
                        time.sleep(retry_after_seconds(response, default=1 + attempt))
                        continue
                    raise failure
                return response.content
        except NetworkFailure:
            raise
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            failure = failure_from_exception("GridStatus", exc, attempts=attempt + 1)
            if failure.retryable and attempt < settings.request_retry_times:
                time.sleep(1 + attempt)
                continue
            raise failure from exc
    raise AssertionError("unreachable")


def default_fetch_json(url: str) -> dict[str, Any]:
    content = default_fetch_csv(url)
    text = content.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise NetworkFailure(
            service="GridStatus",
            kind=NetworkFailureKind.INVALID_RESPONSE,
            retryable=True,
        ) from exc
    if not isinstance(payload, dict):
        raise NetworkFailure(
            service="GridStatus",
            kind=NetworkFailureKind.INVALID_RESPONSE,
            retryable=True,
        )
    return payload


def redact_api_key(url: str) -> str:
    parts = urlsplit(url)
    query = urlencode(
        [
            (key, "***" if key.lower() == "api_key" else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def build_gridstatus_json_url(
    dataset_id: str,
    api_key: str,
    page_size: int = GRIDSTATUS_DEFAULT_PAGE_SIZE,
    cursor: str | None = "",
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    filter_column: str | None = None,
    filter_value: str | None = None,
) -> str:
    params: dict[str, Any] = {
        "page_size": page_size,
        "api_key": api_key,
    }
    if cursor is not None:
        params["cursor"] = cursor
    if start_time is not None:
        params["start_time"] = format_gridstatus_time(start_time)
    if end_time is not None:
        params["end_time"] = format_gridstatus_time(end_time)
    add_gridstatus_filter_params(params, filter_column, filter_value)
    return f"{GRIDSTATUS_QUERY_BASE_URL.format(dataset_id=dataset_id)}?{urlencode(params)}"


def add_gridstatus_filter_params(
    params: dict[str, Any],
    filter_column: str | None,
    filter_value: str | None,
) -> None:
    validate_gridstatus_filter(filter_column, filter_value)
    if not filter_column:
        return
    params["filter_column"] = filter_column
    params["filter_value"] = filter_value
    params["filter_operator"] = "="


def validate_gridstatus_filter(
    filter_column: str | None,
    filter_value: str | None,
) -> None:
    if bool(filter_column) != bool(filter_value):
        raise ValueError("GridStatus filter_column and filter_value must be provided together.")


def optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def normalize_json_rows(payload: dict[str, Any]) -> list[dict[str, str]]:
    if payload.get("status_code") not in (None, 200):
        raise RuntimeError(f"GridStatus API returned non-200 payload: {payload}")
    data = payload.get("data", [])
    if not isinstance(data, list):
        raise RuntimeError(f"GridStatus API data field is not a list: {payload}")
    return [{str(key): "" if value is None else str(value) for key, value in row.items()} for row in data]


def parse_csv_bytes(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig", errors="replace")
    if not text.strip():
        return []
    reader = csv.DictReader(StringIO(text))
    return [dict(row) for row in reader]


def read_csv_file(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as file:
        return [dict(row) for row in csv.DictReader(file)]


def read_csv_row_state(
    path: Path,
    primary_keys: list[str],
) -> tuple[int, set[tuple[Any, ...] | str]]:
    if not path.exists():
        return 0, set()

    count = 0
    seen: set[tuple[Any, ...] | str] = set()
    with path.open(newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            count += 1
            seen.add(row_key(dict(row), primary_keys))
    return count, seen


def merge_rows(batches: list[list[dict[str, str]]], metadata: dict[str, Any]) -> list[dict[str, str]]:
    primary_keys = parse_primary_keys(metadata.get("primary_key_columns_json"))
    merged: list[dict[str, str]] = []
    seen: set[tuple[Any, ...]] = set()

    for rows in batches:
        for row in rows:
            key = row_key(row, primary_keys)
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
    return merged


def row_key(row: dict[str, str], primary_keys: list[str]) -> tuple[Any, ...]:
    if primary_keys and all(key in row for key in primary_keys):
        return tuple(row.get(key, "") for key in primary_keys)
    return tuple(sorted(row.items()))


def row_key_text(row: dict[str, str], primary_keys: list[str]) -> str:
    return json.dumps(row_key(row, primary_keys), ensure_ascii=False, sort_keys=True)


def initialize_sqlite_output(output_path: Path, metadata: dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(output_path) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("CREATE TABLE IF NOT EXISTS gridstatus_rows (_row_key TEXT PRIMARY KEY)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS gridstatus_metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        metadata_items = {
            "dataset_id": metadata.get("dataset_id"),
            "download_start_time_utc": metadata.get("download_start_time_utc")
            or metadata.get("earliest_available_time_utc"),
            "download_end_time_utc": metadata.get("download_end_time_utc")
            or metadata.get("latest_available_time_utc"),
            "source": metadata.get("source"),
            "data_frequency": metadata.get("data_frequency"),
            "primary_key_columns_json": metadata.get("primary_key_columns_json"),
            "filter_column": metadata.get("download_filter_column"),
            "filter_value": metadata.get("download_filter_value"),
            "filter_operator": "=" if metadata.get("download_filter_column") else None,
            "page_size": metadata.get("download_page_size") or GRIDSTATUS_DEFAULT_PAGE_SIZE,
        }
        connection.executemany(
            """
            INSERT INTO gridstatus_metadata(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            [(key, "" if value is None else str(value)) for key, value in metadata_items.items()],
        )


def read_sqlite_row_count(output_path: Path) -> int:
    if not output_path.exists():
        return 0
    with sqlite3.connect(output_path) as connection:
        if not sqlite_table_exists(connection, "gridstatus_rows"):
            return 0
        return int(connection.execute("SELECT COUNT(*) FROM gridstatus_rows").fetchone()[0])


def read_sqlite_row_keys(output_path: Path) -> set[str]:
    if not output_path.exists():
        return set()
    with sqlite3.connect(output_path) as connection:
        if not sqlite_table_exists(connection, "gridstatus_rows"):
            return set()
        return {str(row[0]) for row in connection.execute("SELECT _row_key FROM gridstatus_rows")}


def write_sqlite_rows(
    output_path: Path,
    rows: list[dict[str, str]],
    primary_keys: list[str],
) -> None:
    if not rows:
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(output_path) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("CREATE TABLE IF NOT EXISTS gridstatus_rows (_row_key TEXT PRIMARY KEY)")
        existing_columns = sqlite_table_columns(connection, "gridstatus_rows")
        row_columns = []
        for row in rows:
            for column in row:
                if column == "_row_key":
                    continue
                if column not in row_columns:
                    row_columns.append(column)
                if column not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE gridstatus_rows ADD COLUMN {quote_identifier(column)} TEXT"
                    )
                    existing_columns.add(column)

        columns = ["_row_key", *row_columns]
        placeholders = ", ".join("?" for _ in columns)
        column_sql = ", ".join(quote_identifier(column) for column in columns)
        sql = f"INSERT OR IGNORE INTO gridstatus_rows ({column_sql}) VALUES ({placeholders})"
        connection.executemany(
            sql,
            [
                [row_key_text(row, primary_keys), *[row.get(column, "") for column in row_columns]]
                for row in rows
            ],
        )


def sqlite_table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        is not None
    )


def sqlite_table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({quote_identifier(table_name)})")}


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def parse_primary_keys(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []


def write_csv(output_path: Path, rows: list[dict[str, str]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)

    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def append_csv_rows(output_path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str]
    write_header = not output_path.exists() or output_path.stat().st_size == 0
    if write_header:
        fieldnames = list(rows[0])
    else:
        with output_path.open(newline="", encoding="utf-8-sig") as file:
            fieldnames = list(csv.DictReader(file).fieldnames or [])

    extra_columns = sorted({key for row in rows for key in row if key not in fieldnames})
    if extra_columns:
        raise RuntimeError(
            "GridStatus response columns changed during CSV download: "
            + ", ".join(extra_columns)
        )

    mode = "w" if write_header else "a"
    with output_path.open(mode, newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def checkpoint_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.name}.state.json")


def save_checkpoint(
    path: Path,
    dataset_id: str,
    cursor: str | None,
    has_next_page: bool,
    requests_made: int,
    intervals_completed: int,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    download_start: datetime | None = None,
    download_end: datetime | None = None,
    filter_column: str | None = None,
    filter_value: str | None = None,
    page_size: int | None = None,
    output_format: str | None = None,
) -> None:
    payload = {
        "dataset_id": dataset_id,
        "cursor": cursor,
        "has_next_page": has_next_page,
        "requests_made": requests_made,
        "intervals_completed": intervals_completed,
        "filter_column": filter_column,
        "filter_value": filter_value,
        "filter_operator": "=" if filter_column else None,
        "page_size": page_size,
        "output_format": output_format,
    }
    if window_start is not None:
        payload["window_start_utc"] = format_gridstatus_time(window_start)
    if window_end is not None:
        payload["window_end_utc"] = format_gridstatus_time(window_end)
    if download_start is not None:
        payload["download_start_time_utc"] = format_gridstatus_time(download_start)
    if download_end is not None:
        payload["download_end_time_utc"] = format_gridstatus_time(download_end)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_checkpoint(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def validate_checkpoint_query(
    state: dict[str, Any],
    *,
    start: datetime,
    end: datetime,
    filter_column: str | None,
    filter_value: str | None,
    page_size: int,
    output_format: str,
) -> None:
    expected = {
        "download_start_time_utc": format_gridstatus_time(start),
        "download_end_time_utc": format_gridstatus_time(end),
        "filter_column": filter_column,
        "filter_value": filter_value,
        "page_size": page_size,
        "output_format": output_format,
    }
    mismatches = [
        key
        for key, value in expected.items()
        if key in state and state.get(key) != value
    ]
    legacy_filter_mismatch = filter_column is not None and "filter_column" not in state
    if mismatches or legacy_filter_mismatch:
        fields = mismatches or ["filter_column", "filter_value"]
        raise ValueError(
            "The existing GridStatus checkpoint uses different query options "
            f"({', '.join(fields)}). Restart the download instead of resuming it."
        )


def parse_gridstatus_time(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_gridstatus_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def estimate_rows_for_range(
    metadata: dict[str, Any],
    start: datetime,
    end: datetime,
    full_start: datetime | None,
    full_end: datetime | None,
) -> int | None:
    approximate = metadata.get("number_of_rows_approximate")
    try:
        approximate_rows = int(approximate)
    except (TypeError, ValueError):
        return None
    if approximate_rows <= 0:
        return None
    if full_start is None or full_end is None or full_start >= full_end:
        return approximate_rows
    full_seconds = (full_end - full_start).total_seconds()
    selected_seconds = max(0.0, (end - start).total_seconds())
    if full_seconds <= 0:
        return approximate_rows
    return max(1, int(approximate_rows * selected_seconds / full_seconds))
