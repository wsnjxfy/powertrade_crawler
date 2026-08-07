from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


DEFAULT_ROUTER_ENDPOINT = "http://127.0.0.1:8317/v1"
DEFAULT_MODEL_STRATEGY = "smart-auto"
CLIENT_ENV_OVERRIDE = "LLM_ROUTER_CLIENT_ENV"


class LLMRouterError(RuntimeError):
    """Safe, user-facing error raised by the local free LLM router client."""


class LLMRouterConfigError(LLMRouterError, ValueError):
    pass


class LLMRouterUnavailableError(LLMRouterError):
    pass


@dataclass(frozen=True)
class FreeRouterConfig:
    provider: str
    api_base: str
    api_url: str
    model: str
    api_key: str = field(repr=False, compare=False)
    source_path: Path

    def safe_summary(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "api_base": self.api_base,
            "api_url": self.api_url,
            "default_model": self.model,
            "source_path": str(self.source_path),
            "api_key": "configured",
        }


def default_client_env_path() -> Path:
    override = os.getenv(CLIENT_ENV_OVERRIDE, "").strip()
    if override:
        return Path(override).expanduser()
    from powertrade_crawler.config import get_settings

    configured_path = (get_settings().llm_router_client_env or "").strip()
    if configured_path:
        return Path(configured_path).expanduser()
    return Path.home() / ".config" / "llm-router" / "client-free.env"


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise LLMRouterConfigError(
            f"免费 LLM 池配置不存在：{path}。请确认本机已安装 llm-router。"
        )
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise LLMRouterConfigError(f"无法读取免费 LLM 池配置：{path}。") from exc
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, raw_value = line.partition("=")
        if not separator or not name.strip():
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[name.strip()] = value
    return values


