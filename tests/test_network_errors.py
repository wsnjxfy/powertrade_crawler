from __future__ import annotations

import httpx
import pytest

from powertrade_crawler.clients.elecheck import ElecheckClient
from powertrade_crawler.clients.elexon import ElexonClient
from powertrade_crawler.clients.entsoe import EntsoeClient
from powertrade_crawler.clients.gridstatus import GridStatusClient
from powertrade_crawler.network_errors import (
    NetworkFailure,
    NetworkFailureKind,
    failure_from_response,
    retry_after_seconds,
)


@pytest.mark.parametrize(
    ("status", "kind", "retryable"),
    [
        (401, NetworkFailureKind.UNAUTHORIZED, False),
        (403, NetworkFailureKind.FORBIDDEN, False),
        (429, NetworkFailureKind.RATE_LIMITED, True),
        (500, NetworkFailureKind.SERVER_ERROR, True),
        (503, NetworkFailureKind.SERVER_ERROR, True),
        (400, NetworkFailureKind.CLIENT_ERROR, False),
    ],
)
def test_http_statuses_have_one_shared_failure_classification(status, kind, retryable):
    response = httpx.Response(status, request=httpx.Request("GET", "https://example.test"))

    failure = failure_from_response("TestSource", response, detail="safe detail")

    assert failure.kind == kind
    assert failure.retryable is retryable
    assert failure.status_code == status
    assert failure.as_dict()["failure_kind"] == kind.value


def test_retry_after_handles_invalid_values_and_caps_long_delays():
    invalid = httpx.Response(429, headers={"Retry-After": "not-a-date"})
    excessive = httpx.Response(429, headers={"Retry-After": "600"})

    assert retry_after_seconds(invalid, default=3) == 3
    assert retry_after_seconds(excessive, default=3) == 60


def test_entsoe_final_rate_limit_keeps_429_reason_without_exposing_token():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(429, request=request, headers={"Retry-After": "0"})

    client = EntsoeClient.__new__(EntsoeClient)
    client.retry_times = 0
    client.min_interval_seconds = 0
    client.last_request_at = 0
    client.client = httpx.Client(transport=httpx.MockTransport(handler))

    with pytest.raises(NetworkFailure) as exc_info:
        client.get({"securityToken": "must-not-appear"})

    assert attempts == 1
    assert exc_info.value.kind == NetworkFailureKind.RATE_LIMITED
    assert "must-not-appear" not in str(exc_info.value)


def test_elexon_retries_server_error_but_not_forbidden(monkeypatch):
    attempts = 0

    def transient_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503 if attempts == 1 else 200, request=request, json={"data": []})

    monkeypatch.setattr("powertrade_crawler.clients.elexon.sleep", lambda _seconds: None)
    client = ElexonClient.__new__(ElexonClient)
    client.retry_times = 2
    client.min_interval_seconds = 0
    client.last_request_at = 0
    client.client = httpx.Client(transport=httpx.MockTransport(transient_handler))

    assert client.get(path="/test", params={}).status_code == 200
    assert attempts == 2

    forbidden_attempts = 0

    def forbidden_handler(request: httpx.Request) -> httpx.Response:
        nonlocal forbidden_attempts
        forbidden_attempts += 1
        return httpx.Response(403, request=request)

    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(forbidden_handler))
    with pytest.raises(NetworkFailure) as exc_info:
        client.get(path="/test", params={})
    assert forbidden_attempts == 1
    assert exc_info.value.kind == NetworkFailureKind.FORBIDDEN


def test_gridstatus_timeout_reports_attempt_count(monkeypatch):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("slow", request=request)

    monkeypatch.setattr("powertrade_crawler.clients.gridstatus.sleep", lambda _seconds: None)
    client = GridStatusClient.__new__(GridStatusClient)
    client.retry_times = 1
    client.min_interval_seconds = 0
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(client, "wait_for_rate_limit", lambda: None)

    with pytest.raises(NetworkFailure) as exc_info:
        client.get("/v1/test", params={})

    assert attempts == 2
    assert exc_info.value.kind == NetworkFailureKind.TIMEOUT
    assert exc_info.value.attempts == 2
    assert "已尝试 2 次" in str(exc_info.value)


def test_elecheck_does_not_retry_403_and_retries_5xx_payload(monkeypatch):
    forbidden_attempts = 0

    def forbidden_handler(request: httpx.Request) -> httpx.Response:
        nonlocal forbidden_attempts
        forbidden_attempts += 1
        return httpx.Response(403, request=request)

    client = ElecheckClient.__new__(ElecheckClient)
    client.retry_times = 2
    client.client = httpx.Client(
        base_url="https://example.test",
        transport=httpx.MockTransport(forbidden_handler),
    )
    with pytest.raises(NetworkFailure) as exc_info:
        client.get_purchasing("provinceList", {})
    assert forbidden_attempts == 1
    assert exc_info.value.kind == NetworkFailureKind.FORBIDDEN

    payload_attempts = 0

    def payload_handler(request: httpx.Request) -> httpx.Response:
        nonlocal payload_attempts
        payload_attempts += 1
        payload = {"code": 500, "message": "temporary"}
        if payload_attempts == 2:
            payload = {"code": 200, "data": []}
        return httpx.Response(200, request=request, json=payload)

    monkeypatch.setattr("powertrade_crawler.clients.elecheck.sleep", lambda _seconds: None)
    client.client.close()
    client.client = httpx.Client(
        base_url="https://example.test",
        transport=httpx.MockTransport(payload_handler),
    )
    assert client.get_purchasing("provinceList", {}) == {"code": 200, "data": []}
    assert payload_attempts == 2
