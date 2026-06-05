"""PIT historical ST status from the tushare ``namechange`` table.

Reconstructs "was instrument X ST on historical date D" as an **as-of step
function on ``start_date``**: the name in effect on D is the one from the row
with the greatest ``start_date <= D``. This deliberately IGNORES ``end_date``
(in the real ``all_namechanges`` data ``end_date`` is 51% null and the filled
values overlap heavily across consecutive rows — unreliable as an interval
bound) and uses only ``start_date`` (0% null). The ST predicate itself is the
one shared with the inference path, :func:`src.data.st_status.is_st_name`.

PIT no-look-ahead: only rows with ``start_date <= D`` are consulted; a future
rename (``start_date > D``) is never used, so the status reflects the name in
effect on D, not a later relabel. ``ann_date <= start_date`` always, so
``start_date <= D`` already implies the change was public by D.

Why name-only (no ``change_reason`` rescue): in the real data ``change_reason``
disagrees with the name in ~795 rows (788 ST-name/non-became-ST-reason, e.g.
摘帽; 7 became-ST-reason/non-ST-name, one with no marker at all), so using it
would trade a tiny false-negative for a worse false-positive. The name-only
blind spot (a truncated name like ``*金亚``) touches csi300 only via 乐视退,
which is delisted and handled by the delisting layer — see the PR2 Step-0
design doc.

Boundaries: pure (pandas in, plain-Python lookup out) — no qlib, no backtest
imports — so the dedup / boundary / ambiguity / default logic is unit-testable
in isolation.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from src.data.pit._common import qlib_to_ts_code, to_iso_date
from src.data.st_status import is_st_name

_REQUIRED_COLUMNS = ("ts_code", "name", "start_date")

# ts_code -> list of (start_date_iso, is_st, representative_name), sorted by
# start_date ascending. One record per (ts_code, start_date).
StLookup = dict[str, list[tuple[str, bool, str]]]


class StHistoryError(RuntimeError):
    """Raised when the namechange source cannot yield a usable ST history."""


def load_namechange(path: str | Path) -> pd.DataFrame:
    """Read + fail-loud-validate the namechange parquet.

    Raises :class:`StHistoryError` if the file is absent, unreadable, missing a
    required column, or empty — never returns a frame that would silently
    yield an empty (no-op) ST mask.
    """
    p = Path(path)
    if not p.exists():
        raise StHistoryError(
            f"namechange source not found: {p}. The backtest ST mask requires "
            "all_namechanges.parquet; refusing to run an un-masked backtest."
        )
    try:
        df = pd.read_parquet(p)
    except Exception as exc:  # noqa: BLE001 — re-raised as a domain error
        raise StHistoryError(
            f"namechange source {p} could not be read as parquet "
            f"({type(exc).__name__}: {exc})."
        ) from exc
    missing = [c for c in _REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise StHistoryError(
            f"namechange source {p} is missing required column(s) {missing} "
            f"(has {list(df.columns)})."
        )
    if df.empty:
        raise StHistoryError(f"namechange source {p} has zero rows.")
    return df


def assert_covers(namechange: pd.DataFrame, eval_end_iso: str) -> None:
    """Fail-loud if the snapshot is too stale to cover the backtest window.

    namechange has no snapshot-date column, so the latest ``ann_date`` (falling
    back to ``start_date``) is the recency proxy. If the latest recorded change
    predates the backtest end, ST designations after it are unrepresented and
    the backtest would silently under-mask — raise instead.
    """
    dates: list[str] = []
    for col in ("ann_date", "start_date"):
        if col in namechange.columns:
            dates += [
                str(v) for v in namechange[col].dropna().tolist() if str(v).isdigit()
            ]
    if not dates:
        raise StHistoryError(
            "namechange has no usable ann_date/start_date values to verify "
            "coverage."
        )
    latest_iso = to_iso_date(max(dates))
    if latest_iso < eval_end_iso:
        raise StHistoryError(
            f"namechange snapshot ends {latest_iso}, before the backtest end "
            f"{eval_end_iso}; ST designations after {latest_iso} would be "
            "un-masked. Refresh all_namechanges.parquet before backtesting "
            "this window."
        )


def build_st_lookup(namechange: pd.DataFrame) -> StLookup:
    """Build a per-ts_code, start_date-sorted ST step function.

    Order matters:

    * **full-row dedup** via ``drop_duplicates()`` — the real table is ~40%
      exact-duplicate rows. This is NOT a key-subset dedup: two rows sharing
      ``(ts_code, start_date)`` but differing in ``name`` are DISTINCT and both
      kept (collapsing them by key would drop a real ST/non-ST distinction).
    * group by ``(ts_code, start_date)``; a period is ST if **any** name at
      that start_date is ST (conservative — for a buy-list exclusion, erring
      toward "ST" is the safe direction).
    * sort each ts_code's records by ``start_date`` ascending.
    """
    missing = [c for c in _REQUIRED_COLUMNS if c not in namechange.columns]
    if missing:
        raise StHistoryError(
            f"namechange is missing required column(s) {missing}; "
            f"has {list(namechange.columns)}."
        )
    df = namechange.drop_duplicates()  # FULL-ROW dedup (never by key subset)
    # (ts_code, start_iso) -> (is_st, representative_name)
    grouped: dict[tuple[str, str], tuple[bool, str]] = {}
    for ts_code, name, start in zip(
        df["ts_code"].astype(str),
        df["name"].astype(str),
        df["start_date"].astype(str),
        strict=True,
    ):
        start_iso = to_iso_date(start)
        st = is_st_name(name)
        key = (ts_code, start_iso)
        prev = grouped.get(key)
        if prev is None:
            grouped[key] = (st, name)
        else:
            # any-ST rule; prefer an ST name as the representative for audit.
            merged_st = prev[0] or st
            repr_name = name if (st and not prev[0]) else prev[1]
            grouped[key] = (merged_st, repr_name)
    lookup: StLookup = {}
    for (ts_code, start_iso), (st, name) in grouped.items():
        lookup.setdefault(ts_code, []).append((start_iso, st, name))
    for records in lookup.values():
        records.sort(key=lambda r: r[0])
    return lookup


def _asof_record(
    lookup: StLookup, ts_code: str, date_iso: str,
) -> tuple[bool, str] | None:
    """The (is_st, name) record in effect on ``date_iso`` (greatest
    ``start_date <= date_iso``), or ``None`` if no record starts on/before it.
    """
    records = lookup.get(ts_code)
    if not records:
        return None
    chosen: tuple[bool, str] | None = None
    for start_iso, st, name in records:
        if start_iso <= date_iso:
            chosen = (st, name)
        else:
            break  # records are sorted; no later start can be <= date_iso
    return chosen


def name_on(lookup: StLookup, ts_code: str, date_iso: str) -> str | None:
    """The name in effect for ``ts_code`` on ``date_iso``, or ``None`` if the
    instrument has no namechange record starting on/before that date."""
    rec = _asof_record(lookup, ts_code, date_iso)
    return None if rec is None else rec[1]


def is_st_on(lookup: StLookup, ts_code: str, date_iso: str) -> bool:
    """Was ``ts_code`` ST on ``date_iso`` (PIT as-of ``start_date``)?

    **Defaults to ``False``** when the instrument has no namechange record
    starting on/before ``date_iso`` — before its first recorded change, and for
    an instrument absent from the table entirely, it is treated as NOT ST. This
    is correct because a stock's pre-first-record name is its original/IPO name
    and a stock is never ST at IPO (ST is a post-listing designation). The
    residual blind spot is per-stock history INcompleteness (an earliest record
    that is already ``*ST`` could hide an earlier plain-``ST`` period); low
    incidence on csi300 — see the PR2 proposal "PIT coverage limitation". A
    curated manual-override file is the escape hatch if a specific gap is found.
    """
    rec = _asof_record(lookup, ts_code, date_iso)
    return False if rec is None else rec[0]


def compute_st_mask(
    pairs: Iterable[tuple[str, str]],
    lookup: StLookup,
) -> tuple[frozenset[tuple[str, str]], list[dict[str, str]]]:
    """Map ``(date_iso, qlib_instrument)`` pairs to the ST drop-set + audit.

    Returns ``(masked, attribution)`` where ``masked`` is the
    ``frozenset[(date_iso, instrument)]`` consumable by
    ``apply_mask_to_predictions`` and ``attribution`` is one record per masked
    pair (``date``, ``instrument``, ``ts_code``, ``name``) for the operator's
    RUN_E2E review. Pure — the caller persists the audit.
    """
    masked: set[tuple[str, str]] = set()
    attribution: list[dict[str, str]] = []
    for date_iso, inst in pairs:
        inst_s = str(inst)
        ts_code = qlib_to_ts_code(inst_s)
        rec = _asof_record(lookup, ts_code, date_iso)
        if rec is not None and rec[0]:
            masked.add((date_iso, inst_s))
            attribution.append({
                "date": date_iso,
                "instrument": inst_s,
                "ts_code": ts_code,
                "name": rec[1],
            })
    attribution.sort(key=lambda r: (r["date"], r["instrument"]))
    return frozenset(masked), attribution


__all__ = [
    "StHistoryError",
    "StLookup",
    "assert_covers",
    "build_st_lookup",
    "compute_st_mask",
    "is_st_on",
    "load_namechange",
    "name_on",
]
