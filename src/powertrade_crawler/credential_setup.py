from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

from powertrade_crawler.credentials import CredentialName, get_credential
from powertrade_crawler.llm_router import (
    FreeRouterManagementClient,
    LLMRouterError,
    default_client_env_path,
    load_free_router_config,
)


Requirement = Literal["collection_required", "agent_required", "optional", "not_required"]


@dataclass(frozen=True)
class PowerCredentialGuide:
    key: str
    source: str
    requirement: Requirement
    requirement_label: str
    capability: str
    credential_name: CredentialName | None
    credential_label: str
    account_url: str | None
    credential_url: str | None
    steps: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class LLMPlatformGuide:
    key: str
    platform: str
    provider_prefixes: tuple[str, ...]
    credential_label: str
    credential_url: str
    steps: tuple[str, ...]
    note: str = ""


@dataclass(frozen=True)
class RouterSnapshot:
    configured: bool
    reachable: bool
    config_path: Path
    message: str
    providers: tuple[dict[str, object], ...] = ()

    @property
    def available_provider_count(self) -> int:
        return sum(
            1
            for provider in self.providers
            if provider.get("tier") == "free" and provider.get("available") is True
        )


POWER_CREDENTIAL_GUIDES = (
    PowerCredentialGuide(
        key="gridstatus",
        source="GridStatus 北美",
        requirement="collection_required",
        requirement_label="实时采集必需",
        capability="下载和更新北美 ISO/RTO 市场数据",
        credential_name="gridstatus_api_key",
        credential_label="GridStatus API Key",
        account_url="https://www.gridstatus.io/products/api",
        credential_url="https://www.gridstatus.io/settings/api",
        steps=(
            "打开 GridStatus API 产品页并注册或登录账号。",
            "进入 Settings → API，创建或复制 API Key。",
            "将完整 Key 粘贴到下方输入框并保存。",
            "回到 GridStatus 页面执行小范围查询；部分数据集受账号套餐限制。",
        ),
        note="只在使用 GridStatus 在线下载时必需；浏览已经保存的本地数据不需要。",
    ),
    PowerCredentialGuide(
        key="entsoe",
        source="ENTSO-E 欧洲",
        requirement="collection_required",
        requirement_label="实时采集必需",
        capability="查询 ENTSO-E Transparency Platform Web API",
        credential_name="entsoe_security_token",
        credential_label="ENTSO-E Security Token",
        account_url="https://transparency.entsoe.eu/",
        credential_url=(
            "https://transparencyplatform.zendesk.com/hc/en-us/articles/"
            "12845911031188-How-to-get-security-token"
        ),
        steps=(
            "在 Transparency Platform 注册账号并完成邮件验证。",
            "按官方说明发送主题为 RESTful API access 的申请邮件，等待开通。",
            "收到确认后登录平台，在 My Account 中生成 Security Token。",
            "粘贴 Security Token 并保存；不要填写 File Library 的短期 Bearer Token。",
        ),
        note="官方说明开通可能需要最多 3 个工作日；本项目使用 Web API Security Token。",
    ),
    PowerCredentialGuide(
        key="elecheck",
        source="Elecheck 易能电易查",
        requirement="collection_required",
        requirement_label="实时采集必需",
        capability="采集现货、代理购电和增量机制电价",
        credential_name="elecheck_authorization",
        credential_label="Elecheck Authorization",
        account_url=None,
        credential_url=None,
        steps=(
            "在微信中搜索并登录“易能电易查”小程序，确认账号有权使用相关数据。",
            "当前没有公开的开发者 API Key 自助申请页；请向数据服务方或项目管理员申请授权。",
            "将对方提供的完整 Authorization 粘贴到下方输入框并保存。",
            "若采集返回 401，说明授权已失效，需要重新向授权方获取。",
        ),
        note="不要使用他人账号、绕过登录或把 Authorization 发到聊天、截图和日志中。",
    ),
    PowerCredentialGuide(
        key="elexon",
        source="Elexon 英国",
        requirement="not_required",
        requirement_label="无需配置",
        capability="查询英国 Insights API 的负荷、发电、价格和平衡数据",
        credential_name=None,
        credential_label="无需 API Key",
        account_url="https://bmrs.elexon.co.uk/api-documentation/introduction",
        credential_url="https://data.elexon.co.uk/swagger/index.html",
        steps=(
            "无需注册开发者账号，也无需在软件中填写 API Key。",
            "直接进入 Elexon 英国页面，选择数据集和日期范围。",
            "先使用 dry-run 检查请求，再执行采集。",
        ),
        note="项目保留了兼容槽位，但当前 Insights API 的新用户不需要配置它。",
    ),
    PowerCredentialGuide(
        key="gzpec",
        source="广州电力交易中心",
        requirement="not_required",
        requirement_label="无需配置",
        capability="采集公开市场信息与新闻",
        credential_name=None,
        credential_label="无需 API Key",
        account_url="http://www.gzpec.cn/",
        credential_url=None,
        steps=(
            "该模块读取广州电力交易中心公开信息，不需要注册开发者账号。",
            "可通过定时任务或多数据源 Agent 发起公开信息采集。",
        ),
        note="网站可访问且网络正常即可；公开页面结构变化时可能需要更新采集规则。",
    ),
)


