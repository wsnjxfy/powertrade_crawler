from __future__ import annotations

import hashlib
import json
import re
from typing import Any


_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;\"']+"),
    re.compile(r"(?i)(api[_ -]?key\s*[:=]\s*)[^\s,;\"']+"),
    re.compile(r"(?i)(securityToken=)[^&\s]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
)


def redact_text(value: str) -> str:
    result = value
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(r"\1***", result)
    return result


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).endswith("_tokens") and isinstance(item, (int, float)):
                sanitized[str(key)] = item
            elif re.search(
                r"(?i)(authorization|api.?key|token|secret|password)",
                str(key),
            ):
                sanitized[str(key)] = "***"
            else:
                sanitized[str(key)] = redact_value(item)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def arguments_hash(arguments: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(arguments).encode("utf-8")).hexdigest()
