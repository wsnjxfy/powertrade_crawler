import json
import sys
from pathlib import Path
from typing import Literal


CredentialName = Literal[
    "gridstatus_api_key",
    "elecheck_authorization",
    "entsoe_security_token",
    "elexon_api_key",
    "siliconflow_api_key",
]

CREDENTIAL_NAMES: tuple[CredentialName, ...] = (
    "gridstatus_api_key",
    "elecheck_authorization",
    "entsoe_security_token",
    "elexon_api_key",
    "siliconflow_api_key",
)
_PROCESS_CREDENTIALS: dict[CredentialName, str] = {}
_FILE_KEY_ALIASES: dict[CredentialName, tuple[str, ...]] = {
    "siliconflow_api_key": ("siliconflow_api_key", "SILICONFLOW_API_KEY"),
}


def get_project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def get_credentials_directory() -> Path:
    return get_project_root() / ".auth"


def get_credentials_path() -> Path:
    return get_credentials_directory() / "credentials.json"


def read_credentials(path: Path | None = None) -> dict[str, str]:
    credential_path = path or get_credentials_path()
    if not credential_path.exists():
        return {}
    try:
        payload = json.loads(credential_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    credentials: dict[str, str] = {}
    for name in CREDENTIAL_NAMES:
        candidate_keys = _FILE_KEY_ALIASES.get(name, (name,))
        value = next((payload.get(key) for key in candidate_keys if payload.get(key)), None)
        if value:
            credentials[name] = str(value).strip()
    return credentials


def get_credential(
    name: CredentialName,
    *,
    override: str | None = None,
    path: Path | None = None,
) -> str | None:
    value = override or _PROCESS_CREDENTIALS.get(name) or read_credentials(path).get(name)
    return value.strip() if value else None


def save_credential(
    name: CredentialName,
    value: str,
    *,
    path: Path | None = None,
) -> Path:
    credential_path = path or get_credentials_path()
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} cannot be empty.")

    credentials = read_credentials(credential_path)
    credentials[name] = normalized
    credential_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        credential_path.parent.chmod(0o700)
    except OSError:
        pass
    credential_path.write_text(
        json.dumps(
            {key: credentials.get(key, "") for key in CREDENTIAL_NAMES},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        credential_path.chmod(0o600)
    except OSError:
        pass
    return credential_path


def set_process_credential(name: CredentialName, value: str) -> None:
    normalized = value.strip()
    if normalized:
        _PROCESS_CREDENTIALS[name] = normalized


def clear_process_credentials() -> None:
    _PROCESS_CREDENTIALS.clear()