LLM_PLATFORM_GUIDES = (
    LLMPlatformGuide(
        key="gemini",
        platform="Google Gemini",
        provider_prefixes=("gemini-",),
        credential_label="Gemini API Key",
        credential_url="https://aistudio.google.com/apikey",
        steps=(
            "登录 Google AI Studio 并接受服务条款。",
            "在 API Keys 页面创建或复制 Key。",
            "打开本机网关管理页，把 Key 添加或替换到 Gemini 免费渠道。",
        ),
    ),
    LLMPlatformGuide(
        key="modelscope",
        platform="ModelScope 魔搭",
        provider_prefixes=("modelscope-",),
        credential_label="SDK Token",
        credential_url="https://modelscope.cn/my/myaccesstoken",
        steps=(
            "注册并登录 ModelScope。",
            "在个人中心创建或复制 SDK Token。",
            "在本机网关管理页配置对应免费推理渠道。",
        ),
    ),
    LLMPlatformGuide(
        key="nvidia",
        platform="NVIDIA NIM",
        provider_prefixes=("nvidia-",),
        credential_label="NVIDIA API Key",
        credential_url="https://build.nvidia.com/settings/api-keys",
        steps=(
            "登录 NVIDIA Developer 账号。",
            "在 API Keys 页面生成用于免费 serverless API 的 Key。",
            "在本机网关管理页配置一个或多个 NVIDIA NIM 免费模型渠道。",
        ),
    ),
    LLMPlatformGuide(
        key="groq",
        platform="GroqCloud",
        provider_prefixes=("groq-",),
        credential_label="Groq API Key",
        credential_url="https://console.groq.com/keys",
        steps=(
            "注册并登录 GroqCloud。",
            "选择项目，在 API Keys 页面创建 Key。",
            "在本机网关管理页配置 Groq 免费渠道并测试。",
        ),
    ),
    LLMPlatformGuide(
        key="cloudflare",
        platform="Cloudflare Workers AI",
        provider_prefixes=("cloudflare-",),
        credential_label="API Token + Account ID",
        credential_url="https://dash.cloudflare.com/profile/api-tokens",
        steps=(
            "登录 Cloudflare，确认账号可使用 Workers AI。",
            "创建只包含所需 Workers AI 权限的 API Token，并记录 Account ID。",
            "在本机网关管理页按渠道要求配置凭据并测试。",
        ),
    ),
    LLMPlatformGuide(
        key="zhipu",
        platform="智谱 AI 开放平台",
        provider_prefixes=("zhipu-",),
        credential_label="API Key",
        credential_url="https://open.bigmodel.cn/usercenter/proj-mgmt/apikeys",
        steps=(
            "使用手机号或账号登录智谱 AI 开放平台。",
            "在项目管理的 API Keys 页面创建或复制 Key。",
            "在本机网关管理页配置可用的免费模型渠道。",
        ),
    ),
    LLMPlatformGuide(
        key="github",
        platform="GitHub Models",
        provider_prefixes=("github-",),
        credential_label="GitHub PAT（models 权限）",
        credential_url="https://github.com/settings/tokens",
        steps=(
            "登录 GitHub，进入 Personal access tokens。",
            "按 GitHub Models 官方说明创建具有 models 权限的 Token。",
            "在本机网关管理页配置 GitHub Models 渠道并测试。",
        ),
    ),
    LLMPlatformGuide(
        key="mistral",
        platform="Mistral La Plateforme",
        provider_prefixes=("mistral-",),
        credential_label="Mistral API Key",
        credential_url="https://console.mistral.ai/api-keys/",
        steps=(
            "注册并登录 Mistral La Plateforme。",
            "在 API Keys 页面创建 Key，并确认账号当前额度策略。",
            "在本机网关管理页配置 Mistral 免费渠道并测试。",
        ),
    ),
    LLMPlatformGuide(
        key="openrouter",
        platform="OpenRouter",
        provider_prefixes=("openrouter-",),
        credential_label="OpenRouter API Key",
        credential_url="https://openrouter.ai/settings/keys",
        steps=(
            "注册并登录 OpenRouter。",
            "在 Keys 页面创建 API Key。",
            "在本机网关管理页配置只使用免费模型路由的渠道。",
        ),
    ),
    LLMPlatformGuide(
        key="siliconflow",
        platform="硅基流动 SiliconFlow",
        provider_prefixes=("siliconflow-",),
        credential_label="SiliconFlow API Key",
        credential_url="https://cloud.siliconflow.cn/account/ak",
        steps=(
            "注册并登录硅基流动控制台。",
            "在 API 密钥页面创建 Key。",
            "在本机网关管理页配置免费模型渠道；不要填入项目旧兼容槽位。",
        ),
        note="项目中的 siliconflow_api_key 是旧版直连兼容项，当前两套 Agent 不读取它。",
    ),
)


