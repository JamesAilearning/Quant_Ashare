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
    # LEXICAL representation first (codex #412 r10): pandas' strptime
    # is lenient — "2019012" parses as 2019-01-02 and "199012" as
    # 1990-01-02 (verified empirically) — while every downstream
    # consumer compares these values as STRINGS, where "2019012" sorts
    # before "20190102" and corrupts min/anchor comparisons. Exactly
    # eight ASCII digits, then calendar validity. (The same lesson
    # bundle_integrity learned in r5 for its stamp dates; dropping the
    # lexical layer when this logic moved into the shared module was a
    # regression r10 caught.)
    #
    # [0-9], NOT \d (codex #412 r11): \d is Unicode-aware, so an
    # Arabic-Indic spelling like "٢٠١٥1008"
    # matches it AND strptime parses it to 2015-10-08 (verified
    # empirically) — while the string minimum downstream sorts it
    # after every ASCII date, deriving the wrong first session. The
    # class literally spells the ASCII contract the comment claims.
    if not dates_str.str.fullmatch(r"[0-9]{8}").all():
        bad = dates_str[~dates_str.str.fullmatch(r"[0-9]{8}")].iloc[0]
        return (f"trade_cal cal_date {bad!r} is not exactly eight "
                "ASCII digits (YYYYMMDD)")
    parsed = pd.to_datetime(dates_str, format="%Y%m%d", errors="coerce")
    if parsed.isna().any():
        bad = dates_str[parsed.isna()].iloc[0]
        return (f"trade_cal cal_date {bad!r} is not a real calendar "
                "date")
    if parsed.duplicated().any():
        dup = dates_str[parsed.duplicated()].iloc[0]
        return f"trade_cal frame has duplicate cal_date {dup!r}"
    # is_open is a NON-NULL binary marker (codex #412 r10): a damaged
    # value (2, NaN, "1") on the TRUE first session would read as
    # closed, deriving a later session as "expected" and letting a
    # bundle missing the real first session pass the exact-match
    # guard. isin() is strict about type and NaN-false, so string
    # spellings and nulls are defects too.
    if not df["is_open"].isin([0, 1]).all():
        bad_open = df["is_open"][~df["is_open"].isin([0, 1])].iloc[0]
        return (f"trade_cal is_open {bad_open!r} is not a binary 0/1 "
                "marker")
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
