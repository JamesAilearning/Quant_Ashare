"""Constraint scope under a non-daily cadence (revision R1, 2026-07-18).

The N5 ignition (2026-07-18, result-blind) hit a 0.04-0.05pp
``max_per_name`` overage on a single HOLD day — market drift, not an
allocation decision. Validating hold days makes the same numeric cap
strictly harsher under N=5 than under N=1 (whose daily rebalance resets
weights before every check), breaking same-config comparability. The
revision scopes constraint validation to REBALANCE-EFFECT days
(thinned stamp + total lag); numbers, RAISE mode and every veto stay
untouched.

Coverage matrix (>=1 case per dimension):
  fill mapping    — stamp + lag trading days, calendar-driven.
  edge handling   — stamps off-calendar skipped; fills beyond the
                    calendar end skipped.
  scope filtering — only scoped days reach the constraint check
                    (drift-day violation ignored; scoped-day violation
                    still RAISEs).
  N=1 identity    — the daily path passes the FULL map (byte-identical
                    behaviour is exercised by the existing #336
                    contract tests; here we pin the helper contract).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.core.backtest_runner import BacktestRunner  # noqa: E402
from src.core.risk_constraints import (  # noqa: E402
    MinimalRiskConstraints,
    RiskConstraintError,
)

_CAL = [date(2024, 1, d) for d in (2, 3, 4, 5, 8, 9, 10, 11, 12, 15)]


def test_fill_mapping_stamp_plus_lag() -> None:
    days = BacktestRunner._constraint_scope_days(
        [date(2024, 1, 2), date(2024, 1, 9)], _CAL, lag=1)
    assert days == {"2024-01-03", "2024-01-10"}


def test_off_calendar_stamp_and_overflow_fill_skipped() -> None:
    days = BacktestRunner._constraint_scope_days(
        [date(2024, 1, 6),      # not a trading day -> skipped
         date(2024, 1, 15)],    # fill would fall beyond calendar end
        _CAL, lag=1)
    assert days == set()


def test_scope_filtering_ignores_drift_day_violation() -> None:
    # a violation on a HOLD day is drift; the same violation on a
    # rebalance-effect day still RAISEs.
    constraints = MinimalRiskConstraints()  # defaults: max_per_name 0.05
    positions = {
        "2024-01-03": {"SH600000": 0.04, "SZ000001": 0.04},   # scoped, ok
        "2024-01-04": {"SH600000": 0.09, "SZ000001": 0.04},   # drift day
    }
    scope = BacktestRunner._constraint_scope_days(
        [date(2024, 1, 2)], _CAL, lag=1)
    scoped = {d: w for d, w in positions.items() if d in scope}
    assert set(scoped) == {"2024-01-03"}
    constraints.apply(scoped)          # drift-day violation not checked
    with pytest.raises(RiskConstraintError):
        constraints.apply(positions)   # unscoped map still raises


def test_multi_cycle_scope_covers_every_rebalance() -> None:
    # N=5 over ten trading days -> two stamps -> two fill days.
    stamps = [_CAL[0], _CAL[5]]
    days = BacktestRunner._constraint_scope_days(stamps, _CAL, lag=1)
    assert days == {"2024-01-03", "2024-01-10"}
