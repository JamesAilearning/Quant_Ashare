"""Explicit per-security name history; pure validation and bounded collection.

This is not a fallback for the announcement-range query. The caller chooses the
mode, verifies snapshot provenance, and owns the only atomic publication.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pandas as pd

from src.data.tushare import aggregate_response as aggregate

MAX_NAMECHANGE_SECURITIES = 10_000
# Official stock_basic response cap; saturation cannot attest a full snapshot.
STOCK_BASIC_ROW_GUARD = 6_000
_CODE = re.compile(r"[0-9]{6}\.(SH|SZ|BJ)")


def _require_code(value: object, label: str) -> str:
    if not isinstance(value, str) or _CODE.fullmatch(value) is None:
        raise aggregate.AggregateResponseError(f"{label}: invalid security code")
    return value


def namechange_security_universe(
    active: pd.DataFrame, delisted: pd.DataFrame, retained: pd.DataFrame,
    *, snapshot_date: str,
) -> tuple[str, ...]:
    """Freeze validated L/D/retained codes; never infer a missing snapshot."""
    aggregate._real_date(snapshot_date, "namechange snapshot")
    buckets: list[set[str]] = []
    for frame, status in ((active, "L"), (delisted, "D")):
        label = f"namechange stock_basic {status}"
        aggregate._frame_bytes(frame, label)
        required = {"ts_code", "list_status", "snapshot_date"}
        if not frame.columns.is_unique or not required.issubset(frame.columns):
            raise aggregate.AggregateResponseError(f"{label}: missing or duplicate snapshot fields")
        if frame.empty or len(frame) >= STOCK_BASIC_ROW_GUARD:
            raise aggregate.AggregateResponseError(f"{label}: empty or saturated snapshot")
        codes = [_require_code(value, label) for value in frame["ts_code"]]
        if len(codes) != len(set(codes)):
            raise aggregate.AggregateResponseError(f"{label}: duplicate security codes")
        for column, expected in (("list_status", status), ("snapshot_date", snapshot_date)):
            if any(not isinstance(value, str) or value != expected for value in frame[column]):
                raise aggregate.AggregateResponseError(f"{label}: invalid or stale {column}")
        buckets.append(set(codes))
    if buckets[0] & buckets[1]:
        raise aggregate.AggregateResponseError("namechange stock_basic: overlapping L/D codes")
    aggregate.require_retained_keys(retained, retained, "namechange", label="namechange retained")
    all_codes = buckets[0] | buckets[1] | {
        _require_code(value, "namechange retained") for value in retained["ts_code"]
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
    validated = [_require_code(code, "namechange request") for code in securities]
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
