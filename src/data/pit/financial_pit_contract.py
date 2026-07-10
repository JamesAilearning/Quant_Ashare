"""Financial-statement PIT contract layer (阶段8 Gate-2, PR-1).

Turns the versioned raw store (``src.data.tushare.financial_statements``) into
PIT-contract-keyed records. This is the data-layer contract — deterministic
metadata about WHEN each filing became knowable. It computes NO factor and
applies NO carry-forward / exclusion / exposure (those are the PR-2
``FinancialPITDataView``'s job).

Contract fields (spec ``v2-financial-pit-contract``)
----------------------------------------------------
* ``report_period`` — the quarter the record describes (``end_date``).
* ``announcement_date`` — ``f_ann_date``, falling back to ``ann_date`` with the
  fallback RECORDED in ``announcement_date_source``. If BOTH are absent the
  record is UNAVAILABLE — it is NEVER assigned an availability date derived from
  the report period.
* ``available_from_trade_date`` — the first trading day STRICTLY AFTER the
  announcement (post-close assumption), from the canonical bundle calendar. All
  PIT joins key on this; the report-period end is never an availability date.
* revision linkage — a revised (``update_flag=1``) record carries the content
  hash of the as-originally-reported (``update_flag=0``) record for the same
  ``(ts_code, report_period)``, so a consumer can serve original-first.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from src.data.trading_calendar import StaticTradingCalendar
from src.data.tushare.financial_statements import (
    COL_CONTENT_HASH,
    COL_FETCH_BATCH,
    LOGICAL_KEY,
)

# Contract output columns.
REPORT_PERIOD = "report_period"
ANNOUNCEMENT_DATE = "announcement_date"
ANNOUNCEMENT_SOURCE = "announcement_date_source"  # "f_ann_date" | "ann_date" | ""
AVAILABLE_FROM = "available_from_trade_date"
REVISION_OF = "revision_of_content_hash"          # set on update_flag=1 rows

_REQUIRED = ("ts_code", "end_date", "ann_date", "f_ann_date", "update_flag")


class FinancialPITContractError(RuntimeError):
    """Raised when the contract cannot be derived honestly (missing columns,
    malformed dates). Fail-loud — never a silently-defaulted availability date."""


def _parse_yyyymmdd(value: object) -> date | None:
    """Parse a tushare ``YYYYMMDD`` value (str or int) to a date; None on
    NA/blank/unparseable (the caller decides fail-loud vs unavailable)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    token = str(value).strip()
    if not token or token in {"None", "nan", "NaT", "<NA>"}:
        return None
    token = token.split(".")[0]  # tolerate "20220331.0" from float coercion
    if len(token) != 8 or not token.isdigit():
        return None
    try:
        return date(int(token[:4]), int(token[4:6]), int(token[6:8]))
    except ValueError:
        return None


def build_contract_frame(
    store: pd.DataFrame, calendar: StaticTradingCalendar,
) -> pd.DataFrame:
    """Augment a raw-store frame with the PIT contract fields.

    Input is rows as written by ``FinancialStatementIngestor`` (may span many
    instruments / batches). Output adds ``report_period``, ``announcement_date``,
    ``announcement_date_source``, ``available_from_trade_date`` and
    ``revision_of_content_hash``. Rows with no announcement date get NaT
    availability (UNAVAILABLE) — never a period-end fallback.
    """
    missing = [c for c in _REQUIRED if c not in store.columns]
    if missing:
        raise FinancialPITContractError(
            f"store frame missing required columns {missing}; "
            f"have {sorted(store.columns)}."
        )
    out = store.copy()
    report_period = out["end_date"].map(_parse_yyyymmdd)
    f_ann = out["f_ann_date"].map(_parse_yyyymmdd)
    ann = out["ann_date"].map(_parse_yyyymmdd)

    announcement: list[date | None] = []
    source: list[str] = []
    for fa, a in zip(f_ann, ann, strict=True):
        if fa is not None:
            announcement.append(fa)
            source.append("f_ann_date")
        elif a is not None:
            announcement.append(a)
            source.append("ann_date")
        else:
            # BOTH absent — unavailable. NEVER derive availability from the
            # report period (that would be a look-ahead to the quarter end).
            announcement.append(None)
            source.append("")

    available = [
        calendar.next_trading_day_after(d) if d is not None else None
        for d in announcement
    ]

    out[REPORT_PERIOD] = pd.Series(report_period, index=out.index, dtype="object")
    out[ANNOUNCEMENT_DATE] = pd.Series(announcement, index=out.index, dtype="object")
    out[ANNOUNCEMENT_SOURCE] = pd.Series(source, index=out.index, dtype="object")
    out[AVAILABLE_FROM] = pd.Series(available, index=out.index, dtype="object")
    out[REVISION_OF] = _revision_linkage(out)
    return out


def _revision_linkage(frame: pd.DataFrame) -> pd.Series:
    """For each ``update_flag=1`` row, the content hash of the as-originally-
    reported (``update_flag=0``) row for the same ``(ts_code, report_period)``
    (latest batch if several). NA for original rows / when no original exists."""
    link: dict[tuple[str, object], str] = {}
    if COL_CONTENT_HASH in frame.columns:
        originals = frame[frame["update_flag"].astype(str) == "0"]
        # latest batch wins if the original was re-fetched
        batch_col = COL_FETCH_BATCH if COL_FETCH_BATCH in frame.columns else None
        for (ts, rp), grp in originals.groupby(["ts_code", REPORT_PERIOD], dropna=False):
            if batch_col is not None:
                grp = grp.sort_values(batch_col)
            link[(str(ts), rp)] = str(grp.iloc[-1][COL_CONTENT_HASH])
    out: list[object] = []
    for _, row in frame.iterrows():
        if str(row["update_flag"]) == "1":
            out.append(link.get((str(row["ts_code"]), row[REPORT_PERIOD]), pd.NA))
        else:
            out.append(pd.NA)
    return pd.Series(out, index=frame.index, dtype="object")


def resolve_current_versions(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep the LATEST batch per logical key ``(ts_code, end_date, update_flag)``.

    The physical store is append-only; this is the read-time resolution the
    spec mandates — the newest fetch of a logical record wins, but every prior
    version stays in the store (a changed re-fetch is retained, not lost)."""
    if COL_FETCH_BATCH not in frame.columns:
        return frame
    keys = [c for c in LOGICAL_KEY if c in frame.columns]
    if len(keys) != len(LOGICAL_KEY):
        return frame
    ordered = frame.sort_values(COL_FETCH_BATCH)
    return ordered.drop_duplicates(subset=keys, keep="last").reset_index(drop=True)
