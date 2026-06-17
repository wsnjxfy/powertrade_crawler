import json
import os
from pathlib import Path

from powertrade_crawler.config import get_settings


ELECHECK_AUTHORIZATION_ENV = "ELECHECK_AUTHORIZATION"


def get_elecheck_authorization_cache_path() -> Path:
    return Path("data") / ".elecheck_authorization_cache.json"


def get_valid_cached_elecheck_authorization(
    cache_path: Path | None = None,
    now: object | None = None,
) -> str | None:
    _ = now
    path = cache_path or get_elecheck_authorization_cache_path()
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        authorization = payload.get("authorization")
    except (OSError, json.JSONDecodeError, TypeError):
        return None

    if not authorization:
        return None
    return str(authorization)


def cache_elecheck_authorization(
    authorization: str,
    cache_path: Path | None = None,
    now: object | None = None,
) -> Path:
    _ = now
    path = cache_path or get_elecheck_authorization_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"authorization": authorization},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def resolve_elecheck_authorization(
    authorization_override: str | None = None,
    cache_path: Path | None = None,
) -> str | None:
    settings = get_settings()
    authorization = (
        authorization_override
        or settings.elecheck_authorization
        or get_valid_cached_elecheck_authorization(cache_path)
    )
    if not authorization:
        return None
    return authorization.strip()


def set_elecheck_authorization_for_current_process(authorization: str) -> None:
    os.environ[ELECHECK_AUTHORIZATION_ENV] = authorization
    get_settings.cache_clear()
