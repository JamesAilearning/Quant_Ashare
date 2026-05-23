"""Shared display formatting helpers with no Streamlit imports."""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any

UNAVAILABLE = "—"


def _finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def fmt_metric(val: Any, /) -> str:
    """Format a numeric metric for existing UI call sites."""

    return format_number(val, decimals=4, thousands=False)


def format_percent(
    value: Any,
    *,
    decimals: int = 2,
    signed: bool = True,
    arrow: bool = False,
    parens_negative: bool = False,
    missing: str = UNAVAILABLE,
) -> str:
    """Format a decimal return value as a percentage string."""

    parsed = _finite_float(value)
    if parsed is None:
        return missing
    percent = parsed * 100
    sign = ""
    if parens_negative and percent < 0:
        body = f"{abs(percent):.{decimals}f}%"
        rendered = f"({body})"
    else:
        if percent < 0:
            sign = "-"
        elif signed:
            sign = "+"
        rendered = f"{sign}{abs(percent):.{decimals}f}%"
    if arrow:
        rendered += " \u2197" if parsed >= 0 else " \u2198"
    return rendered


def format_number(
    value: Any,
    *,
    decimals: int = 2,
    thousands: bool = True,
    abbreviate: bool = False,
    signed: bool = False,
    missing: str = UNAVAILABLE,
) -> str:
    """Format a finite number for table or metric display."""

    parsed = _finite_float(value)
    if parsed is None:
        return missing
    prefix = "+" if signed and parsed >= 0 else ""
    if abbreviate:
        abs_value = abs(parsed)
        for suffix, scale in (("B", 1_000_000_000), ("M", 1_000_000), ("k", 1_000)):
            if abs_value >= scale:
                return f"{prefix}{parsed / scale:.{decimals}f}{suffix}"
    comma = "," if thousands else ""
    return f"{prefix}{parsed:{comma}.{decimals}f}"


def format_money(
    value: Any,
    currency: str = "CNY",
    *,
    decimals: int = 2,
    missing: str = UNAVAILABLE,
) -> str:
    """Format a money value with an ASCII currency prefix."""

    parsed = _finite_float(value)
    if parsed is None:
        return missing
    currency_prefix = currency.upper()
    sign = "-" if parsed < 0 else ""
    return f"{sign}{currency_prefix} {abs(parsed):,.{decimals}f}"


def format_duration(value: Any, *, missing: str = UNAVAILABLE) -> str:
    """Format elapsed seconds into a compact duration string."""

    seconds = _finite_float(value)
    if seconds is None or seconds < 0:
        return missing
    total = int(seconds)
    if seconds < 1:
        return "<1 秒"
    days, remainder = divmod(total, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, secs = divmod(remainder, 60)
    if days:
        return f"{days}天 {hours}小时"
    if hours:
        return f"{hours}小时 {minutes}分"
    if minutes:
        if total >= 600:
            return f"{minutes}分"
        return f"{minutes}分 {secs}秒"
    return f"{secs}秒"


def format_relative_time(
    value: Any,
    *,
    now: datetime | None = None,
    missing: str = UNAVAILABLE,
) -> str:
    """Format a datetime-like value relative to ``now``."""

    parsed = _parse_datetime(value)
    if parsed is None:
        return missing
    anchor = now or datetime.now(timezone.utc)
    parsed, anchor = _align_timezones(parsed, anchor)
    seconds = int((anchor - parsed).total_seconds())
    future = seconds < 0
    seconds = abs(seconds)
    if seconds < 60:
        return "刚刚" if not future else "<1 分钟后"
    minutes = seconds // 60
    if minutes < 60:
        return _relative_label(minutes, "分钟", future)
    hours = minutes // 60
    if hours < 24:
        return _relative_label(hours, "小时", future)
    days = hours // 24
    if days == 1 and not future:
        return "昨天"
    if days < 7:
        return _relative_label(days, "天", future)
    weeks = days // 7
    if days < 30:
        return _relative_label(weeks, "周", future)
    months = days // 30
    if days < 365:
        return _relative_label(months, "个月", future)
    years = days // 365
    return _relative_label(years, "年", future)


def format_date_absolute(
    value: Any,
    *,
    style: str = "date",
    missing: str = UNAVAILABLE,
) -> str:
    """Format a date-like value as date, datetime, or ISO text."""

    parsed = _parse_datetime(value)
    if parsed is None:
        return missing
    if style == "date":
        return parsed.strftime("%Y-%m-%d")
    if style == "datetime":
        return parsed.strftime("%Y-%m-%d %H:%M")
    if style == "iso":
        return parsed.isoformat()
    raise ValueError(f"Unknown date style: {style!r}")


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
    return None


def _align_timezones(left: datetime, right: datetime) -> tuple[datetime, datetime]:
    if left.tzinfo is None and right.tzinfo is not None:
        return left.replace(tzinfo=right.tzinfo), right
    if left.tzinfo is not None and right.tzinfo is None:
        return left, right.replace(tzinfo=left.tzinfo)
    return left, right


def _relative_label(value: int, unit: str, future: bool) -> str:
    if future:
        return f"{value} {unit}后"
    return f"{value} {unit}前"
