from __future__ import annotations

from datetime import UTC, datetime


def as_utc_naive(value: datetime) -> datetime:
    """Normalize aware datetimes to UTC while retaining the project's naive-UTC storage."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def parse_utc_naive(value: str, *, compact_format: str | None = None) -> datetime:
    stripped = value.strip()
    if len(stripped) == 10:
        return datetime.fromisoformat(stripped)
    if compact_format and stripped.isdigit():
        return datetime.strptime(stripped, compact_format)
    parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    return as_utc_naive(parsed)


def format_utc_z(value: datetime, *, timespec: str = "minutes") -> str:
    normalized = as_utc_naive(value)
    if timespec == "minutes":
        return normalized.strftime("%Y-%m-%dT%H:%MZ")
    if timespec == "seconds":
        return normalized.strftime("%Y-%m-%dT%H:%M:%SZ")
    raise ValueError(f"Unsupported UTC timespec: {timespec}")
