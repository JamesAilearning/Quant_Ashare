"""Tests for PerformanceAttribution — structural + E2E."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from src.core.performance_attribution import (
    AttributionConfig,
    AttributionResult,
    MonthlyReturn,
    PerformanceAttribution,
    PerformanceAttributionError,
    SectorAttribution,
)


class PerformanceAttributionStructuralTests(unittest.TestCase):
    """Tests that don't require qlib data."""

    def test_rejects_when_qlib_not_initialized(self) -> None:
        with patch("src.core.performance_attribution.is_canonical_qlib_initialized", return_value=False):
            with self.assertRaisesRegex(PerformanceAttributionError, "not initialized"):
                PerformanceAttribution.analyze(
                    return_series={"return": {}},
                    predictions=pd.Series(dtype=float),
                )

    def test_rejects_missing_return_key(self) -> None:
        with patch("src.core.performance_attribution.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(PerformanceAttributionError, "return"):
                PerformanceAttribution.analyze(
                    return_series={"bench": {}},
                    predictions=pd.Series(dtype=float),
                )

    def test_config_defaults(self) -> None:
        cfg = AttributionConfig()
        self.assertTrue(cfg.use_code_based_sectors)
        self.assertEqual(cfg.benchmark_code, "SH000300")

    def test_sector_attribution_dataclass(self) -> None:
        sa = SectorAttribution(
            sector="SH_Main", portfolio_weight=0.5, benchmark_weight=0.3,
            portfolio_return=0.1, benchmark_return=0.05,
            allocation_effect=0.01, selection_effect=0.02,
            interaction_effect=0.005, total_effect=0.035,
        )
        self.assertEqual(sa.sector, "SH_Main")
        self.assertAlmostEqual(sa.total_effect, 0.035)

    def test_monthly_return_dataclass(self) -> None:
        mr = MonthlyReturn(year=2025, month=7, portfolio_return=0.03,
                           benchmark_return=0.01, excess_return=0.02)
        self.assertEqual(mr.year, 2025)
        self.assertAlmostEqual(mr.excess_return, 0.02)

    def test_code_based_sector_map(self) -> None:
        instruments = ["SH600000", "SZ300001", "SZ002001", "SH688001", "SZ000001"]
        sector_map = PerformanceAttribution._code_based_sector_map(instruments)
        self.assertEqual(sector_map["SH600000"], "SH_Main")
        self.assertEqual(sector_map["SZ300001"], "ChiNext")
        self.assertEqual(sector_map["SZ002001"], "SME")
        self.assertEqual(sector_map["SH688001"], "STAR")
        self.assertEqual(sector_map["SZ000001"], "SZ_Main")

    def test_monthly_decomposition_empty(self) -> None:
        result = PerformanceAttribution._monthly_decomposition(
            pd.Series(dtype=float), pd.Series(dtype=float),
        )
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
