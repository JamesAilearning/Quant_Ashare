"""An authenticated SSE schedule, deliberately separate from price calendars.

The snapshot plans a session; it never certifies future prices, tradability,
vendor finality or a completed production update. Recommendation is offline.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from src.contracts.recommendation_timing import CALENDAR_MAX_AGE, SESSION_CLOSE, aware_clock
from src.data._trade_cal import calendar_frame_defect

SNAPSHOT_SCHEMA_VERSION = 1
SOURCE_API = "trade_cal"
EXCHANGE = "SSE"
FIELDS = "exchange,cal_date,is_open"
MAX_SNAPSHOT_BYTES = 1024 * 1024
# Three years can include one leap day. This also bounds frame construction.
MAX_QUERY_DAYS = 1096
_SNAPSHOT_KEYS = {"schema_version", "source_api", "exchange", "query", "fetched_at", "rows"}
_QUERY_KEYS = {"start_date", "end_date", "fields"}
_ROW_KEYS = {"exchange", "cal_date", "is_open"}


class CalendarSnapshotError(ValueError):
    """The source schedule or its relationship with recorded prices is invalid."""


class CalendarClient(Protocol):
    def call(self, api_name: str, **params: Any) -> Any: ...


@dataclass(frozen=True)
class TradingCalendarSnapshot:
    path: Path
    sha256: str
    fetched_at: str
    start_date: str
    end_date: str
    open_dates: tuple[str, ...]

    @property
    def context(self) -> dict[str, Any]:
        """A fresh, serializable provenance projection, not a mutable authority."""
        return {
            "path": str(self.path), "sha256": self.sha256,
            "source_api": SOURCE_API, "exchange": EXCHANGE,
            "query": {"start_date": self.start_date, "end_date": self.end_date, "fields": FIELDS},
            "fetched_at": self.fetched_at,
        }


@dataclass(frozen=True)
class SessionDates:
    as_of_date: str
    entry_date: str
    latest_closed_date: str


def _compact_date(value: Any) -> date:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{8}", value) is None:
        raise CalendarSnapshotError("Calendar dates must be exact YYYYMMDD ASCII strings")
    try:
        return date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:]}")
    except ValueError as exc:
        raise CalendarSnapshotError("Calendar date is not a real date") from exc


def validate_query(start_date: str, end_date: str) -> tuple[date, date]:
    """Validate before client construction or an actual vendor call."""
    start, end = _compact_date(start_date), _compact_date(end_date)
    if end < start or (end - start).days + 1 > MAX_QUERY_DAYS:
        raise CalendarSnapshotError("Calendar query must be ordered and span at most three years")
    return start, end


def _acquisition_time(value: Any, now: datetime | None) -> datetime:
    if not isinstance(value, str):
        raise CalendarSnapshotError("Calendar fetched_at must be an aware ISO timestamp")
    try:
        acquired = aware_clock(datetime.fromisoformat(value))
    except ValueError as exc:
        raise CalendarSnapshotError("Calendar fetched_at must be an aware ISO timestamp") from exc
    if now is not None:
        clock = aware_clock(now)
        if acquired > clock or clock - acquired > CALENDAR_MAX_AGE:
            raise CalendarSnapshotError("Calendar snapshot is future or older than seven days")
    return acquired


def _validate_snapshot(snapshot: Any, *, now: datetime | None) -> tuple[date, date, tuple[str, ...]]:
    if not isinstance(snapshot, dict) or set(snapshot) != _SNAPSHOT_KEYS:
        raise CalendarSnapshotError("Calendar snapshot has an invalid field set")
    if type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != SNAPSHOT_SCHEMA_VERSION:
        raise CalendarSnapshotError("Unsupported calendar snapshot schema")
    if snapshot["source_api"] != SOURCE_API or snapshot["exchange"] != EXCHANGE:
        raise CalendarSnapshotError("Calendar source must be SSE trade_cal")
    query = snapshot["query"]
    if not isinstance(query, dict) or set(query) != _QUERY_KEYS or query["fields"] != FIELDS:
        raise CalendarSnapshotError("Calendar query binding is invalid")
    start, end = validate_query(query["start_date"], query["end_date"])
    _acquisition_time(snapshot["fetched_at"], now)
    rows = snapshot["rows"]
    expected = (end - start).days + 1
    if not isinstance(rows, list) or len(rows) != expected:
        raise CalendarSnapshotError("Calendar rows do not cover the complete requested range")
    parsed_dates: list[date] = []
    opened: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != _ROW_KEYS or row["exchange"] != EXCHANGE:
            raise CalendarSnapshotError("Calendar row must have the exact SSE field set")
        day = _compact_date(row["cal_date"])
        if day < start or day > end:
            raise CalendarSnapshotError("Calendar row lies outside the requested range")
        marker = row["is_open"]
        if type(marker) is not int or marker not in (0, 1):
            raise CalendarSnapshotError("Calendar is_open must be a strict integer 0 or 1")
        parsed_dates.append(day)
        if marker == 1:
            opened.append(day.isoformat())
    # The shared canonical validator remains the structural authority; the
    # outer checks additionally bind its full span to the recorded request.
    defect = calendar_frame_defect(pd.DataFrame(rows))
    if defect is not None:
        raise CalendarSnapshotError(f"Invalid calendar frame: {defect}")
    if min(parsed_dates) != start or max(parsed_dates) != end:
        raise CalendarSnapshotError("Calendar response truncates the requested boundaries")
    return start, end, tuple(sorted(opened))


def fetch_snapshot(
    client: CalendarClient, *, start_date: str, end_date: str, now: datetime,
) -> dict[str, Any]:
    """Make exactly one canonical call, validate it and retain only its fields."""
    validate_query(start_date, end_date)
    clock = aware_clock(now)
    frame = client.call(SOURCE_API, exchange=EXCHANGE, start_date=start_date, end_date=end_date, fields=FIELDS)
    if (not isinstance(frame, pd.DataFrame) or len(frame) > MAX_QUERY_DAYS
            or len(frame.columns) != 3 or set(frame.columns) != _ROW_KEYS):
        raise CalendarSnapshotError("trade_cal must return the exact bounded three-column frame")
    snapshot: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION, "source_api": SOURCE_API, "exchange": EXCHANGE,
        "query": {"start_date": start_date, "end_date": end_date, "fields": FIELDS},
        "fetched_at": clock.isoformat(), "rows": frame.to_dict(orient="records"),
    }
    _validate_snapshot(snapshot, now=clock)
    return snapshot


def _ordinary_file(value: os.stat_result) -> None:
    if (not stat.S_ISREG(value.st_mode) or value.st_nlink != 1
            or getattr(value, "st_file_attributes", 0) & 0x400):
        raise CalendarSnapshotError("Calendar snapshot must be an ordinary single-link file")


def _identity(value: os.stat_result) -> tuple[int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _read_bytes(path: Path) -> bytes:
    before = path.lstat()
    _ordinary_file(before)
    if not 0 < before.st_size <= MAX_SNAPSHOT_BYTES:
        raise CalendarSnapshotError("Calendar snapshot exceeds its byte budget or is empty")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        _ordinary_file(opened)
        if _identity(opened) != _identity(before):
            raise CalendarSnapshotError("Calendar snapshot changed before opening")
        payload = stream.read(MAX_SNAPSHOT_BYTES + 1)
        after_fd = os.fstat(stream.fileno())
    after_path = path.lstat()
    _ordinary_file(after_path)
    if (len(payload) != before.st_size or _identity(before) != _identity(after_fd)
            or _identity(before) != _identity(after_path)):
        raise CalendarSnapshotError("Calendar snapshot changed while reading")
    return payload


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CalendarSnapshotError("Calendar JSON contains duplicate object keys")
        result[key] = value
    return result


def _nonfinite(value: str) -> None:
    raise CalendarSnapshotError("Calendar JSON contains a non-finite number")


def _decode(payload: bytes) -> Any:
    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_nonfinite)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise CalendarSnapshotError("Calendar snapshot is not bounded valid UTF-8 JSON") from exc


def read_snapshot(path: str | Path, *, now: datetime) -> TradingCalendarSnapshot:
    """Authenticate and validate the same bounded bytes, with no network access."""
    clock = aware_clock(now)
    selected = Path(path).absolute()
    payload = _read_bytes(selected)
    snapshot = _decode(payload)
    _, _, opened = _validate_snapshot(snapshot, now=clock)
    return TradingCalendarSnapshot(
        path=selected, sha256=hashlib.sha256(payload).hexdigest(), fetched_at=snapshot["fetched_at"],
        start_date=snapshot["query"]["start_date"], end_date=snapshot["query"]["end_date"], open_dates=opened,
    )


def write_snapshot(path: str | Path, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Publish once, never replace; fsync private bytes before making them visible.

    Windows rename is no-replace. POSIX uses a same-directory no-replace link
    publication and removes the private name immediately (no shared data root).
    """
    _validate_snapshot(snapshot, now=None)
    payload = (json.dumps(snapshot, ensure_ascii=True, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    if len(payload) > MAX_SNAPSHOT_BYTES:
        raise CalendarSnapshotError("Calendar snapshot exceeds its byte budget")
    target = Path(path).absolute()
    if os.path.lexists(target):
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, private_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    private = Path(private_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if _read_bytes(private) != payload:
            raise CalendarSnapshotError("Private calendar publication bytes changed")
        if os.name == "nt":
            os.rename(private, target)
        else:
            os.link(private, target)
            private.unlink()
        if _read_bytes(target) != payload:
            raise CalendarSnapshotError("Published calendar bytes differ from the validated snapshot")
    finally:
        if private.exists():
            private.unlink()
    return {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def _price_date(value: Any) -> date:
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise CalendarSnapshotError("Price calendar requires exact ISO dates") from exc
        if parsed.isoformat() != value:
            raise CalendarSnapshotError("Price calendar requires exact ISO dates")
        return parsed
    if value is pd.NaT:
        raise CalendarSnapshotError("Price calendar contains NaT")
    if isinstance(value, datetime):
        if any((value.hour, value.minute, value.second, value.microsecond, getattr(value, "nanosecond", 0))):
            raise CalendarSnapshotError("Price calendar contains an intraday timestamp")
        return value.date()
    if isinstance(value, date):
        return value
    raise CalendarSnapshotError("Price calendar contains an unsupported date value")


def resolve_session_dates(
    price_calendar: Iterable[Any], snapshot: TradingCalendarSnapshot, *, now: datetime,
    as_of_date: str | None = None,
) -> SessionDates:
    """Plan T+1 from source schedule; never invent a recorded price session."""
    clock = aware_clock(now)
    _acquisition_time(snapshot.fetched_at, clock)
    start, end = validate_query(snapshot.start_date, snapshot.end_date)
    today = clock.date()
    if not start <= today <= end:
        raise CalendarSnapshotError("Calendar schedule does not cover the clock date")
    prices = tuple(_price_date(value) for value in price_calendar)
    if not prices or any(left >= right for left, right in zip(prices, prices[1:], strict=False)):
        raise CalendarSnapshotError("Price calendar must be nonempty, ordered and unique")
    opened = tuple(date.fromisoformat(value) for value in snapshot.open_dates)
    closed = tuple(day for day in opened if day < today or (day == today and clock.time() >= SESSION_CLOSE))
    if not closed:
        raise CalendarSnapshotError("Calendar schedule has no known closed session")
    latest = closed[-1]
    if prices[-1] > latest:
        raise CalendarSnapshotError("Provider contains an intraday or future price session")
    overlap_start, overlap_end = max(prices[0], start), min(prices[-1], end)
    if overlap_start > overlap_end:
        raise CalendarSnapshotError("Price and schedule calendars do not overlap")
    if (tuple(day for day in prices if overlap_start <= day <= overlap_end)
            != tuple(day for day in opened if overlap_start <= day <= overlap_end)):
        raise CalendarSnapshotError("Price and schedule sessions disagree in their overlapping span")
    if as_of_date is None:
        if prices[-1] != latest:
            raise CalendarSnapshotError("Provider tail is not the latest closed session")
        decision = latest
    else:
        decision = _price_date(as_of_date)
        if decision not in prices or decision > latest:
            raise CalendarSnapshotError("Explicit as-of must be a recorded closed price session")
    if not start <= decision <= end:
        raise CalendarSnapshotError("Calendar schedule does not cover the decision session")
    following = next((day for day in opened if day > decision), None)
    if following is None:
        raise CalendarSnapshotError("Calendar schedule has no planned next open session")
    return SessionDates(decision.isoformat(), following.isoformat(), latest.isoformat())
