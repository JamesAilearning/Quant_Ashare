"""Shared Alpha158 label-lookahead embargo validator.

qlib's Alpha158 default label is ``Ref($close, -2) / Ref($close, -1) - 1``
— i.e. the label at trading day ``t`` consumes close prices at ``t+1``
and ``t+2``. If two adjacent walk-forward segments (train/valid or
valid/test) are separated by fewer than :data:`LABEL_LOOKAHEAD_DAYS`
trading days, the trailing rows of the earlier segment compute labels
from prices that fall inside the later segment, **silently leaking
information across the boundary**. The validation loss the model uses
for early stopping is computed against partially-leaked labels,
biasing the run's OOS performance upward.

Previously this check lived only in ``web/operator_ui/training_guards.py``,
so the operator UI rejected leaky configurations but
``main.py`` / ``scripts/run_walk_forward.py`` / direct API callers
went through unchecked. This module is the shared source of truth;
both UI and core ``FeatureDatasetBuilder._validate`` call into it.

No qlib import here — callers supply the trading calendar (the UI
reads it from the bundle's calendar file; the core builder loads it
from qlib's already-initialised ``D.calendar``).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

# qlib's Alpha158 default label is ``Ref($close, -2) / Ref($close, -1) - 1``
# — the label at day t consumes close prices at t+1 and t+2. We
# require at least this many trading days of gap between adjacent
# walk-forward segments to prevent boundary-row label leakage.
LABEL_LOOKAHEAD_DAYS = 2


def trading_days_between(
    earlier: date, later: date, calendar: Sequence[date],
) -> int:
    """Return the number of trading days strictly between ``earlier``
    and ``later`` (exclusive on both sides).

    For example, given a calendar containing ``2025-09-30, 2025-10-09,
    2025-10-10`` and arguments ``(2025-09-30, 2025-10-09)``, the gap is
    ``0`` — the two endpoints are adjacent trading days, so there is
    no embargo day in between.

    The implementation is a linear scan; the calendar is short enough
    (a few thousand entries per year) that a sorted bisect would only
    save microseconds and isn't worth the added complexity.
    """
    if later <= earlier:
        return 0
    return sum(1 for day in calendar if earlier < day < later)


def validate_segment_embargo(
    *,
    train_end: date,
    valid_start: date,
    valid_end: date,
    test_start: date,
    calendar: Sequence[date],
    lookahead_days: int = LABEL_LOOKAHEAD_DAYS,
) -> list[str]:
    """Return error messages for any adjacent-segment pair that violates
    the Alpha158 label-lookahead embargo.

    Empty list = both ``(train→valid)`` and ``(valid→test)`` boundaries
    are clear. The caller decides whether to raise (core builder) or
    surface as a UI-level error message (operator UI).

    Parameters
    ----------
    train_end, valid_start, valid_end, test_start
        The four segment boundaries. Pre-parsed to :class:`datetime.date`
        so this helper has no parsing concerns.
    calendar
        Sorted-or-unsorted sequence of trading-day dates covering at
        least the range spanned by the boundaries. Out-of-range entries
        are filtered by the ``earlier < day < later`` clauses.
    lookahead_days
        How many trading days the label peeks ahead. ``2`` is correct
        for Alpha158's default label; downstream handlers with
        different label horizons should override.

    Returns
    -------
    list of str
        One error message per offending boundary. Stable order
        (train/valid first, then valid/test) so callers can render or
        join deterministically.

    Notes
    -----
    - When ``later <= earlier`` for a pair (a separate validator has
      already flagged the non-monotone ordering), we skip that pair to
      avoid double-reporting. Only the date-ordering validator should
      flag those.
    - When the calendar is empty, every gap is ``0`` and both pairs
      will report a violation. That is the correct fail-loud behavior
      — the caller should not have called this helper without a
      calendar, and if they did the result is "everything fails", not
      "everything silently passes".
    """
    errors: list[str] = []
    pairs = (
        ("train_end", train_end, "valid_start", valid_start),
        ("valid_end", valid_end, "test_start", test_start),
    )
    for e_name, e_date, l_name, l_date in pairs:
        if l_date <= e_date:
            # Other validators already flag non-monotone ordering.
            continue
        gap = trading_days_between(e_date, l_date, calendar)
        if gap < lookahead_days:
            errors.append(
                f"{e_name} ({e_date.isoformat()}) → {l_name} "
                f"({l_date.isoformat()}) is only {gap} trading day(s) "
                f"apart, less than the {lookahead_days} required for "
                f"Alpha158 label lookahead. The trailing rows of the "
                f"earlier segment would compute labels from prices "
                f"inside the later segment, leaking information across "
                f"the boundary and biasing OOS metrics upward."
            )
    return errors


__all__ = [
    "LABEL_LOOKAHEAD_DAYS",
    "trading_days_between",
    "validate_segment_embargo",
]
