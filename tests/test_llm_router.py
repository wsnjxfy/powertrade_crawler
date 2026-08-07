import json

import httpx
import pytest

from powertrade_crawler.agent.provider import (
    FreeLLMRouterProvider as ElecheckRouterProvider,
)
from powertrade_crawler.llm_router import (
    FreeRouterManagementClient,
    LLMRouterConfigError,
    LLMRouterUnavailableError,
    load_free_router_config,
)
from powertrade_crawler.market_agent.provider import (
    FreeLLMRouterProvider as MarketRouterProvider,
)


def write_client_env(path, *, api_base="http://127.0.0.1:8317/v1"):
    path.write_text(
        "\n".join(
            [
                "LLM_PROVIDER=openai-compatible",
                f"LLM_API_BASE={api_base}",
                f"LLM_API_URL={api_base}/chat/completions",
                "LLM_MODEL=smart-auto",
                "LLM_API_KEY=fake-local-router-secret",
            ]
        ),
        encoding="utf-8",
    )


def test_external_router_config_does_not_expose_key(monkeypatch, tmp_path):
    env_path = tmp_path / "client-free.env"
    write_client_env(env_path)
    monkeypatch.setenv("LLM_ROUTER_CLIENT_ENV", str(env_path))

    config = load_free_router_config()

    assert config.model == "smart-auto"
    assert "fake-local-router-secret" not in repr(config)
    assert "fake-local-router-secret" not in json.dumps(config.safe_summary())
    assert config.safe_summary()["api_key"] == "configured"


def test_external_router_config_rejects_non_loopback_endpoint(tmp_path):
    env_path = tmp_path / "client-free.env"
    write_client_env(env_path, api_base="https://example.com/v1")

    with pytest.raises(LLMRouterConfigError, match="回环地址"):
        load_free_router_config(env_path)


def test_management_only_allows_available_free_providers(tmp_path):
    env_path = tmp_path / "client-free.env"
    write_client_env(env_path)
    config = load_free_router_config(env_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/v1/providers":
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {
                            "id": "free-ok",
                            "tier": "free",
                            "available": True,
                            "directModel": "provider/free-ok",
                        },
                        {
                            "id": "free-down",
                            "tier": "free",
                            "available": False,
                            "directModel": "provider/free-down",
                        },
                        {
                            "id": "paid",
                            "tier": "paid",
                            "available": True,
                            "directModel": "provider/paid",
                        },
                    ],
                },
            )
        if request.url.path == "/v1/alerts":
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {
                            "providerId": "free-down",
                            "severity": "error",
                            "message": "expired",
                            "secret": "must-not-return",
                        }
                    ],
                },
            )
        raise AssertionError(request.url)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    router = FreeRouterManagementClient(config, client=client)

    assert router.ensure_strategy_available("smart-auto")["mode"] == "automatic"
    assert router.ensure_strategy_available("provider/free-ok")["mode"] == "fixed_provider"
    with pytest.raises(LLMRouterUnavailableError, match="不可用"):
        router.ensure_strategy_available("provider/free-down")
    with pytest.raises(LLMRouterConfigError, match="tier=free"):
        router.ensure_strategy_available("provider/paid")
    assert router.list_alerts() == [
        {
            "providerId": "free-down",
            "severity": "error",
            "message": "expired",
        }
    ]


@pytest.mark.parametrize("provider_class", [ElecheckRouterProvider, MarketRouterProvider])
def test_agent_providers_capture_router_response_headers(provider_class):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={
                "x-llm-router-provider": "free-ok",
                "x-llm-router-upstream-model": "upstream/model",
                "x-llm-router-alert-count": "2",
            },
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 3},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = provider_class(
        api_key="fake-local-router-secret",
        model_id="smart-auto",
        client=client,
    )

    response = provider.complete(messages=[{"role": "user", "content": "hi"}])

    assert requests[0]["model"] == "smart-auto"
    assert response.router_provider == "free-ok"
    assert response.upstream_model == "upstream/model"
    assert response.router_alert_count == 2


def test_router_unavailable_has_explicit_no_paid_fallback_message(tmp_path):
    env_path = tmp_path / "client-free.env"
    write_client_env(env_path)
    config = load_free_router_config(env_path)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    router = FreeRouterManagementClient(config, client=client)

    with pytest.raises(LLMRouterUnavailableError, match="未调用任何外部或付费模型"):
        router.ensure_running(start_if_needed=False)
