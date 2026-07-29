from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any
from uuid import uuid4
from urllib.parse import urlparse

import httpx

from powertrade_crawler.agent.schemas import ProviderResponse, ProviderToolCall
from powertrade_crawler.agent.security import redact_text


class ProviderError(RuntimeError):
    code = "provider_error"
    retryable = False


class ProviderAuthenticationError(ProviderError):
    code = "provider_authentication_error"


class ProviderQuotaError(ProviderError):
    code = "provider_quota_error"


class ProviderTimeoutError(ProviderError):
    code = "provider_timeout"
    retryable = True


class ProviderProtocolError(ProviderError):
    code = "provider_protocol_error"


class LLMProvider(ABC):
    @abstractmethod
    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> ProviderResponse:
        """Return one model response without executing any tool."""

    def list_models(self) -> list[str]:
        raise NotImplementedError


class SiliconFlowProvider(LLMProvider):
    def __init__(
        self,
        *,
        api_key: str,
        model_id: str,
        endpoint: str = "https://api.siliconflow.cn/v1",
        timeout_seconds: float = 60,
        max_tokens: int = 2048,
        extra_body: dict[str, Any] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("SiliconFlow API Key is required.")
        if not model_id.strip():
            raise ValueError("SiliconFlow model_id is required.")
        parsed_endpoint = urlparse(endpoint)
        if (
            parsed_endpoint.scheme != "https"
            or parsed_endpoint.hostname != "api.siliconflow.cn"
        ):
            raise ValueError(
                "SiliconFlow API Key may only be sent to https://api.siliconflow.cn."
            )
        self.model_id = model_id.strip()
        self.endpoint = endpoint.rstrip("/")
        self.max_tokens = max_tokens
        self.extra_body = extra_body or {}
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=timeout_seconds,
            headers={
                "Authorization": f"Bearer {api_key.strip()}",
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def list_models(self) -> list[str]:
        payload = self._request("GET", "/models")
        rows = payload.get("data", [])
        if not isinstance(rows, list):
            raise ProviderProtocolError("SiliconFlow /models returned an invalid data field.")
        return sorted(
            str(row["id"])
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        )

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> ProviderResponse:
        body: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": 0,
        }
        if self.extra_body:
            body.update(self.extra_body)
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        payload = self._request("POST", "/chat/completions", json_body=body)
        try:
            choice = payload["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderProtocolError("SiliconFlow response has no valid first choice.") from exc

        tool_calls: list[ProviderToolCall] = []
        for raw_call in message.get("tool_calls") or []:
            try:
                function = raw_call["function"]
                raw_arguments = function.get("arguments") or "{}"
                arguments = (
                    json.loads(raw_arguments)
                    if isinstance(raw_arguments, str)
                    else raw_arguments
                )
                if not isinstance(arguments, dict):
                    raise TypeError("arguments must be an object")
                tool_calls.append(
                    ProviderToolCall(
                        id=str(raw_call.get("id") or uuid4()),
                        name=str(function["name"]),
                        arguments=arguments,
                    )
                )
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ProviderProtocolError("Invalid native tool call in model response.") from exc

        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        return ProviderResponse(
            content=message.get("content"),
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason"),
            usage={
                str(key): value
                for key, value in usage.items()
                if isinstance(value, (int, float))
            },
            raw_protocol="native" if tools else "json",
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self.client.request(method, f"{self.endpoint}{path}", json=json_body)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("SiliconFlow request timed out.") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(redact_text(f"SiliconFlow request failed: {exc}")) from exc
        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError("SiliconFlow rejected the configured API Key.")
        if response.status_code == 429:
            raise ProviderQuotaError("SiliconFlow quota or rate limit was reached.")
        if response.is_error:
            raise ProviderError(f"SiliconFlow returned HTTP {response.status_code}.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderProtocolError("SiliconFlow returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise ProviderProtocolError("SiliconFlow returned a non-object JSON response.")
        return payload


class FakeProvider(LLMProvider):
    """Deterministic provider used by tests and offline evaluation."""

    def __init__(self, responses: Iterable[ProviderResponse]) -> None:
        self._responses = iter(responses)
        self.requests: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> ProviderResponse:
        self.requests.append(
            {"messages": messages, "tools": tools, "json_mode": json_mode}
        )
        try:
            return next(self._responses)
        except StopIteration as exc:
            raise ProviderProtocolError("FakeProvider has no scripted response left.") from exc

    def list_models(self) -> list[str]:
        return ["fake/elecheck-agent"]
