"""The embedded ``snapshot_date`` contract of the active-stocks snapshot (P3-5).

``TushareFetcher._fetch_stock_basic`` stamps every row of
``active_stocks.parquet`` / ``delisted_stocks.parquet`` with a ``snapshot_date``
column (``YYYYMMDD``, one value per file) at fetch time. Downstream staleness
guards previously only had the file mtime — a WEAK proxy: any sync / copy tool
that rewrites mtime makes a stale snapshot look fresh and lets a guard pass
silently. The embedded column survives copies and pandas round-trips, so guards
read THIS instead.

This module is the single reader of that contract: pure (frame in, date out),
no IO — callers load the parquet themselves and decide their own error type.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

SNAPSHOT_DATE_COLUMN = "snapshot_date"


class SnapshotDateError(ValueError):
    """The frame's embedded snapshot_date is missing or malformed. For a file
    written before P3-5 (no column), the fix is to re-fetch ``stock_basic`` so
    the snapshot is regenerated with the stamp — callers fail loud rather than
    silently falling back to mtime."""


def embedded_snapshot_date(df: pd.DataFrame, *, source: str = "snapshot") -> date:
    """The single embedded snapshot date of an active/delisted-stocks frame.

    Fail-loud on every malformed shape rather than guessing: a MISSING column
    (pre-P3-5 file), an empty frame, a null value, MULTIPLE distinct values
    (a corrupt or hand-concatenated file — there is no honest single answer),
    or a non-``YYYYMMDD`` value all raise :class:`SnapshotDateError`.
    """
    if SNAPSHOT_DATE_COLUMN not in df.columns:
        raise SnapshotDateError(
            f"{source} has no embedded '{SNAPSHOT_DATE_COLUMN}' column — written "
            "before the snapshot-date stamp existed (P3-5). Re-fetch stock_basic "
            "to regenerate it; refusing to guess from file mtime."
        )
    column = df[SNAPSHOT_DATE_COLUMN]
    # EVERY row must carry the stamp: a partially-null column is a hand-merged
    # old+new file — dropping the nulls and returning the surviving date would
    # bless exactly the corrupt shape this contract exists to refuse (codex P2).
    null_count = int(column.isna().sum())
    if null_count > 0 and null_count < len(column):
        raise SnapshotDateError(
            f"{source} '{SNAPSHOT_DATE_COLUMN}' is null on {null_count} of "
            f"{len(column)} row(s); every row must carry the stamp. The file "
            "looks hand-merged from old and new snapshots — re-fetch stock_basic."
        )
    values = column.dropna().unique()
    if len(values) == 0:
        raise SnapshotDateError(
            f"{source} '{SNAPSHOT_DATE_COLUMN}' column carries no value "
            "(empty frame or all-null); cannot establish the snapshot date."
        )
    if len(values) > 1:
        raise SnapshotDateError(
            f"{source} '{SNAPSHOT_DATE_COLUMN}' carries {len(values)} distinct "
            f"values ({sorted(map(str, values))[:4]}…); a snapshot has exactly "
            "one. Refusing to pick — the file looks corrupt or hand-merged."
        )
    raw = str(values[0])
    # Exact shape BEFORE strptime: %Y%m%d is lenient (a 7-digit '2026061'
    # parses as 2026-06-01) and this date drives the staleness + consistency
    # guards — a malformed stamp must fail loud, not be reinterpreted (codex P2).
    if len(raw) != 8 or not raw.isdigit():
        raise SnapshotDateError(
            f"{source} '{SNAPSHOT_DATE_COLUMN}' value {raw!r} is not YYYYMMDD "
            "(exactly 8 digits)."
        )
    try:
        return datetime.strptime(raw, "%Y%m%d").date()
    except ValueError as exc:
        raise SnapshotDateError(
            f"{source} '{SNAPSHOT_DATE_COLUMN}' value {raw!r} is not YYYYMMDD."
        ) from exc


__all__ = ["SNAPSHOT_DATE_COLUMN", "SnapshotDateError", "embedded_snapshot_date"]