_PLACEHOLDERS: Mapping[CredentialName, frozenset[str]] = {
    "gridstatus_api_key": frozenset({"replace-with-your-gridstatus-api-key"}),
    "entsoe_security_token": frozenset({"replace-with-your-entsoe-security-token"}),
    "elexon_api_key": frozenset({"replace-with-your-elexon-api-key"}),
    "elecheck_authorization": frozenset(),
    "siliconflow_api_key": frozenset(),
}


def credential_is_configured(name: CredentialName, value: str | None = None) -> bool:
    resolved = get_credential(name) if value is None else value
    normalized = (resolved or "").strip()
    return bool(normalized and normalized not in _PLACEHOLDERS[name])


def power_credential_status(guide: PowerCredentialGuide) -> tuple[str, bool | None]:
    if guide.credential_name is None:
        return "无需配置", None
    configured = credential_is_configured(guide.credential_name)
    return ("已配置", True) if configured else ("待配置", False)


def collection_setup_progress() -> tuple[int, int]:
    required = [
        guide
        for guide in POWER_CREDENTIAL_GUIDES
        if guide.requirement == "collection_required" and guide.credential_name is not None
    ]
    configured = sum(
        1 for guide in required if credential_is_configured(guide.credential_name)
    )
    return configured, len(required)


def inspect_router(*, start_if_needed: bool = False) -> RouterSnapshot:
    path = default_client_env_path()
    try:
        config = load_free_router_config(path)
    except LLMRouterError as exc:
        return RouterSnapshot(False, False, path, str(exc))
    except Exception:
        return RouterSnapshot(False, False, path, "读取本地免费模型网关配置失败。")

    try:
        with FreeRouterManagementClient(config, timeout_seconds=2) as client:
            client.ensure_running(start_if_needed=start_if_needed)
            providers = tuple(client.list_providers())
    except LLMRouterError as exc:
        return RouterSnapshot(True, False, path, str(exc))
    except Exception:
        return RouterSnapshot(True, False, path, "本地免费模型网关检测失败。")
    return RouterSnapshot(
        True,
        True,
        path,
        "本地免费模型网关已连接。",
        providers,
    )


