import csv
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from threading import Event
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from powertrade_crawler.config import get_settings


GRIDSTATUS_QUERY_BASE_URL = "https://api.gridstatus.io/v1/datasets/{dataset_id}/query"


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
        min_interval_seconds: float = 1.2,
        batch_size: int = 50,
        batch_pause_seconds: float = 30.0,
        max_requests_per_minute: int = 30,
        max_requests_per_hour: int = 600,
    ) -> None:
        self.min_interval_seconds = min_interval_seconds
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
    limit: int = 1000,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    download: bool = True,
) -> str:
    params: dict[str, Any] = {
        "return_format": "csv",
        "limit": limit,
        "api_key": api_key,
    }
    if download:
        params["download"] = "true"
    if start_time is not None:
        params["start_time"] = format_gridstatus_time(start_time)
    if end_time is not None:
        params["end_time"] = format_gridstatus_time(end_time)
    return f"{GRIDSTATUS_QUERY_BASE_URL.format(dataset_id=dataset_id)}?{urlencode(params)}"


def download_dataset_csv_adaptive(
    metadata: dict[str, Any],
    output_path: Path,
    limit: int = 1000,
    min_interval_seconds: float = 1.2,
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
    settings = get_settings()
    api_key = settings.gridstatus_api_key
    if not api_key:
        raise ValueError("GRIDSTATUS_API_KEY is required to download GridStatus CSV data.")

    dataset_id = str(metadata["dataset_id"])
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
        window_seconds or default_window_seconds(metadata),
    )
    state_path = checkpoint_path(output_path)
    state = load_checkpoint(state_path)
    primary_keys = parse_primary_keys(metadata.get("primary_key_columns_json"))
    if state and state.get("dataset_id") == dataset_id:
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
            merged_rows: list[dict[str, str]] = []
        else:
            merged_rows = read_csv_file(output_path)
            seen = {row_key(row, primary_keys) for row in merged_rows}
            rows_written = len(merged_rows)
        if progress:
            emit_progress(
                progress,
                dataset_id=dataset_id,
                message=f"发现断点文件：已存在 {rows_written} 行，将从上次进度继续下载",
                rows_written=rows_written,
                requests_made=requests_made,
                intervals_pending=1 if has_next_page else 0,
                estimated_total_rows=estimated_total_rows,
            )
    else:
        window_start = start
        window_end = min(end, window_start + timedelta(seconds=effective_window_seconds))
        cursor = ""
        has_next_page = True
        requests_made = 0
        intervals_completed = 0
        merged_rows: list[dict[str, str]] = []
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
                intervals_pending=1 if has_next_page else 0,
                estimated_total_rows=estimated_total_rows,
            )

        limiter.wait(progress, control)
        control.raise_if_cancelled()
        url = build_gridstatus_json_url(
            dataset_id=dataset_id,
            api_key=api_key,
            page_size=limit,
            cursor=cursor,
            start_time=window_start,
            end_time=window_end,
        )
        payload = fetch_json(url)
        rows = normalize_json_rows(payload)
        if len(rows) > limit:
            raise RuntimeError(
                f"GridStatus returned {len(rows)} rows in one page, exceeding requested "
                f"page_size={limit}. Download stopped to avoid unexpected API usage."
            )
        meta = payload.get("meta") or {}
        has_next_page = bool(meta.get("hasNextPage") or meta.get("has_next_page"))
        cursor = meta.get("cursor") or None
        requests_made += 1

        if progress:
            emit_progress(
                progress,
                dataset_id=dataset_id,
                message=f"返回 {len(rows)} 行；hasNextPage={has_next_page}",
                rows_written=rows_written,
                requests_made=requests_made,
                intervals_pending=1 if has_next_page else 0,
                estimated_total_rows=estimated_total_rows,
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
            merged_rows.extend(new_rows)
            rows_written = len(merged_rows)
            write_csv(output_path, merged_rows)
        save_checkpoint(
            state_path,
            dataset_id=dataset_id,
            cursor=cursor,
            has_next_page=has_next_page,
            requests_made=requests_made,
            intervals_completed=intervals_completed,
            window_start=window_start,
            window_end=window_end,
        )
        if progress:
            emit_progress(
                progress,
                dataset_id=dataset_id,
                message=f"已保存断点：当前写入 {rows_written} 行",
                rows_written=rows_written,
                requests_made=requests_made,
                intervals_pending=1 if has_next_page else 0,
                estimated_total_rows=estimated_total_rows,
            )

        if has_next_page and not cursor:
            raise RuntimeError("API indicated hasNextPage=true but did not provide a cursor.")

    if output_format == "csv":
        write_csv(output_path, merged_rows)
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
) -> None:
    if estimated_total_rows and estimated_total_rows > 0:
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


def default_window_seconds(metadata: dict[str, Any]) -> float:
    frequency = str(metadata.get("data_frequency") or "").upper()
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
    last_error: Exception | None = None
    safe_url = redact_api_key(url)
    for attempt in range(settings.request_retry_times + 1):
        try:
            with httpx.Client(
                timeout=settings.request_timeout_seconds,
                headers={"User-Agent": settings.user_agent},
                follow_redirects=True,
                trust_env=False,
            ) as client:
                response = client.get(url)
                response.raise_for_status()
                return response.content
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:500]
            raise RuntimeError(
                f"GridStatus request failed with HTTP {exc.response.status_code}: {body}"
            ) from exc
        except httpx.TimeoutException as exc:
            last_error = exc
            if attempt < settings.request_retry_times:
                time.sleep(1 + attempt)
                continue
            raise RuntimeError(
                f"GridStatus request timed out after {settings.request_timeout_seconds} seconds: "
                f"{safe_url}"
            ) from exc
        except httpx.RequestError as exc:
            last_error = exc
            if attempt < settings.request_retry_times:
                time.sleep(1 + attempt)
                continue
            raise RuntimeError(f"GridStatus request failed: {exc}. URL: {safe_url}") from exc

    raise RuntimeError(f"GridStatus request failed after retries: {safe_url}") from last_error


def default_fetch_json(url: str) -> dict[str, Any]:
    content = default_fetch_csv(url)
    text = content.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"API did not return JSON: {text[:300]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("API JSON response was not an object.")
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
    page_size: int = 1000,
    cursor: str | None = "",
    start_time: datetime | None = None,
    end_time: datetime | None = None,
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
    return f"{GRIDSTATUS_QUERY_BASE_URL.format(dataset_id=dataset_id)}?{urlencode(params)}"


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
) -> None:
    payload = {
        "dataset_id": dataset_id,
        "cursor": cursor,
        "has_next_page": has_next_page,
        "requests_made": requests_made,
        "intervals_completed": intervals_completed,
    }
    if window_start is not None:
        payload["window_start_utc"] = format_gridstatus_time(window_start)
    if window_end is not None:
        payload["window_end_utc"] = format_gridstatus_time(window_end)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_checkpoint(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


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
