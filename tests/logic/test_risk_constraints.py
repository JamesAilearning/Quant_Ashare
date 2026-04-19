"""Tests for src.core.risk_constraints — portfolio risk constraint engine."""

import unittest
from unittest.mock import patch

import pandas as pd
import numpy as np

from src.core.risk_constraints import (
    RiskConstraintConfig,
    RiskConstraintEngine,
    RiskConstraintError,
    RiskConstraintResult,
)


def _make_predictions(n_dates=5, n_stocks=20):
    """Create synthetic predictions with (datetime, instrument) MultiIndex."""
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    # Mix of sectors via stock codes
    stocks = (
        [f"SH60000{i}" for i in range(5)] +  # SH_Main
        [f"SZ00000{i}" for i in range(5)] +  # SZ_Main
        [f"SZ30000{i}" for i in range(5)] +  # ChiNext
        [f"SH68800{i}" for i in range(5)]    # STAR
    )[:n_stocks]
    idx = pd.MultiIndex.from_product([dates, stocks], names=["datetime", "instrument"])
    np.random.seed(42)
    return pd.Series(np.random.randn(len(idx)), index=idx)


class RiskConstraintValidationTests(unittest.TestCase):
    """Unit tests that do NOT require qlib."""

    def test_rejects_when_qlib_not_initialized(self):
        predictions = _make_predictions()
        with patch("src.core.risk_constraints.is_canonical_qlib_initialized", return_value=False):
            with self.assertRaises(RiskConstraintError):
                RiskConstraintEngine.apply(predictions)

    def test_rejects_non_series(self):
        with patch("src.core.risk_constraints.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaises(RiskConstraintError):
                RiskConstraintEngine.apply([1, 2, 3])

    def test_rejects_non_multiindex(self):
        with patch("src.core.risk_constraints.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaises(RiskConstraintError):
                RiskConstraintEngine.apply(pd.Series([1.0], index=[0]))

    def test_default_config(self):
        cfg = RiskConstraintConfig()
        self.assertEqual(cfg.max_stock_weight, 0.05)
        self.assertEqual(cfg.max_industry_weight, 0.30)
        self.assertEqual(cfg.max_stocks_per_industry, 10)

    def test_industry_limit_applied(self):
        predictions = _make_predictions(n_dates=3, n_stocks=20)
        with patch("src.core.risk_constraints.is_canonical_qlib_initialized", return_value=True):
            result = RiskConstraintEngine.apply(
                predictions,
                config=RiskConstraintConfig(
                    max_stocks_per_industry=2,
                    topk=10,
                ),
            )
            self.assertIsInstance(result, RiskConstraintResult)
            # Should have removed some stocks
            self.assertGreater(result.stocks_removed, 0)

    def test_no_limit_passes_through(self):
        predictions = _make_predictions(n_dates=2, n_stocks=10)
        with patch("src.core.risk_constraints.is_canonical_qlib_initialized", return_value=True):
            result = RiskConstraintEngine.apply(
                predictions,
                config=RiskConstraintConfig(
                    max_stocks_per_industry=100,  # effectively no limit
                    topk=10,
                ),
            )
            self.assertEqual(result.stocks_removed, 0)

    def test_code_based_sector_map(self):
        instruments = ["SH600000", "SZ000001", "SZ300001", "SH688001"]
        sector_map = RiskConstraintEngine._code_based_sector_map(instruments)
        self.assertEqual(sector_map["SH600000"], "SH_Main")
        self.assertEqual(sector_map["SZ000001"], "SZ_Main")
        self.assertEqual(sector_map["SZ300001"], "ChiNext")
        self.assertEqual(sector_map["SH688001"], "STAR")


class IndustryMapResolutionTests(unittest.TestCase):
    """Regression guards for P2d: _get_industry_map must respect an
    explicit taxonomy and not silently pretend to consult qlib.

    The old implementation called ``D.instruments(market="all")`` and
    discarded the result, masking real errors behind a bare
    ``except Exception`` that unconditionally fell back to the prefix
    heuristic. The new contract is: explicit map wins; otherwise
    prefix heuristic + INFO log.
    """

    def test_explicit_industry_map_overrides_fallback(self):
        """Passing RiskConstraintConfig(industry_map={...}) must route
        through the caller's taxonomy, not the prefix heuristic."""
        predictions = _make_predictions(n_dates=2, n_stocks=20)
        # A deliberately weird mapping — nothing a code-prefix heuristic
        # would ever produce. If this wins, we know the override path
        # is live.
        custom_map = {
            inst: "CUSTOM_BUCKET"
            for inst in predictions.index.get_level_values(1).unique()
        }
        config = RiskConstraintConfig(industry_map=custom_map)

        resolved = RiskConstraintEngine._get_industry_map(predictions, config)
        self.assertEqual(set(resolved.values()), {"CUSTOM_BUCKET"})
        # Must be a real dict (caller's mapping type may not be dict).
        self.assertIsInstance(resolved, dict)

    def test_fallback_logs_info_when_no_explicit_map(self):
        """No explicit map → code-based fallback + INFO log so the
        operator knows a rough heuristic is in force.

        The old code swallowed qlib import/API errors via
        ``except Exception`` without any log — breakages were
        completely invisible.
        """
        predictions = _make_predictions(n_dates=1, n_stocks=10)
        config = RiskConstraintConfig()  # industry_map=None (default)

        with patch("src.core.risk_constraints._logger") as mock_logger:
            resolved = RiskConstraintEngine._get_industry_map(predictions, config)

        # Fallback map produced (prefixes yield SH_Main / SZ_Main / etc.)
        self.assertGreater(len(resolved), 0)
        self.assertTrue(
            any("CUSTOM" not in v for v in resolved.values()),
            "fallback should produce heuristic buckets, not custom ones",
        )
        # INFO log must mention the fallback so silent downgrade doesn't hide.
        mock_logger.info.assert_called_once()
        msg = mock_logger.info.call_args[0][0]
        self.assertIn("code-based sector heuristic", msg)

    def test_get_industry_map_takes_config(self):
        """Regression: the helper's signature changed from
        ``(predictions,)`` to ``(predictions, config)``. Callers inside
        the module must route the config through.
        """
        import inspect
        sig = inspect.signature(RiskConstraintEngine._get_industry_map)
        # self + (predictions, config)
        self.assertIn("config", sig.parameters)

    def test_industry_map_flows_through_apply(self):
        """End-to-end: a custom industry_map passed via config changes
        which stocks are removed, proving it actually reaches
        _apply_industry_limit."""
        predictions = _make_predictions(n_dates=1, n_stocks=20)
        # Put every instrument into the SAME industry → with
        # max_stocks_per_industry=3, exactly topk*2 - 3 = 17 get removed
        # from the top-40 candidate pool on that single day (our
        # predictions have only 20 rows, so it's 20 - 3 = 17).
        all_in_one = {
            inst: "ONE"
            for inst in predictions.index.get_level_values(1).unique()
        }
        with patch(
            "src.core.risk_constraints.is_canonical_qlib_initialized",
            return_value=True,
        ):
            result = RiskConstraintEngine.apply(
                predictions,
                config=RiskConstraintConfig(
                    industry_map=all_in_one,
                    max_stocks_per_industry=3,
                    topk=10,
                ),
            )
        # Everything crammed into one industry → aggressive removal.
        self.assertGreater(result.stocks_removed, 10)


if __name__ == "__main__":
    unittest.main()
