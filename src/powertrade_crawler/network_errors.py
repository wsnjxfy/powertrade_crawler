from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Any

import httpx


class NetworkFailureKind(StrEnum):
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    CLIENT_ERROR = "client_error"
    INVALID_RESPONSE = "invalid_response"


class NetworkFailure(RuntimeError):
    """A sanitized, user-actionable failure raised by every data-source client."""

    def __init__(
        self,
        *,
        service: str,
        kind: NetworkFailureKind,
        detail: str | None = None,
        status_code: int | None = None,
        retryable: bool = False,
        attempts: int = 1,
    ) -> None:
        self.service = service
        self.kind = kind
        self.detail = clean_error_detail(detail)
        self.status_code = status_code
        self.retryable = retryable
        self.attempts = max(1, attempts)
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        attempt_note = f"，已尝试 {self.attempts} 次" if self.attempts > 1 else ""
        if self.kind == NetworkFailureKind.TIMEOUT:
            return f"{self.service} 请求超时{attempt_note}。请检查网络后重试或缩短日期范围。"
        if self.kind == NetworkFailureKind.CONNECTION:
            return f"{self.service} 无法连接{attempt_note}。请检查网络、代理和目标网站状态后重试。"
        if self.kind == NetworkFailureKind.UNAUTHORIZED:
            return (
                f"{self.service} 凭据无效或已过期（HTTP 401）。"
                "请在 API 配置向导中重新配置并验证凭据。"
            )
        if self.kind == NetworkFailureKind.FORBIDDEN:
            return (
                f"{self.service} 拒绝访问（HTTP 403）。"
                "请确认账号已获接口权限、凭据与所选数据集相匹配。"
            )
        if self.kind == NetworkFailureKind.RATE_LIMITED:
            return (
                f"{self.service} 请求过于频繁（HTTP 429{attempt_note}）。"
                "请稍后重试、缩短日期范围或降低定时任务频率。"
            )
        if self.kind == NetworkFailureKind.SERVER_ERROR:
            status = f"HTTP {self.status_code}" if self.status_code else "服务器错误"
            return (
                f"{self.service} 服务暂时异常（{status}{attempt_note}）。"
                "本次结果不能视为无新数据，请稍后重试。"
            )
        if self.kind == NetworkFailureKind.INVALID_RESPONSE:
            return (
                f"{self.service} 返回了无法解析的数据{attempt_note}。"
                "可能是接口变更或临时异常，请查看任务日志后重试。"
            )
        status = f"HTTP {self.status_code}" if self.status_code else "请求错误"
        detail = f": {self.detail}" if self.detail else ""
        return f"{self.service} 请求被拒绝（{status}{detail}）。请检查请求参数后重试。"

    def as_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "failure_kind": self.kind.value,
            "status_code": self.status_code,
            "retryable": self.retryable,
            "attempts": self.attempts,
        }


def failure_from_response(
    service: str,
    response: httpx.Response,
    *,
    detail: str | None = None,
    attempts: int = 1,
) -> NetworkFailure:
    return failure_from_status(
        service,
        response.status_code,
        detail=detail,
        attempts=attempts,
    )


def failure_from_status(
    service: str,
    status: int,
    *,
    detail: str | None = None,
    attempts: int = 1,
) -> NetworkFailure:
    if status == 401:
        kind = NetworkFailureKind.UNAUTHORIZED
        retryable = False
    elif status == 403:
        kind = NetworkFailureKind.FORBIDDEN
        retryable = False
    elif status == 429:
        kind = NetworkFailureKind.RATE_LIMITED
        retryable = True
    elif 500 <= status <= 599:
        kind = NetworkFailureKind.SERVER_ERROR
        retryable = True
    else:
        kind = NetworkFailureKind.CLIENT_ERROR
        retryable = False
    return NetworkFailure(
        service=service,
        kind=kind,
        detail=detail,
        status_code=status,
        retryable=retryable,
        attempts=attempts,
    )


def failure_from_exception(
    service: str,
    exc: Exception,
    *,
    attempts: int = 1,
) -> NetworkFailure:
    if isinstance(exc, NetworkFailure):
        return exc
    if isinstance(exc, httpx.TimeoutException):
        kind = NetworkFailureKind.TIMEOUT
    elif isinstance(exc, httpx.RequestError):
        kind = NetworkFailureKind.CONNECTION
    else:
        kind = NetworkFailureKind.INVALID_RESPONSE
    return NetworkFailure(
        service=service,
        kind=kind,
        retryable=True,
        attempts=attempts,
    )


def retry_after_seconds(
    response: httpx.Response,
    *,
    default: float,
    maximum: float = 60.0,
) -> float:
    raw_value = response.headers.get("Retry-After", "").strip()
    delay = default
    if raw_value:
        try:
            delay = float(raw_value)
        except ValueError:
            try:
                target = parsedate_to_datetime(raw_value)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=UTC)
                delay = (target - datetime.now(UTC)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                delay = default
    return max(0.0, min(float(maximum), delay))


def clean_error_detail(value: str | None) -> str | None:
    if value is None:
        return None
    compact = " ".join(str(value).split())[:240]
    return compact or None
