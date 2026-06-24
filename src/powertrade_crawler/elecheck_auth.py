import json
from pathlib import Path

from powertrade_crawler.credentials import (
    get_credential,
    get_credentials_path,
    save_credential,
    set_process_credential,
)


def get_elecheck_authorization_cache_path() -> Path:
    return get_credentials_path()


def get_valid_cached_elecheck_authorization(
    cache_path: Path | None = None,
    now: object | None = None,
) -> str | None:
    _ = now
    path = cache_path or get_elecheck_authorization_cache_path()
    authorization = get_credential(
        "elecheck_authorization",
        path=path,
    )
    if authorization or not cache_path or not path.exists():
        return authorization

    try:
        legacy_payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    legacy_authorization = legacy_payload.get("authorization")
    return str(legacy_authorization).strip() if legacy_authorization else None


def cache_elecheck_authorization(
    authorization: str,
    cache_path: Path | None = None,
    now: object | None = None,
) -> Path:
    _ = now
    return save_credential(
        "elecheck_authorization",
        authorization,
        path=cache_path or get_elecheck_authorization_cache_path(),
    )


def resolve_elecheck_authorization(
    authorization_override: str | None = None,
    cache_path: Path | None = None,
) -> str | None:
    return get_credential(
        "elecheck_authorization",
        override=authorization_override,
        path=cache_path or get_elecheck_authorization_cache_path(),
    )


def set_elecheck_authorization_for_current_process(authorization: str) -> None:
    set_process_credential("elecheck_authorization", authorization)
