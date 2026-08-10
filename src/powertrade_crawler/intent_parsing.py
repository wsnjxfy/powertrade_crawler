from __future__ import annotations

import re
from typing import Any


_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _chinese_number(value: str) -> int | None:
    if not value:
        return None
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        tens = _CHINESE_DIGITS.get(left, 1) if left else 1
        ones = _CHINESE_DIGITS.get(right, 0) if right else 0
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    if len(value) == 1:
        return _CHINESE_DIGITS.get(value)
    digits = [_CHINESE_DIGITS.get(char) for char in value]
    if any(item is None for item in digits):
        return None
    return int("".join(str(item) for item in digits))


def parse_clock_time(message: str, *, default_hour: int = 2) -> tuple[int, int]:
    """Parse common Chinese/24-hour clock phrases used in schedule requests."""
    arabic = re.search(
        r"(?:凌晨|早上|上午|中午|下午|晚上)?\s*(\d{1,2})\s*[点:：时]"
        r"\s*(?:(\d{1,2})\s*分?|半|一刻|三刻)?",
        message,
    )
    chinese = None
    if arabic is None:
        chinese = re.search(
            r"(?:凌晨|早上|上午|中午|下午|晚上)?\s*"
            r"([零〇一二两三四五六七八九十]{1,3})\s*[点时]"
            r"\s*(半|一刻|三刻|[零〇一二两三四五六七八九十]{1,3}\s*分?)?",
            message,
        )

    if arabic is not None:
        hour = int(arabic.group(1))
        suffix = arabic.group(0)
        if "半" in suffix:
            minute = 30
        elif "一刻" in suffix:
            minute = 15
        elif "三刻" in suffix:
            minute = 45
        else:
            minute = int(arabic.group(2) or 0)
    elif chinese is not None:
        hour = _chinese_number(chinese.group(1))
        raw_minute = (chinese.group(2) or "").strip().removesuffix("分")
        if hour is None:
            hour = default_hour
        if raw_minute == "半":
            minute = 30
        elif raw_minute == "一刻":
            minute = 15
        elif raw_minute == "三刻":
            minute = 45
        else:
            minute = _chinese_number(raw_minute) or 0
    else:
        return default_hour, 0

    if any(token in message for token in ("下午", "晚上")) and 1 <= hour <= 11:
        hour += 12
    elif "中午" in message and 1 <= hour <= 10:
        hour += 12
    return hour, minute


def contextualize_continuation(
    message: str,
    model_messages: list[dict[str, Any]],
) -> str:
    """Attach the previous user turn to short action continuations."""
    text = message.strip().lower()
    if not any(
        token in text
        for token in (
            "那就",
            "就按",
            "按刚才",
            "接着",
            "继续",
            "补上",
            "这个日期",
            "这个范围",
            "我说的",
        )
    ):
        return message
    user_messages = [
        str(item.get("content") or "").strip()
        for item in model_messages
        if item.get("role") == "user" and str(item.get("content") or "").strip()
    ]
    if len(user_messages) < 2:
        return message
    previous = user_messages[-2]
    if len(previous) > 500:
        return message
    return f"{previous}\n{message.strip()}"
