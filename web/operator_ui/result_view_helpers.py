"""Pure helpers for operator UI result-detail display interactions."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

TIME_RANGE_OPTIONS = ("ALL", "1Y", "6M", "3M", "1M")
LOG_LEVEL_OPTIONS = ("ERROR", "WARNING", "INFO", "DEBUG")
_RANGE_MONTHS = {"1M": 1, "3M": 3, "6M": 6, "1Y": 12}


def filter_nav_frame_by_range(frame: Any, range_label: str) -> Any:
    """Return displayed NAV rows for ``range_label`` without mutating input."""

    if frame is None or getattr(frame, "empty", True):
        return frame

    import pandas as pd

    if "date" not in frame:
        return frame.copy()

    working = frame.copy()
    working["_qv2_date"] = pd.to_datetime(working["date"], errors="coerce")
    working = working.dropna(subset=["_qv2_date"])
    if working.empty:
        return working.drop(columns=["_qv2_date"])

    label = str(range_label or "ALL").upper()
    if label != "ALL":
        months = _RANGE_MONTHS.get(label)
        if months is not None:
            end = working["_qv2_date"].max()
            start = end - pd.DateOffset(months=months)
            working = working[working["_qv2_date"] >= start]

    working = working.sort_values("_qv2_date", kind="stable")
    working["date"] = working["_qv2_date"].dt.strftime("%Y-%m-%d")
    return working.drop(columns=["_qv2_date"])


def nav_y_range(frame: Any) -> list[float] | None:
    """Return a display range that always includes the normalized 1.0 line."""

    if frame is None or getattr(frame, "empty", True):
        return None

    import pandas as pd

    values: list[float] = [1.0]
    for column in ("strategy_nav", "benchmark_nav"):
        if column not in frame:
            continue
        series = pd.to_numeric(frame[column], errors="coerce").dropna()
        values.extend(float(value) for value in series.tolist())
    if not values:
        return None
    low = min(values)
    high = max(values)
    if low == high:
        pad = max(abs(low) * 0.02, 0.01)
    else:
        pad = (high - low) * 0.05
    return [low - pad, high + pad]


def filter_log_text(
    text: str,
    *,
    search: str = "",
    levels: Iterable[str] = LOG_LEVEL_OPTIONS,
) -> str:
    """Filter log text by search term and selected severity names."""

    search_term = str(search or "").strip().lower()
    selected = {str(level).upper() for level in levels if str(level).strip()}
    all_levels = set(LOG_LEVEL_OPTIONS)
    apply_level_filter = bool(selected) and selected != all_levels

    kept: list[str] = []
    for line in str(text or "").splitlines():
        lowered = line.lower()
        uppered = line.upper()
        if search_term and search_term not in lowered:
            continue
        if apply_level_filter and not any(level in uppered for level in selected):
            continue
        kept.append(line)
    return "\n".join(kept)
