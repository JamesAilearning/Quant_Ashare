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


if __name__ == "__main__":
    unittest.main()