def providers_for_platform(
    platform: LLMPlatformGuide,
    providers: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    return tuple(
        provider
        for provider in providers
        if any(
            str(provider.get("id") or "").startswith(prefix)
            for prefix in platform.provider_prefixes
        )
    )


def llm_platform_status(
    platform: LLMPlatformGuide,
    providers: tuple[dict[str, object], ...],
) -> str:
    matches = providers_for_platform(platform, providers)
    if any(provider.get("available") is True for provider in matches):
        return "已接入 · 可用"
    if matches:
        return "已接入 · 不可用"
    return "未接入 · 可选"


def llm_platform_display_status(
    platform: LLMPlatformGuide,
    snapshot: RouterSnapshot,
) -> str:
    if not snapshot.reachable:
        return "状态未知 · 可选"
    return llm_platform_status(platform, snapshot.providers)


def assistant_credential_setup_payload() -> dict[str, object]:
    """Return a secret-free setup snapshot suitable for either Agent."""
    snapshot = inspect_router(start_if_needed=False)
    power_sources = []
    for guide in POWER_CREDENTIAL_GUIDES:
        status, configured = power_credential_status(guide)
        power_sources.append(
            {
                "key": guide.key,
                "source": guide.source,
                "requirement": guide.requirement,
                "requirement_label": guide.requirement_label,
                "status": status,
                "configured": configured,
                "capability": guide.capability,
                "account_url": guide.account_url,
                "credential_url": guide.credential_url,
                "steps": list(guide.steps),
                "note": guide.note,
            }
        )

    configured_count, required_count = collection_setup_progress()
    missing_required = [
        item
        for item in power_sources
        if item["requirement"] == "collection_required"
        and item["configured"] is False
    ]
    llm_platforms = [
        {
            "key": guide.key,
            "platform": guide.platform,
            "status": llm_platform_display_status(guide, snapshot),
            "credential_label": guide.credential_label,
            "credential_url": guide.credential_url,
            "steps": list(guide.steps),
            "note": guide.note,
        }
        for guide in LLM_PLATFORM_GUIDES
    ]
    router_status = (
        f"已连接，可用免费渠道 {snapshot.available_provider_count} 个"
        if snapshot.reachable
        else snapshot.message
    )
    conclusion = (
        f"在线采集凭据已配置 {configured_count}/{required_count}："
        "GridStatus、ENTSO-E 和 Elecheck 在实时采集时需要配置；"
        "Elexon 与广州电力交易中心无需 API Key。"
        f"Agent 的本机免费模型网关状态：{router_status}。"
        "各 LLM 平台均为可选项，只需至少一个免费渠道可用。"
    )
    warnings = [
        "请从左侧导航打开“API 配置向导”完成保存和状态验证；不要把任何密钥发到 Agent 对话中。",
        (
            "数据源申请入口：GridStatus https://www.gridstatus.io/settings/api；"
            "ENTSO-E https://transparencyplatform.zendesk.com/hc/en-us/articles/"
            "12845911031188-How-to-get-security-token；Elecheck 没有公开自助 Key 页面，"
            "需向数据服务方或项目管理员申请。Elexon 与广州交易中心无需 Key。"
        ),
        (
            "LLM 平台均为可选：API 配置向导已整理 Gemini、ModelScope、NVIDIA、Groq、"
            "Cloudflare、智谱、GitHub Models、Mistral、OpenRouter 和 SiliconFlow 的申请入口；"
            "任选一个免费渠道配置并测试即可。"
        ),
        (
            "如果状态显示已配置但接口仍返回 401/403，通常是密钥过期、权限未开通或账号套餐"
            "不支持；请在配置向导本机替换后，先做一天或一个小数据集的验证请求。"
        ),
    ]
    if missing_required:
        warnings.append(
            "待配置的实时采集来源："
            + "、".join(str(item["source"]) for item in missing_required)
            + "。"
        )
        for item in missing_required:
            first_step = next(iter(item["steps"]), "请查看 API 配置向导。")
            url = item["credential_url"] or item["account_url"]
            url_text = f" 申请/说明：{url}" if url else ""
            warnings.append(f"{item['source']}：{first_step}{url_text}")
    if not snapshot.reachable:
        warnings.append(
            "本机免费模型网关当前不可用；请在 API 配置向导的 LLM / Agent 页检查网关配置和渠道状态。"
        )
    return {
        "assistant_conclusion": conclusion,
        "warnings": warnings,
        "power_sources": power_sources,
        "llm_platforms": llm_platforms,
        "collection_required": required_count,
        "collection_configured": configured_count,
        "router": {
            "configured": snapshot.configured,
            "reachable": snapshot.reachable,
            "available_free_providers": snapshot.available_provider_count,
            "message": snapshot.message,
        },
        "data_sources": ["本机 API 配置向导", "本机免费模型网关"],
        "secrets_included": False,
    }
