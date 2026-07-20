"""ISO-week rebalance-day schedule for production serving (PR-A of
2026-07-20-csi800-n5-production-promotion, DP-2).

The certified N5 campaign anchored folds with ``fold_phase``; production
has no fold, so the signed production anchor is **the first trading day
of each ISO week** (same schedule family as the walk-forward
``rebalance_anchor="iso_week"`` — see WalkForwardConfig). This module is
the single serving-side authority for that determination; it is pure
(calendar in, verdict out) and deterministic so the ISO-year boundary,
long-holiday and single-day-week edges are unit-testable without qlib.

Semantics (spec: v2-daily-stock-recommendation, cadence requirements):

* ``is_rebalance_day(d)`` — True iff ``d`` is the FIRST trading day of
  its (iso_year, iso_week) group in the supplied trading calendar. A
  holiday Monday shifts the anchor to that week's first actual trading
  day; a week with no trading days simply has no rebalance day.
* ``next_rebalance_date(d)`` — the first rebalance day **>= d** (equals
  ``d`` itself on a rebalance day; the next week's anchor on a HOLD
  day). ``None`` when the calendar ends before one exists — callers
  disclose the gap rather than fabricating a date.
"""

from __future__ import annotations

from datetime import date


class RebalanceScheduleError(ValueError):
    """Raised on malformed calendar/date inputs (fail loud, no guessing)."""


def _parse_day(value: str) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise RebalanceScheduleError(
            f"unparseable trading day {value!r} — expected YYYY-MM-DD"
        ) from exc


def _iso_week_key(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return (iso[0], iso[1])


def is_rebalance_day(as_of: str, calendar: list[str]) -> bool:
    """True iff ``as_of`` is the first trading day of its ISO week.

    ``as_of`` MUST be a member of ``calendar`` (the caller has already
    resolved it to a real trading day); anything else is a contract
    violation and raises rather than silently returning False.
    """
    if not calendar:
        raise RebalanceScheduleError("empty trading calendar")
    target = _parse_day(as_of)
    days = sorted({_parse_day(c) for c in calendar})
    if target not in set(days):
        raise RebalanceScheduleError(
            f"as_of {as_of} is not a trading day of the supplied calendar"
        )
    week = _iso_week_key(target)
    first_of_week = min(d for d in days if _iso_week_key(d) == week)
    return target == first_of_week


def next_rebalance_date(as_of: str, calendar: list[str]) -> str | None:
    """First rebalance day >= ``as_of`` (``as_of`` itself when it IS one).

    Returns ``None`` when the calendar tail ends before the next ISO-week
    anchor exists — the caller must disclose that honestly (the field is
    nullable in the artifact) instead of inventing a date.
    """
    if not calendar:
        raise RebalanceScheduleError("empty trading calendar")
    target = _parse_day(as_of)
    days = sorted({_parse_day(c) for c in calendar})
    seen_weeks: set[tuple[int, int]] = set()
    for d in days:
        week = _iso_week_key(d)
        if week in seen_weeks:
            continue
        seen_weeks.add(week)
        # d is the first trading day of `week`.
        if d >= target:
            return d.isoformat()
    return None
