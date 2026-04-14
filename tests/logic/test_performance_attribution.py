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

    def test_predictions_to_weights_clips_negatives(self) -> None:
        # Names with negative scores must not leak weight.
        idx = pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2025-10-01"), "SH600000"),
             (pd.Timestamp("2025-10-01"), "SH600001"),
             (pd.Timestamp("2025-10-01"), "SH600002")],
            names=["datetime", "instrument"],
        )
        predictions = pd.Series([1.0, 0.5, -2.0], index=idx)
        weights = PerformanceAttribution._predictions_to_weights(predictions)
        self.assertAlmostEqual(weights["SH600000"], 1.0 / 1.5)
        self.assertAlmostEqual(weights["SH600001"], 0.5 / 1.5)
        self.assertAlmostEqual(weights["SH600002"], 0.0)

    def test_predictions_to_weights_all_negative_falls_back_uniform(self) -> None:
        idx = pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2025-10-01"), "A"),
             (pd.Timestamp("2025-10-01"), "B")],
            names=["datetime", "instrument"],
        )
        predictions = pd.Series([-0.5, -1.0], index=idx)
        weights = PerformanceAttribution._predictions_to_weights(predictions)
        self.assertAlmostEqual(weights["A"], 0.5)
        self.assertAlmostEqual(weights["B"], 0.5)

    def test_positions_override_predictions_in_brinson(self) -> None:
        # When positions are supplied, Brinson weighting must come from
        # the real holdings, not the prediction scores.
        import pandas as pd

        idx = pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2025-10-01"), "SH600000"),
             (pd.Timestamp("2025-10-01"), "SZ300001")],
            names=["datetime", "instrument"],
        )
        # Predictions would give SH600000 almost all weight
        predictions = pd.Series([10.0, 0.01], index=idx)
        # But actual positions hold SZ300001 heavily
        positions = {
            "2025-10-01": {"SH600000": 0.1, "SZ300001": 0.9},
            "2025-10-02": {"SH600000": 0.1, "SZ300001": 0.9},
        }
        port_returns = pd.Series([0.01, -0.01],
                                 index=[pd.Timestamp("2025-10-01"), pd.Timestamp("2025-10-02")])
        bench_returns = pd.Series([0.005, 0.002],
                                  index=[pd.Timestamp("2025-10-01"), pd.Timestamp("2025-10-02")])
        cfg = AttributionConfig(start_date="2025-10-01", end_date="2025-10-02")

        # Mock _get_instrument_returns to avoid qlib dependency
        with patch.object(PerformanceAttribution, "_get_instrument_returns",
                          return_value=pd.Series({"SH600000": 0.05, "SZ300001": -0.03})):
            results = PerformanceAttribution._brinson_attribution(
                predictions, port_returns, bench_returns, cfg, positions,
            )

        # SH_Main sector (SH600000) should have portfolio_weight ≈ 0.1
        # ChiNext sector (SZ300001) should have portfolio_weight ≈ 0.9
        by_sector = {s.sector: s for s in results}
        self.assertIn("SH_Main", by_sector)
        self.assertIn("ChiNext", by_sector)
        self.assertAlmostEqual(by_sector["SH_Main"].portfolio_weight, 0.1, places=2)
        self.assertAlmostEqual(by_sector["ChiNext"].portfolio_weight, 0.9, places=2)


if __name__ == "__main__":
    unittest.main()
