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


# Only force the NAV chart's y-axis to include the 1.0 baseline when the
# series stays close to it. Once the strategy compounds well above 1.0
# (e.g. a 3× run) or drops well below, anchoring on 1.0 squashes the
# curve into a thin band and the operator can't read the shape. Beyond
# this window we let the axis auto-fit the data and draw a 1.0 reference
# line separately (UI review P2-5).
_NAV_BASELINE_INCLUDE_HIGH = 1.5
_NAV_BASELINE_INCLUDE_LOW = 0.7


def nav_y_range(frame: Any) -> list[float] | None:
    """Return a display range for the NAV chart's y-axis.

    Includes the normalized 1.0 baseline only when the data stays within
    ``[_NAV_BASELINE_INCLUDE_LOW, _NAV_BASELINE_INCLUDE_HIGH]`` — outside
    that the curve would be flattened against the baseline, so we fit the
    data instead and rely on a separate ``add_hline(y=1.0)`` reference
    (UI review P2-5). Returns ``None`` when there's no numeric data.
    """

    if frame is None or getattr(frame, "empty", True):
        return None

    import pandas as pd

    data_values: list[float] = []
    for column in ("strategy_nav", "benchmark_nav"):
        if column not in frame:
            continue
        series = pd.to_numeric(frame[column], errors="coerce").dropna()
        data_values.extend(float(value) for value in series.tolist())
    if not data_values:
        return None

    data_low = min(data_values)
    data_high = max(data_values)

    # Include the 1.0 baseline only while the series hugs it; once the
    # NAV ranges far from 1.0, anchoring there flattens the curve.
    if data_high <= _NAV_BASELINE_INCLUDE_HIGH and data_low >= _NAV_BASELINE_INCLUDE_LOW:
        values = [*data_values, 1.0]
    else:
        values = data_values

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
    if not selected:
        return ""
    all_levels = set(LOG_LEVEL_OPTIONS)
    apply_level_filter = selected != all_levels

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
