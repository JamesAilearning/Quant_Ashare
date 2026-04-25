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

import threading
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

    Thread safety
    -------------
    Cache initialization is guarded by an instance lock: a first caller
    racing with a second on the same instance is guaranteed to see at
    most one ``_fetch_and_build_cache()`` call. Without the lock two
    threads could both read ``self._cache is None``, both invoke qlib,
    and the second write would overwrite a partially observed cache
    pointer. The consistency risk is small — both callers would build
    the same calendar from the same qlib data — but the wasted qlib IO
    and the potential for torn reads on the pointer (CPython's GIL
    makes this unlikely in practice but not guaranteed for non-CPython
    interpreters) are easy to avoid with a lock this cheap.

    The sibling ``src.core.qlib_runtime._INIT_LOCK`` uses the same
    pattern for the one-shot ``qlib.init`` call.
    """

    def __init__(self, freq: str = "day") -> None:
        self._freq = freq
        self._cache: Optional[StaticTradingCalendar] = None
        self._cache_lock = threading.Lock()

    def count_trading_days(self, start: date, end: date) -> int:
        # Double-checked locking: fast path when the cache is already
        # populated avoids lock acquisition on the hot path; the slow
        # path holds the lock while populating so concurrent initialisers
        # see a single fetch.
        cache = self._cache
        if cache is None:
            with self._cache_lock:
                cache = self._cache
                if cache is None:
                    cache = self._fetch_and_build_cache()
                    self._cache = cache
        return cache.count_trading_days(start, end)

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


def extend_end_by_trading_days(
    end_dt: Any,
    n_trading_days: int,
    *,
    logger: Any,
    caller_name: str,
) -> Any:
    """Return ``end_dt`` shifted forward by ``n_trading_days`` qlib trading
    days, or — if the calendar lookup fails / returns fewer days than
    requested — by ``n_trading_days * 3`` *calendar* days as a fallback.

    Both fallback branches log a **WARNING** through the supplied ``logger``
    so a degraded extension is visible. A silent fallback would mask
    provider mis-configuration, broken calendar APIs, or data-tail
    truncation behind an apparently-normal completion with quietly-shrunken
    forward returns.

    Why this lives here
    -------------------
    ``signal_analyzer`` and ``factor_analyzer`` previously each carried a
    line-for-line copy of this routine. Centralising avoids drift —
    fixing the calendar contract is now a one-line change.

    Parameters
    ----------
    end_dt
        Anything ``pd.Timestamp(...)`` accepts.
    n_trading_days
        How many trading days strictly after ``end_dt`` to step.
    logger
        Logger to emit WARNINGs on degraded paths. Caller-supplied so
        the warning shows up under the caller's logger name.
    caller_name
        Human-readable caller name (e.g. ``"SignalAnalyzer"``) prepended
        to WARNING messages so logs stay diagnostic.

    Returns
    -------
    A ``pd.Timestamp`` ``n_trading_days`` after ``end_dt`` if qlib has
    that many entries, otherwise the calendar-day fallback.
    """
    import pandas as pd

    end_ts = pd.Timestamp(end_dt)
    fallback = end_ts + pd.Timedelta(days=n_trading_days * 3)
    try:
        from qlib.data import D  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - environment-dependent
        logger.warning(
            "%s: qlib import failed inside extend_end_by_trading_days "
            "(%s: %s). Falling back to %d calendar-day padding (%s). "
            "Check qlib installation and provider_uri.",
            caller_name, type(exc).__name__, exc,
            n_trading_days * 3, fallback,
        )
        return fallback

    try:
        # 4× the requested days plus a 30-day buffer should always
        # exceed n_trading_days even across long A-share holidays
        # (Spring Festival, National Day Golden Week).
        future_end = end_ts + pd.Timedelta(days=n_trading_days * 4 + 30)
        cal = D.calendar(start_time=end_ts, end_time=future_end, freq="day")
        cal_after = [pd.Timestamp(d) for d in cal if pd.Timestamp(d) > end_ts]
        if len(cal_after) >= n_trading_days:
            return cal_after[n_trading_days - 1]
        logger.warning(
            "%s: qlib calendar returned only %d trading day(s) after %s; "
            "need %d. Falling back to calendar-day padding (%s). "
            "Forward returns near the tail will be NaN.",
            caller_name, len(cal_after), end_ts, n_trading_days, fallback,
        )
        return fallback
    except Exception as exc:
        logger.warning(
            "%s: qlib D.calendar lookup failed (%s: %s). Falling back to "
            "%d calendar-day padding (%s). Check qlib provider_uri and "
            "data bundle integrity.",
            caller_name, type(exc).__name__, exc,
            n_trading_days * 3, fallback,
        )
        return fallback


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
