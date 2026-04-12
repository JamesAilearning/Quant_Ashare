"""Trading calendar abstraction for V2 data loaders.

Why this module exists
----------------------
Loaders need an accurate count of "expected trading days inside an
inclusive [start, end] window" to compute coverage ratios. The
benchmark loader historically used a constant approximation
(``span_days * 0.63``) which is wrong for any window that contains
A-share holidays (Spring Festival, National Day, etc.).

This module provides a Protocol-shaped abstraction so loaders can
depend on the *interface* without importing qlib. Three pieces:

- :class:`TradingCalendarError` -- raised on misuse or adapter failure.
- :class:`TradingCalendar` -- ``typing.Protocol`` declaring the single
  method ``count_trading_days(start, end) -> int``.
- :class:`StaticTradingCalendar` -- in-memory deterministic
  implementation, used by tests and as the internal cache layer of
  :class:`QlibTradingCalendar`.
- :class:`QlibTradingCalendar` -- production adapter that lazily
  imports ``qlib.data.D``, fetches the full calendar on first call,
  caches it as a :class:`StaticTradingCalendar`, and never re-queries
  qlib afterwards.

Boundaries
----------
- This module does NOT call ``qlib.init``. Callers must initialize the
  canonical qlib runtime via ``src.core.qlib_runtime.init_qlib_canonical``
  before constructing :class:`QlibTradingCalendar`.
- Importing this module does NOT import qlib. The qlib import is
  performed lazily inside :meth:`QlibTradingCalendar.count_trading_days`,
  so contract-only environments without qlib remain functional.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date
from typing import Iterable, Optional, Protocol, runtime_checkable


class TradingCalendarError(ValueError):
    """Raised on calendar misuse or adapter import/fetch failure."""


@runtime_checkable
class TradingCalendar(Protocol):
    """Minimum interface every trading-calendar implementation must expose.

    The interval ``[start, end]`` is treated as **inclusive on both ends**.
    Implementations SHALL return ``0`` when ``end < start`` rather than
    raising, so callers do not need to guard reversed intervals.
    """

    def count_trading_days(self, start: date, end: date) -> int: ...


class StaticTradingCalendar:
    """In-memory deterministic trading calendar.

    Construction sorts and deduplicates the input into an immutable
    tuple, so the calendar object is safe to share. Lookups use
    bisection for ``O(log n)`` queries on multi-thousand-entry
    calendars.
    """

    __slots__ = ("_dates",)

    def __init__(self, trading_dates: Iterable[date]) -> None:
        materialized: list[date] = []
        for value in trading_dates:
            if not isinstance(value, date):
                raise TradingCalendarError(
                    "StaticTradingCalendar requires datetime.date instances; "
                    f"got {type(value).__name__}."
                )
            materialized.append(value)
        self._dates: tuple[date, ...] = tuple(sorted(set(materialized)))

    def count_trading_days(self, start: date, end: date) -> int:
        if not isinstance(start, date) or not isinstance(end, date):
            raise TradingCalendarError(
                "count_trading_days requires datetime.date instances for "
                "both start and end."
            )
        if end < start:
            return 0
        left = bisect_left(self._dates, start)
        right = bisect_right(self._dates, end)
        return right - left


class QlibTradingCalendar:
    """Adapter wrapping ``qlib.data.D.calendar``.

    Lazily imports qlib on the first :meth:`count_trading_days` call,
    fetches the full calendar once, and caches the result inside an
    internal :class:`StaticTradingCalendar`. Subsequent calls reuse the
    cache without touching qlib.
    """

    def __init__(self, freq: str = "day") -> None:
        self._freq = freq
        self._cache: Optional[StaticTradingCalendar] = None

    def count_trading_days(self, start: date, end: date) -> int:
        if self._cache is None:
            self._cache = self._fetch_and_build_cache()
        return self._cache.count_trading_days(start, end)

    def _fetch_and_build_cache(self) -> StaticTradingCalendar:
        try:
            from qlib.data import D  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise TradingCalendarError(
                "qlib is not importable. QlibTradingCalendar requires the "
                "canonical qlib runtime to be initialized first via "
                "src.core.qlib_runtime.init_qlib_canonical."
            ) from exc

        try:
            stamps = D.calendar(freq=self._freq)
        except Exception as exc:  # pragma: no cover - depends on qlib state
            raise TradingCalendarError(
                f"Failed to fetch qlib calendar (freq={self._freq!r}). "
                "Ensure src.core.qlib_runtime.init_qlib_canonical has been "
                f"called with a valid provider_uri. Underlying error: {exc}"
            ) from exc

        dates: list[date] = []
        for stamp in stamps:
            converted = _coerce_to_date(stamp)
            if converted is not None:
                dates.append(converted)
        return StaticTradingCalendar(dates)


def _coerce_to_date(value: object) -> Optional[date]:
    """Best-effort conversion of a qlib calendar entry to ``datetime.date``.

    qlib's ``D.calendar`` typically returns ``pandas.Timestamp`` objects
    which expose a ``.date()`` method. Strings and ``datetime`` objects
    also occur in older versions; this helper accepts all three shapes
    and returns ``None`` for anything else so we never crash mid-fetch.
    """
    if type(value) is date:
        return value
    to_date = getattr(value, "date", None)
    if callable(to_date):
        try:
            result = to_date()
        except (TypeError, ValueError, OverflowError):
            # Only catch errors specific to the .date() call:
            # TypeError  — unexpected argument in some pandas Timestamp variants
            # ValueError — out-of-range date value
            # OverflowError — date value overflows Python's date range
            # Everything else (AttributeError, NameError, …) is a programmer
            # error and must propagate so it isn't silently swallowed.
            return None
        if isinstance(result, date):
            return result
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None
