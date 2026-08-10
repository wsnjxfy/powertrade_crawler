import json

from powertrade_crawler.credential_setup import (
    LLM_PLATFORM_GUIDES,
    POWER_CREDENTIAL_GUIDES,
    RouterSnapshot,
    assistant_credential_setup_payload,
    credential_is_configured,
    inspect_router,
    llm_platform_display_status,
    llm_platform_status,
    providers_for_platform,
)


def test_power_guides_cover_all_user_facing_data_sources():
    assert [guide.key for guide in POWER_CREDENTIAL_GUIDES] == [
        "gridstatus",
        "entsoe",
        "elecheck",
        "elexon",
        "gzpec",
    ]
    assert {
        guide.key for guide in POWER_CREDENTIAL_GUIDES if guide.requirement == "collection_required"
    } == {"gridstatus", "entsoe", "elecheck"}
    assert {
        guide.key for guide in POWER_CREDENTIAL_GUIDES if guide.requirement == "not_required"
    } == {"elexon", "gzpec"}
    assert len({guide.credential_name for guide in POWER_CREDENTIAL_GUIDES}) == 4
    assert all(guide.steps and guide.note for guide in POWER_CREDENTIAL_GUIDES)


def test_credential_status_rejects_known_placeholders():
    assert credential_is_configured("gridstatus_api_key", "real-grid-key") is True
    assert (
        credential_is_configured(
            "gridstatus_api_key",
            "replace-with-your-gridstatus-api-key",
        )
        is False
    )
    assert credential_is_configured("entsoe_security_token", "") is False


def test_llm_platforms_are_optional_and_cover_router_provider_families():
    providers = (
        {
            "id": "nvidia-example",
            "name": "NVIDIA example",
            "tier": "free",
            "available": True,
        },
        {
            "id": "github-models",
            "name": "GitHub Models",
            "tier": "free",
            "available": False,
        },
    )
    nvidia = next(guide for guide in LLM_PLATFORM_GUIDES if guide.key == "nvidia")
    github = next(guide for guide in LLM_PLATFORM_GUIDES if guide.key == "github")
    gemini = next(guide for guide in LLM_PLATFORM_GUIDES if guide.key == "gemini")

    assert len(providers_for_platform(nvidia, providers)) == 1
    assert llm_platform_status(nvidia, providers) == "已接入 · 可用"
    assert llm_platform_status(github, providers) == "已接入 · 不可用"
    assert llm_platform_status(gemini, providers) == "未接入 · 可选"
    assert len({guide.key for guide in LLM_PLATFORM_GUIDES}) == len(LLM_PLATFORM_GUIDES)
    assert len({guide.credential_url for guide in LLM_PLATFORM_GUIDES}) == len(
        LLM_PLATFORM_GUIDES
    )
    assert all(
        guide.credential_url.startswith("https://") and guide.steps
        for guide in LLM_PLATFORM_GUIDES
    )


def test_router_snapshot_counts_only_available_free_providers(tmp_path):
    snapshot = RouterSnapshot(
        configured=True,
        reachable=True,
        config_path=tmp_path / "client-free.env",
        message="ok",
        providers=(
            {"tier": "free", "available": True},
            {"tier": "free", "available": False},
            {"tier": "paid", "available": True},
        ),
    )

    assert snapshot.available_provider_count == 1


def test_llm_platform_status_is_unknown_when_router_cannot_be_reached(tmp_path):
    platform = next(guide for guide in LLM_PLATFORM_GUIDES if guide.key == "gemini")
    snapshot = RouterSnapshot(
        configured=True,
        reachable=False,
        config_path=tmp_path / "client-free.env",
        message="offline",
    )

    assert llm_platform_display_status(platform, snapshot) == "状态未知 · 可选"


def test_router_inspection_hides_unexpected_config_error_details(monkeypatch, tmp_path):
    config_path = tmp_path / "client-free.env"
    monkeypatch.setattr(
        "powertrade_crawler.credential_setup.default_client_env_path",
        lambda: config_path,
    )

    def fail_to_load(_path):
        raise RuntimeError("do-not-show-this-detail")

    monkeypatch.setattr(
        "powertrade_crawler.credential_setup.load_free_router_config",
        fail_to_load,
    )

    snapshot = inspect_router()

    assert snapshot.configured is False
    assert snapshot.reachable is False
    assert snapshot.message == "读取本地免费模型网关配置失败。"


def test_agent_setup_payload_reports_status_without_secret_values(monkeypatch, tmp_path):
    sentinel = "super-secret-value-that-must-never-leak"
    monkeypatch.setattr(
        "powertrade_crawler.credential_setup.get_credential",
        lambda _name: sentinel,
    )
    monkeypatch.setattr(
        "powertrade_crawler.credential_setup.inspect_router",
        lambda **_kwargs: RouterSnapshot(
            configured=True,
            reachable=True,
            config_path=tmp_path / "client-free.env",
            message="ok",
            providers=({"tier": "free", "available": True, "id": "gemini-test"},),
        ),
    )

    payload = assistant_credential_setup_payload()
    serialized = json.dumps(payload, ensure_ascii=False)

    assert sentinel not in serialized
    assert payload["collection_configured"] == 3
    assert payload["router"]["available_free_providers"] == 1
    assert payload["secrets_included"] is False
