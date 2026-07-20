"""ISO-week rebalance schedule (PR-A of
2026-07-20-csi800-n5-production-promotion, DP-2).

Coverage matrix (>=1 case per dimension, spec: deterministic tests over
跨年 ISO 周边界 / 春节长假周 / 单日交易周):

  anchor rule      — first trading day of each ISO week; holiday Monday
                     shifts within the week.
  ISO-year boundary— Dec 29 2025 (Mon) opens ISO week 2026-W01.
  long holiday     — a whole non-trading week has NO rebalance day and
                     the anchor falls to the next week's first day.
  single-day week  — that lone day IS the rebalance day.
  next semantics   — >= as_of; equals as_of on a rebalance day; None
                     past the calendar tail.
  contract guards  — empty calendar / as_of not in calendar raise.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.inference.rebalance_schedule import (  # noqa: E402
    RebalanceScheduleError,
    is_rebalance_day,
    next_rebalance_date,
)

# Two plain consecutive weeks (Mon-Fri), 2024-01-08 .. 2024-01-19.
_TWO_WEEKS = [
    "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12",
    "2024-01-15", "2024-01-16", "2024-01-17", "2024-01-18", "2024-01-19",
]


def test_first_trading_day_of_week_is_rebalance_day() -> None:
    assert is_rebalance_day("2024-01-08", _TWO_WEEKS) is True
    assert is_rebalance_day("2024-01-09", _TWO_WEEKS) is False
    assert is_rebalance_day("2024-01-12", _TWO_WEEKS) is False
    assert is_rebalance_day("2024-01-15", _TWO_WEEKS) is True


def test_holiday_monday_shifts_anchor_within_week() -> None:
    # Monday 01-08 is a holiday -> Tuesday is the week's first trading
    # day and becomes the rebalance day.
    cal = [d for d in _TWO_WEEKS if d != "2024-01-08"]
    assert is_rebalance_day("2024-01-09", cal) is True
    assert is_rebalance_day("2024-01-10", cal) is False


def test_iso_year_boundary_week() -> None:
    # 2025-12-29 (Mon) belongs to ISO week 2026-W01; 2025-12-26 (Fri)
    # is ISO 2025-W52. The Monday opens a NEW iso week -> rebalance day,
    # even though the calendar year has not turned yet.
    cal = ["2025-12-22", "2025-12-26", "2025-12-29", "2025-12-31",
           "2026-01-05"]
    assert is_rebalance_day("2025-12-29", cal) is True
    assert is_rebalance_day("2025-12-31", cal) is False
    assert is_rebalance_day("2026-01-05", cal) is True


def test_whole_holiday_week_has_no_rebalance_day() -> None:
    # Spring-Festival style: the middle week has no trading days at all.
    cal = ["2024-02-05", "2024-02-06",           # week W06 (partial)
           # W07 fully closed
           "2024-02-19", "2024-02-20"]           # week W08 resumes
    # No date of W07 is in the calendar; the next anchor after the
    # holiday is W08's first trading day.
    assert next_rebalance_date("2024-02-06", cal) == "2024-02-19"
    assert is_rebalance_day("2024-02-19", cal) is True


def test_single_day_trading_week() -> None:
    cal = ["2024-01-08", "2024-01-17"]  # W03 has exactly one trading day
    assert is_rebalance_day("2024-01-17", cal) is True
    assert next_rebalance_date("2024-01-17", cal) == "2024-01-17"


def test_next_rebalance_date_semantics() -> None:
    # On a rebalance day: the day itself. On a HOLD day: next week's
    # anchor. Past the tail: None (disclosed, never fabricated).
    assert next_rebalance_date("2024-01-08", _TWO_WEEKS) == "2024-01-08"
    assert next_rebalance_date("2024-01-09", _TWO_WEEKS) == "2024-01-15"
    assert next_rebalance_date("2024-01-16", _TWO_WEEKS) is None


def test_contract_guards_raise() -> None:
    with pytest.raises(RebalanceScheduleError):
        is_rebalance_day("2024-01-08", [])
    with pytest.raises(RebalanceScheduleError):
        is_rebalance_day("2024-01-13", _TWO_WEEKS)  # Saturday, not in cal
    with pytest.raises(RebalanceScheduleError):
        is_rebalance_day("not-a-date", _TWO_WEEKS)
