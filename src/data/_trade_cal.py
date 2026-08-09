"""Structural validation of an exchange trading-calendar frame.

Shared by the tushare fetcher (before persisting ``trade_cal.parquet``)
and the qlib bin builder (before deriving the anchor-specific expected
first session from it), so the two ends cannot drift apart — the same
lesson the coverage guards learned in codex #412 r4.

The frame's contract is ONE ROW PER CALENDAR DAY, open or closed. That
makes completeness an EQUATION, not an enumeration: the row count must
equal the day span exactly, so any interior gap — however shaped —
breaks it. codex #412 walked the enumeration one shape at a time
(empty in r7; leading truncation, missing columns, all-closed in r8; a
head+tail-only frame with the interior missing in r9), and each patch
invited the next shape. The equation closes the family.

Every row is parsed as a REAL calendar date first: an eight-digit
non-date such as ``19901340`` passes a lexical ``\\d{8}`` regex but
crashes downstream bounds arithmetic (r9 P2) — here it is a defect
report, never a crash and never a persisted file.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

REQUIRED_COLUMNS = ("cal_date", "is_open")


def calendar_frame_defect(df: Any) -> str | None:
    """Why this trade_cal frame is structurally unusable, or ``None``.

    Callers translate a non-``None`` defect into their own refusal
    (the fetcher records a hole and writes nothing; the builder raises
    instead of deriving) — the classification lives with them, the
    STRUCTURE lives here.
    """
    if df is None or len(df) == 0:
        return "empty trade_cal frame"
    missing = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing:
        return f"trade_cal frame lacks column(s) {sorted(missing)}"
    dates_str = df["cal_date"].astype(str)
    parsed = pd.to_datetime(dates_str, format="%Y%m%d", errors="coerce")
    if parsed.isna().any():
        bad = dates_str[parsed.isna()].iloc[0]
        return (f"trade_cal cal_date {bad!r} is not a real calendar "
                "date")
    if parsed.duplicated().any():
        dup = dates_str[parsed.duplicated()].iloc[0]
        return f"trade_cal frame has duplicate cal_date {dup!r}"
    # The completeness EQUATION: one row per calendar day means the
    # row count must equal the day span exactly. Any interior gap
    # breaks this, whatever its shape or position.
    span_days = (parsed.max() - parsed.min()).days + 1
    if len(df) != span_days:
        return (f"trade_cal frame has {len(df)} rows but spans "
                f"{span_days} calendar days "
                f"({parsed.min().date()}..{parsed.max().date()}) - "
                "interior days are missing")
    if not (df["is_open"] == 1).any():
        return "trade_cal frame has no open sessions at all"
    return None
