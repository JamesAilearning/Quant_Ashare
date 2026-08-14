"""Governance: the two calendar sources of the #213 tail boundary are
each pinned to the calendar they are SUPPOSED to read.

The runner-side guard (``BacktestRunner._load_execution_calendar``) and
the walk-forward generator's tail-headroom check (#327,
``WalkForwardEngine._load_trading_calendar``) deliberately read
DIFFERENT calendars, and the asymmetry is load-bearing:

* The runner guard judges whether qlib's executor will overflow, so it
  must read what ``TradeCalendarManager.reset`` reads —
  ``Cal.calendar(freq=..., future=True)``. Reading the data calendar
  instead would refuse windows qlib could actually run once a bundle
  ships ``day_future.txt``.
* The engine's calendar ALSO feeds embargo pull-back, coverage checks
  and the whole window enumeration. Flipping it to the future calendar
  would let window generation emit folds over future sessions that have
  no data. Its data-calendar sourcing is therefore a conservative
  choice, recorded as rejected-alternative #2 in
  ``openspec/changes/2026-08-12-backtest-calendar-tail-boundary``.

Both directions are silent failures if flipped — no test would
otherwise fail — so they are pinned here by asserting the actual
``D.calendar`` call each seam makes.

On mocking ``qlib.data``: the repo's PR7 rule says tests should mock at
the right boundary rather than at ``qlib.data.D``. That rule targets
tests of BUSINESS logic, which should patch the seam. These two pins
are about the seams THEMSELVES — the assertion IS "which arguments
reach ``D.calendar``" — so intercepting ``D`` is the only boundary that
can express it. Patching the seam here would assert nothing.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


class CalendarSourcingPins(unittest.TestCase):
    def test_runner_guard_reads_the_future_execution_calendar(self) -> None:
        from src.core.backtest_runner import BacktestRunner

        fake = MagicMock()
        fake.D.calendar.return_value = []
        with patch.dict("sys.modules", {"qlib.data": fake}):
            BacktestRunner._load_execution_calendar(freq="day")

        fake.D.calendar.assert_called_once_with(freq="day", future=True)

    def test_walk_forward_generator_reads_the_data_calendar(self) -> None:
        """``future=True`` here would silently widen window generation
        onto sessions with no data (rejected alternative #2). The engine
        seam must call ``D.calendar()`` with NO future kwarg."""
        from src.core.walk_forward.engine import WalkForwardEngine

        fake = MagicMock()
        fake.D.calendar.return_value = []
        with patch.dict("sys.modules", {"qlib.data": fake}):
            WalkForwardEngine._load_trading_calendar()

        fake.D.calendar.assert_called_once_with()
        _, kwargs = fake.D.calendar.call_args
        self.assertNotIn(
            "future", kwargs,
            "the walk-forward calendar must stay the DATA calendar — it "
            "also drives embargo pull-back, coverage checks and window "
            "enumeration, which must never range over data-less future "
            "sessions (rejected alternative #2 of the #213 change).",
        )


if __name__ == "__main__":
    unittest.main()
