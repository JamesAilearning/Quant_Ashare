"""Bounded aggregate acquisition and publication checks; no disk or network I/O.

The caller supplies its canonical rate-limited/retried API call. These checks
reject observable truncation and lost records, not every possible vendor omission.
In particular, namechange date queries cannot attest undisclosed null-ann_date
history; there is deliberately no speculative pagination/partition fallback.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

AGGREGATE_FIELDS: dict[str, tuple[str, ...]] = {
    "namechange": ("ts_code", "name", "start_date", "end_date", "ann_date", "change_reason"),
    "suspend_d": ("ts_code", "trade_date", "suspend_timing", "suspend_type"),
}
# Conservative tripwires from observed unsafe full-range responses, NOT vendor
# guarantees. Check the raw count before deduplication can disguise saturation.
RESPONSE_ROW_GUARDS = {"namechange": 10_000, "suspend_d": 5_000}
MAX_PARTITION_CALLS = 2_000
MAX_CANDIDATE_ROWS = 1_000_000
MAX_CANDIDATE_BYTES = 256 * 1024 * 1024


class AggregateResponseError(ValueError):
    """The aggregate cannot be published safely; preserve the old file."""


def _real_date(value: Any, label: str) -> date:
    if (not isinstance(value, str) or len(value) != 8
            or not value.isascii() or not value.isdigit()):
        raise AggregateResponseError(f"{label}: expected a real YYYYMMDD date")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise AggregateResponseError(f"{label}: expected a real YYYYMMDD date") from exc


def _month_windows(start: date, end: date) -> Iterator[tuple[date, date]]:
    cursor = start
    while cursor <= end:
        last = date(cursor.year, cursor.month, calendar.monthrange(cursor.year, cursor.month)[1])
        window_end = min(last, end)
        yield cursor, window_end
        if window_end == end:  # also avoids overflow for 9999-12-31
            break
        cursor = window_end + timedelta(days=1)


def _frame_bytes(frame: Any, label: str) -> int:
    if not isinstance(frame, pd.DataFrame):
        raise AggregateResponseError(f"{label}: response is not a DataFrame")
    if len(frame) > MAX_CANDIDATE_ROWS:
        raise AggregateResponseError(f"{label}: aggregate row budget exceeded")
    size = int(frame.memory_usage(index=True, deep=True).sum())
    if size > MAX_CANDIDATE_BYTES:
        raise AggregateResponseError(f"{label}: aggregate memory budget exceeded")
    return size


def validate_aggregate_frame(
    frame: pd.DataFrame, endpoint: str, *, label: str,
    start_date: str | None = None, end_date: str | None = None,
) -> None:
    """Validate raw values without coercing, dropping or filling source rows."""
    _frame_bytes(frame, label)
    fields = AGGREGATE_FIELDS[endpoint]
    if not frame.columns.is_unique or set(frame.columns) != set(fields):
        raise AggregateResponseError(f"{label}: response schema must contain exactly {fields}")
    nullable = {"ann_date", "end_date", "change_reason", "suspend_timing"}
    for column in fields:
        for value in frame[column]:
            if not pd.api.types.is_scalar(value):
                raise AggregateResponseError(f"{label}: non-scalar {column}")
            if pd.isna(value):
                if column not in nullable:
                    raise AggregateResponseError(f"{label}: missing {column}")
            elif not isinstance(value, str) or not value.strip():
                raise AggregateResponseError(f"{label}: malformed {column}")
    dates = ("start_date", "end_date", "ann_date") if endpoint == "namechange" else ("trade_date",)
    for column in dates:
        for value in frame[column].dropna().unique():
            _real_date(value, f"{label}: {column}")
    if start_date is not None and end_date is not None:
        # Official namechange bounds filter ANNOUNCEMENT dates, not effective
        # start_date. Preserve null announcements instead of inventing dates.
        query_column = "ann_date" if endpoint == "namechange" else "trade_date"
        query_dates = frame[query_column].dropna()
        if not query_dates.between(start_date, end_date).all():
            raise AggregateResponseError(f"{label}: {query_column} outside requested window")


def _key_columns(endpoint: str) -> list[str]:
    return [column for column in AGGREGATE_FIELDS[endpoint] if column != "end_date"]


def _keys(frame: pd.DataFrame, endpoint: str) -> set[tuple[str | None, ...]]:
    return {
        tuple(None if pd.isna(value) else value for value in row)
        for row in frame[_key_columns(endpoint)].itertuples(index=False, name=None)
    }


def _unambiguous(frame: pd.DataFrame, endpoint: str, label: str) -> pd.DataFrame:
    unique = frame.drop_duplicates().reset_index(drop=True)
    if unique.duplicated(subset=_key_columns(endpoint)).any():
        raise AggregateResponseError(f"{label}: conflicting payloads for one business key")
    return unique


def require_retained_keys(
    candidate: pd.DataFrame, retained: pd.DataFrame, endpoint: str, *, label: str,
) -> None:
    """Allow only end_date correction; never union old rows into a partial fetch."""
    validate_aggregate_frame(retained, endpoint, label=label)
    _unambiguous(retained, endpoint, label)
    missing = _keys(retained, endpoint) - _keys(candidate, endpoint)
    if missing:
        raise AggregateResponseError(f"{label}: candidate loses {len(missing)} retained business keys")


def collect_aggregate_response(
    *, endpoint: str, start_date: str, end_date: str,
    call: Callable[..., pd.DataFrame],
) -> pd.DataFrame:
    """Build one candidate, or raise; leave persistence entirely to the caller.

    Suspension leaves cover the exact request once. Saturated parent observations
    must survive their child queries, so subdivision cannot hide dropped rows.
    API exceptions propagate unchanged to the fetcher's existing failure policy.
    """
    start = _real_date(start_date, endpoint)
    end = _real_date(end_date, endpoint)
    if start > end:
        raise AggregateResponseError(f"{endpoint}: reversed request interval")
    fields = AGGREGATE_FIELDS[endpoint]
    calls = 0
    accepted_rows = 0
    accepted_bytes = 0

    def collect_window(lo: date, hi: date) -> pd.DataFrame:
        nonlocal calls, accepted_rows, accepted_bytes
        lo_s, hi_s = lo.strftime("%Y%m%d"), hi.strftime("%Y%m%d")
        label = f"{endpoint} requested {lo_s}..{hi_s}"
        if calls >= MAX_PARTITION_CALLS:
            raise AggregateResponseError(f"{label}: partition call budget exceeded")
        calls += 1
        raw = call(endpoint, start_date=lo_s, end_date=hi_s, fields=",".join(fields))
        size = _frame_bytes(raw, label)
        validate_aggregate_frame(raw, endpoint, label=label, start_date=lo_s, end_date=hi_s)
        saturated = len(raw) >= RESPONSE_ROW_GUARDS[endpoint]
        if saturated:
            if endpoint == "namechange" or lo == hi:
                raise AggregateResponseError(
                    f"{label}: potentially truncated response reaches safety threshold "
                    f"{RESPONSE_ROW_GUARDS[endpoint]}; no unverified fallback"
                )
            midpoint = lo + timedelta(days=(hi - lo).days // 2)
            left = collect_window(lo, midpoint)
            right = collect_window(midpoint + timedelta(days=1), hi)
            combined = pd.concat([left, right], ignore_index=True)
            require_retained_keys(combined, raw, endpoint, label=f"{label}: saturated parent")
            return combined
        accepted_rows += len(raw)
        accepted_bytes += size
        if accepted_rows > MAX_CANDIDATE_ROWS or accepted_bytes > MAX_CANDIDATE_BYTES:
            raise AggregateResponseError(f"{label}: aggregate candidate budget exceeded")
        return _unambiguous(raw.loc[:, list(fields)], endpoint, label)

    windows = [(start, end)] if endpoint == "namechange" else _month_windows(start, end)
    chunks = [collect_window(lo, hi) for lo, hi in windows]
    nonempty = [chunk for chunk in chunks if not chunk.empty]
    candidate = (pd.concat(nonempty, ignore_index=True) if nonempty
                 else pd.DataFrame(columns=list(fields)))
    return _unambiguous(candidate, endpoint, f"{endpoint}: full candidate")