def _validate_loopback_url(value: str, *, label: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise LLMRouterConfigError(
            f"{label} 必须是本机 HTTP 回环地址，拒绝把免费池密钥发送到外部主机。"
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LLMRouterConfigError(f"{label} 格式无效。")
    return normalized


def validate_router_endpoint(value: str) -> str:
    return _validate_loopback_url(value, label="LLM router endpoint")


def validate_model_strategy(model: str) -> str:
    normalized = model.strip()
    if normalized == DEFAULT_MODEL_STRATEGY:
        return normalized
    if not normalized.startswith("provider/"):
        raise LLMRouterConfigError(
            "模型策略只能是 smart-auto 或 provider/<免费渠道ID>。"
        )
    provider_id = normalized.removeprefix("provider/")
    if not provider_id or any(character.isspace() for character in provider_id):
        raise LLMRouterConfigError("provider/<免费渠道ID> 格式无效。")
    return normalized


def load_free_router_config(path: Path | None = None) -> FreeRouterConfig:
    source_path = path or default_client_env_path()
    values = _parse_env_file(source_path)
    required = {
        "LLM_PROVIDER",
        "LLM_API_BASE",
        "LLM_API_URL",
        "LLM_MODEL",
        "LLM_API_KEY",
    }
    missing = sorted(name for name in required if not values.get(name, "").strip())
    if missing:
        raise LLMRouterConfigError(
            "免费 LLM 池配置缺少字段：" + ", ".join(missing)
        )
    provider = values["LLM_PROVIDER"].strip()
    if provider != "openai-compatible":
        raise LLMRouterConfigError("LLM_PROVIDER 必须是 openai-compatible。")
    api_base = _validate_loopback_url(values["LLM_API_BASE"], label="LLM_API_BASE")
    api_url = _validate_loopback_url(values["LLM_API_URL"], label="LLM_API_URL")
    expected_url = f"{api_base}/chat/completions"
    if api_url != expected_url:
        raise LLMRouterConfigError(
            "LLM_API_URL 必须等于 LLM_API_BASE 下的 /chat/completions。"
        )
    return FreeRouterConfig(
        provider=provider,
        api_base=api_base,
        api_url=api_url,
        model=validate_model_strategy(values["LLM_MODEL"]),
        api_key=values["LLM_API_KEY"].strip(),
        source_path=source_path,
    )


class FreeRouterManagementClient:
    """Authenticated management client that never returns or logs router secrets."""

    def __init__(
        self,
        config: FreeRouterConfig,
        *,
        timeout_seconds: float = 10,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {config.api_key}"},
        )

    @classmethod
    def from_external_config(
        cls,
        *,
        start_if_needed: bool = True,
    ) -> FreeRouterManagementClient:
        config = load_free_router_config()
        client = cls(config)
        try:
            client.ensure_running(start_if_needed=start_if_needed)
        except Exception:
            client.close()
            raise
        return client

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> FreeRouterManagementClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def is_healthy(self) -> bool:
        health_url = self.config.api_base.removesuffix("/v1") + "/health"
        try:
            response = self.client.get(health_url)
            if response.is_error:
                return False
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return False
        return isinstance(payload, dict) and payload.get("ok") is True

    def ensure_running(self, *, start_if_needed: bool = True) -> None:
        if self.is_healthy():
            return
        if not start_if_needed:
            raise LLMRouterUnavailableError(
                "本地免费 LLM 路由器未运行，未调用任何外部或付费模型。"
            )
        start_script = self.config.source_path.parent / "start.ps1"
        if os.name != "nt" or not start_script.is_file():
            raise LLMRouterUnavailableError(
                "本地免费 LLM 路由器未运行，且当前环境无法执行 start.ps1；"
                "127.0.0.1 仅能访问同一台电脑。"
            )
        powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if not powershell:
            raise LLMRouterUnavailableError("未找到 PowerShell，无法启动免费 LLM 路由器。")
        try:
            completed = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(start_script),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise LLMRouterUnavailableError("启动免费 LLM 路由器失败。") from exc
        if completed.returncode != 0:
            raise LLMRouterUnavailableError(
                "免费 LLM 路由器启动脚本执行失败，请检查本地路由器日志。"
            )
        for _attempt in range(20):
            if self.is_healthy():
                return
            time.sleep(0.25)
        raise LLMRouterUnavailableError(
            "免费 LLM 路由器启动后仍不可用，请检查本地路由器日志。"
        )

    def _get_data(self, path: str) -> list[dict[str, Any]]:
        try:
            response = self.client.get(f"{self.config.api_base}{path}")
        except httpx.TimeoutException as exc:
            raise LLMRouterUnavailableError("免费 LLM 路由器请求超时。") from exc
        except httpx.HTTPError as exc:
            raise LLMRouterUnavailableError("无法连接本地免费 LLM 路由器。") from exc
        if response.status_code in {401, 403}:
            raise LLMRouterConfigError("本地免费池密钥被路由器拒绝。")
        if response.is_error:
            raise LLMRouterUnavailableError(
                f"免费 LLM 路由器管理接口返回 HTTP {response.status_code}。"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise LLMRouterUnavailableError("免费 LLM 路由器返回了无效 JSON。") from exc
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise LLMRouterUnavailableError("免费 LLM 路由器返回结构不符合预期。")
        return [row for row in rows if isinstance(row, dict)]

    def list_providers(self) -> list[dict[str, Any]]:
        allowed_fields = (
            "id",
            "name",
            "tier",
            "available",
            "directModel",
            "status",
            "expiresAt",
            "priority",
            "disabledReason",
        )
        return [
            {key: row.get(key) for key in allowed_fields if key in row}
            for row in self._get_data("/providers")
        ]

    def list_alerts(self) -> list[dict[str, Any]]:
        allowed_fields = (
            "id",
            "severity",
            "providerId",
            "code",
            "message",
            "expiresAt",
            "createdAt",
        )
        return [
            {key: row.get(key) for key in allowed_fields if key in row}
            for row in self._get_data("/alerts")
        ]

    def ensure_strategy_available(self, strategy: str) -> dict[str, Any]:
        normalized = validate_model_strategy(strategy)
        providers = self.list_providers()
        available_free = [
            row
            for row in providers
            if row.get("tier") == "free" and row.get("available") is True
        ]
        if normalized == DEFAULT_MODEL_STRATEGY:
            if not available_free:
                raise LLMRouterUnavailableError("当前没有可用的免费模型渠道。")
            return {
                "strategy": normalized,
                "mode": "automatic",
                "available_free_providers": len(available_free),
            }
        match = next(
            (
                row
                for row in providers
                if row.get("directModel") == normalized
                or f"provider/{row.get('id')}" == normalized
            ),
            None,
        )
        if match is None:
            raise LLMRouterConfigError(f"未知的免费渠道策略：{normalized}。")
        if match.get("tier") != "free":
            raise LLMRouterConfigError("只允许选择 tier=free 的渠道。")
        if match.get("available") is not True:
            raise LLMRouterUnavailableError("所选免费渠道当前不可用。")
        return {"strategy": normalized, "mode": "fixed_provider", "provider": match}

    def describe_strategy(self, strategy: str) -> dict[str, Any]:
        result = self.ensure_strategy_available(strategy)
        result["router_default"] = self.config.model
        result["api_base"] = self.config.api_base
        return result
