"""Unit tests for PR6.4 NAV-derived annualisation helpers.

These guard the fix for the operator's run-review feedback that
``performance.annual_return`` was actually the **excess** return
annualised, not the strategy's own annualised NAV growth. The new
``strategy_annualized_return`` / ``benchmark_annualized_return``
fields are computed from the NAV frame so the UI can show the right
number with the right label.
"""

from __future__ import annotations

import math
import sys as _sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))


class AnnualiseTotalReturnTests(unittest.TestCase):
    def test_one_year_window_returns_total_unchanged(self) -> None:
        from src.core.pipeline_result_artifacts import _annualise_total_return

        result = _annualise_total_return(0.10, 252)
        assert result is not None
        self.assertAlmostEqual(result, 0.10, places=6)

    def test_short_window_amplifies_total_return(self) -> None:
        """A 10.9% total return over 38 trading days — exactly the
        operator's pipeline_20260524_221821_b978a811 case — geometrically
        annualises to ~+97%. The number is mathematically correct but
        unstable; the UI surfaces a short-window banner separately."""

        from src.core.pipeline_result_artifacts import _annualise_total_return

        result = _annualise_total_return(0.109, 38)
        assert result is not None
        # (1.109) ** (252/38) ≈ 1.97 ⇒ +97.x%
        self.assertGreater(result, 0.85)
        self.assertLess(result, 1.10)

    def test_two_year_window_compounds_below_half(self) -> None:
        from src.core.pipeline_result_artifacts import _annualise_total_return

        # 50% over 504 trading days ≈ sqrt(1.5) - 1 ≈ +22.5% annualised
        result = _annualise_total_return(0.50, 504)
        assert result is not None
        self.assertAlmostEqual(result, math.sqrt(1.5) - 1.0, places=4)

    def test_returns_none_for_missing_inputs(self) -> None:
        from src.core.pipeline_result_artifacts import _annualise_total_return

        self.assertIsNone(_annualise_total_return(None, 252))
        self.assertIsNone(_annualise_total_return(0.10, None))
        self.assertIsNone(_annualise_total_return(None, None))

    def test_returns_none_for_zero_or_negative_window(self) -> None:
        from src.core.pipeline_result_artifacts import _annualise_total_return

        self.assertIsNone(_annualise_total_return(0.10, 0))
        self.assertIsNone(_annualise_total_return(0.10, -5))

    def test_returns_none_for_total_loss(self) -> None:
        """A total return of -100% leaves NAV at 0; geometric annualisation
        is undefined. Don't fabricate — return None per AGENTS.md #8."""

        from src.core.pipeline_result_artifacts import _annualise_total_return

        self.assertIsNone(_annualise_total_return(-1.0, 100))
        self.assertIsNone(_annualise_total_return(-1.5, 100))


class NavTotalReturnForBenchmarkTests(unittest.TestCase):
    def test_benchmark_total_return_from_nav_frame(self) -> None:
        import pandas as pd

        from src.core.pipeline_result_artifacts import _nav_total_return_for

        frame = pd.DataFrame({
            "date": pd.to_datetime(["2025-10-09", "2025-12-01"]),
            "strategy_nav": [1.0, 1.109],
            "benchmark_nav": [1.0, 0.986],
        })
        self.assertAlmostEqual(_nav_total_return_for(frame, "benchmark_nav"), -0.014, places=4)
        self.assertAlmostEqual(_nav_total_return_for(frame, "strategy_nav"), 0.109, places=4)

    def test_missing_column_returns_none(self) -> None:
        import pandas as pd

        from src.core.pipeline_result_artifacts import _nav_total_return_for

        frame = pd.DataFrame({"date": pd.to_datetime(["2025-10-09"]), "strategy_nav": [1.0]})
        self.assertIsNone(_nav_total_return_for(frame, "benchmark_nav"))

    def test_all_nan_column_returns_none(self) -> None:
        import pandas as pd

        from src.core.pipeline_result_artifacts import _nav_total_return_for

        frame = pd.DataFrame({
            "date": pd.to_datetime(["2025-10-09", "2025-10-10"]),
            "strategy_nav": [1.0, 1.05],
            "benchmark_nav": [float("nan"), float("nan")],
        })
        self.assertIsNone(_nav_total_return_for(frame, "benchmark_nav"))


class NavTradingDaysCountTests(unittest.TestCase):
    def test_counts_finite_strategy_rows(self) -> None:
        import pandas as pd

        from src.core.pipeline_result_artifacts import _nav_n_trading_days

        frame = pd.DataFrame({
            "date": pd.to_datetime(["2025-10-09", "2025-10-10", "2025-10-13"]),
            "strategy_nav": [1.0, 1.01, 1.02],
        })
        self.assertEqual(_nav_n_trading_days(frame), 3)

    def test_returns_none_for_empty_frame(self) -> None:
        import pandas as pd

        from src.core.pipeline_result_artifacts import _nav_n_trading_days

        self.assertIsNone(_nav_n_trading_days(None))
        self.assertIsNone(_nav_n_trading_days(pd.DataFrame()))

    def test_skips_nan_rows(self) -> None:
        import pandas as pd

        from src.core.pipeline_result_artifacts import _nav_n_trading_days

        frame = pd.DataFrame({
            "date": pd.to_datetime(["2025-10-09", "2025-10-10", "2025-10-13"]),
            "strategy_nav": [1.0, float("nan"), 1.02],
        })
        self.assertEqual(_nav_n_trading_days(frame), 2)


if __name__ == "__main__":
    unittest.main()
