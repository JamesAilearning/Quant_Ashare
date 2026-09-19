"""Explicit per-security name history; pure validation and bounded collection.

This is not a fallback for the announcement-range query. The caller chooses the
mode, verifies snapshot provenance, and owns the only atomic publication.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pandas as pd

from src.data.tushare import aggregate_response as aggregate

# Stock_basic field list for both 'L' and 'D' buckets. ts_code, list_date,
# delist_date are the load-bearing fields for Phase A.2; the rest are
# kept for diagnostics. Shared by the producer and prerequisite validation.
STOCK_BASIC_FIELDS = (
    "ts_code,symbol,name,area,industry,market,list_date,delist_date,"
    "list_status,curr_type"
)

MAX_NAMECHANGE_SECURITIES = 10_000
# Official stock_basic response cap; saturation cannot attest a full snapshot.
STOCK_BASIC_ROW_GUARD = 6_000
_CODE = re.compile(r"[0-9]{6}\.(SH|SZ|BJ)")
# Exact observed identities, not prefix grammars or aliases for numeric codes.
# The name-only exception must never authorize membership in a stock snapshot.
# See fetch-namechange-per-security design decisions 8 and 10.
HISTORICAL_DELISTED_CODES = frozenset({"T600018.SH"})
HISTORICAL_NAMECHANGE_CODES = HISTORICAL_DELISTED_CODES | frozenset({"X19363.SH"})


def _require_code(
    value: object, label: str, *, historical_codes: frozenset[str] = frozenset(),
) -> str:
    if not isinstance(value, str) or (
        _CODE.fullmatch(value) is None
        and value not in historical_codes
    ):
        raise aggregate.AggregateResponseError(f"{label}: invalid security code")
    return value


def validate_stock_basic_snapshot(
    frame: pd.DataFrame, *, status: str, snapshot_date: str | None,
) -> set[str]:
    """Validate one bucket; None denotes raw input before our date stamp."""
    label = f"stock_basic {status}"
    aggregate._frame_bytes(frame, label)
    required = set(STOCK_BASIC_FIELDS.split(","))
    if snapshot_date is not None:
        aggregate._real_date(snapshot_date, "stock_basic snapshot")
        required.add("snapshot_date")
    if not frame.columns.is_unique or not required.issubset(frame.columns):
        raise aggregate.AggregateResponseError(f"{label}: missing or duplicate snapshot fields")
    if frame.empty or len(frame) >= STOCK_BASIC_ROW_GUARD:
        raise aggregate.AggregateResponseError(f"{label}: empty or saturated snapshot")
    codes = [
        _require_code(value, label, historical_codes=HISTORICAL_DELISTED_CODES if status == "D" else frozenset())
        for value in frame["ts_code"]
    ]
    if len(codes) != len(set(codes)):
        raise aggregate.AggregateResponseError(f"{label}: duplicate security codes")
    expected_columns = {"list_status": status}
    if snapshot_date is not None:
        expected_columns["snapshot_date"] = snapshot_date
    for column, expected in expected_columns.items():
        if any(not isinstance(value, str) or value != expected for value in frame[column]):
            raise aggregate.AggregateResponseError(f"{label}: invalid or stale {column}")
    return set(codes)


def stock_basic_security_codes(
    active: pd.DataFrame, delisted: pd.DataFrame, *, snapshot_date: str,
) -> set[str]:
    """Validate the complete pair without depending on retained name history."""
    buckets: list[set[str]] = []
    for frame, status in ((active, "L"), (delisted, "D")):
        buckets.append(validate_stock_basic_snapshot(frame, status=status, snapshot_date=snapshot_date))
    if buckets[0] & buckets[1]:
        raise aggregate.AggregateResponseError("stock_basic: overlapping L/D codes")
    return buckets[0] | buckets[1]


def namechange_security_universe(
    active: pd.DataFrame, delisted: pd.DataFrame, retained: pd.DataFrame,
    *, snapshot_date: str,
) -> tuple[str, ...]:
    """Freeze validated L/D/retained codes; never infer a missing snapshot."""
    codes = stock_basic_security_codes(active, delisted, snapshot_date=snapshot_date)
    aggregate.require_retained_keys(retained, retained, "namechange", label="namechange retained")
    all_codes = codes | {
        _require_code(value, "namechange retained", historical_codes=HISTORICAL_NAMECHANGE_CODES)
        for value in retained["ts_code"]
    }
    if len(all_codes) > MAX_NAMECHANGE_SECURITIES:
        raise aggregate.AggregateResponseError("namechange: security/call budget exceeded")
    return tuple(sorted(all_codes))


def collect_namechange_history(
    securities: tuple[str, ...], *, call: Callable[..., pd.DataFrame],
    progress: Callable[[int, int, int], None] | None = None,
) -> pd.DataFrame:
    """Query every frozen code without dates; return a candidate, never persist.

    Empty responses keep their schema. Raw source history outside the requested
    operational envelope remains intact, including null announcement dates.
    """
    if not securities or len(securities) > MAX_NAMECHANGE_SECURITIES:
        raise aggregate.AggregateResponseError("namechange: empty set or security/call budget exceeded")
    validated = [
        _require_code(code, "namechange request", historical_codes=HISTORICAL_NAMECHANGE_CODES)
        for code in securities
    ]
    if len(set(validated)) != len(validated):
        raise aggregate.AggregateResponseError("namechange: duplicate requested security")
    fields = aggregate.AGGREGATE_FIELDS["namechange"]
    rows = size = 0
    chunks: list[pd.DataFrame] = []
    for count, code in enumerate(validated, 1):
        label = f"namechange ts_code={code}"
        frame = call("namechange", ts_code=code, fields=",".join(fields))
        frame_size = aggregate._frame_bytes(frame, label)
        aggregate.validate_aggregate_frame(frame, "namechange", label=label)
        if len(frame) >= aggregate.RESPONSE_ROW_GUARDS["namechange"]:
            raise aggregate.AggregateResponseError(f"{label}: potentially truncated response")
        if not frame["ts_code"].eq(code).all():
            raise aggregate.AggregateResponseError(f"{label}: response contains another security code")
        rows += len(frame)
        size += frame_size
        if rows > aggregate.MAX_CANDIDATE_ROWS or size > aggregate.MAX_CANDIDATE_BYTES:
            raise aggregate.AggregateResponseError(f"{label}: aggregate candidate budget exceeded")
        unique = aggregate._unambiguous(frame.loc[:, list(fields)], "namechange", label)
        if not unique.empty:
            chunks.append(unique)
        if progress is not None and (count % 200 == 0 or count == len(validated)):
            progress(count, len(validated), rows)
    candidate = (pd.concat(chunks, ignore_index=True) if chunks
                 else pd.DataFrame(columns=list(fields)))
    return aggregate._unambiguous(candidate, "namechange", "namechange full candidate")
