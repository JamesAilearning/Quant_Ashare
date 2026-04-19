"""Tests for PerformanceAttribution — structural + E2E."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from src.core.performance_attribution import (
    ATTRIBUTION_METHOD_SINGLE_PERIOD,
    RECONCILIATION_WARN_THRESHOLD,
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

    def test_rejects_missing_bench_key(self) -> None:
        """'bench' is mandatory — no implicit empty-benchmark fallback."""
        with patch("src.core.performance_attribution.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(PerformanceAttributionError, "bench"):
                PerformanceAttribution.analyze(
                    return_series={"return": {}},
                    predictions=pd.Series(dtype=float),
                )

    def test_rejects_predictions_not_series(self) -> None:
        with patch("src.core.performance_attribution.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(PerformanceAttributionError, "pd.Series"):
                PerformanceAttribution.analyze(
                    return_series={"return": {}, "bench": {}},
                    predictions=[1.0, 2.0],
                )

    def test_rejects_predictions_flat_index(self) -> None:
        """predictions without a MultiIndex must be rejected."""
        flat = pd.Series([1.0, 2.0], index=["SH600000", "SZ000001"])
        with patch("src.core.performance_attribution.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(PerformanceAttributionError, "MultiIndex"):
                PerformanceAttribution.analyze(
                    return_series={"return": {}, "bench": {}},
                    predictions=flat,
                )

    def test_rejects_predictions_missing_instrument_level(self) -> None:
        """A MultiIndex without an 'instrument' level breaks downstream math."""
        idx = pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2025-10-01"), "X"), (pd.Timestamp("2025-10-02"), "Y")],
            names=["datetime", "ticker"],  # wrong level name
        )
        preds = pd.Series([1.0, 2.0], index=idx)
        with patch("src.core.performance_attribution.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(PerformanceAttributionError, "instrument"):
                PerformanceAttribution.analyze(
                    return_series={"return": {}, "bench": {}},
                    predictions=preds,
                )

    def test_rejects_empty_predictions(self) -> None:
        empty_idx = pd.MultiIndex.from_tuples([], names=["datetime", "instrument"])
        preds = pd.Series(dtype=float, index=empty_idx)
        with patch("src.core.performance_attribution.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(PerformanceAttributionError, "empty"):
                PerformanceAttribution.analyze(
                    return_series={"return": {}, "bench": {}},
                    predictions=preds,
                )

    def test_config_defaults(self) -> None:
        cfg = AttributionConfig()
        # benchmark_code and use_code_based_sectors removed as dead fields —
        # attribution uses return_series["bench"] from CanonicalBacktestOutput.
        self.assertIsInstance(cfg.start_date, str)
        self.assertIsInstance(cfg.end_date, str)

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


    # ------------------------------------------------------------------
    # P1.2: empty / corrupted positions — explicit error, not silent fallback
    # ------------------------------------------------------------------

    def test_rejects_empty_positions_dict(self) -> None:
        """Passing positions={} must raise, not silently fall back to predictions."""
        with patch("src.core.performance_attribution.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(PerformanceAttributionError, "empty dict"):
                PerformanceAttribution.analyze(
                    return_series={"return": {"2025-01-02": 0.01}, "bench": {}},
                    predictions=pd.Series(dtype=float),
                    positions={},
                )

    def test_none_positions_uses_predictions_fallback(self) -> None:
        """positions=None must explicitly allow the prediction-score fallback."""
        # We don't need a full run — just verify _validate doesn't raise.
        with patch("src.core.performance_attribution.is_canonical_qlib_initialized", return_value=True):
            # Should not raise — None is the explicit opt-in for predictions fallback.
            try:
                PerformanceAttribution._validate(
                    config=AttributionConfig(),
                    return_series={"return": {}, "bench": {}},
                    positions=None,
                )
            except PerformanceAttributionError:
                self.fail("_validate raised unexpectedly for positions=None")

    def test_rejects_positions_that_deserialize_to_all_zeros(self) -> None:
        """positions with all-zero weights must raise, not silently fallback."""
        import pandas as pd

        idx = pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2025-10-01"), "SH600000")],
            names=["datetime", "instrument"],
        )
        predictions = pd.Series([1.0], index=idx)
        # All weights are zero-like strings / None — deserialization yields nothing usable
        positions = {"2025-10-01": {}}  # non-empty outer dict, empty inner

        port_returns = pd.Series([0.01], index=[pd.Timestamp("2025-10-01")])
        bench_returns = pd.Series([0.005], index=[pd.Timestamp("2025-10-01")])
        cfg = AttributionConfig(start_date="2025-10-01", end_date="2025-10-01")

        with patch.object(PerformanceAttribution, "_get_instrument_returns",
                          return_value=pd.Series({"SH600000": 0.05})):
            with self.assertRaisesRegex(PerformanceAttributionError, "zero usable weights"):
                PerformanceAttribution._brinson_attribution(
                    predictions, port_returns, bench_returns, cfg, positions,
                )


class ReconciliationResidualTests(unittest.TestCase):
    """Regression guards for P1d: surface the gap between the Brinson
    single-period approximation and the compounded daily excess return.

    These two quantities are *not expected* to agree in general — the
    sector decomposition is a single-period model with time-averaged
    weights and point-to-point instrument returns, while
    ``total_excess_return`` is compounded from daily portfolio and
    benchmark returns. Previously the mismatch was invisible; callers
    who saw ``total_allocation_effect + total_selection_effect +
    total_interaction_effect`` diverge from ``total_excess_return``
    had no way to distinguish "path dependence" from "a bug somewhere".

    Now the result carries three explicit fields:
    - ``attribution_method``: label naming the decomposition model.
    - ``sector_effects_sum``: arithmetic sum of the three effects.
    - ``reconciliation_residual``: total_excess_return - sector_effects_sum.
    """

    def test_attribution_method_constant_set(self) -> None:
        """Constant must be a non-empty string so it can be displayed
        on dashboards and persisted into provenance."""
        self.assertIsInstance(ATTRIBUTION_METHOD_SINGLE_PERIOD, str)
        self.assertIn("single_period", ATTRIBUTION_METHOD_SINGLE_PERIOD)
        self.assertIn("brinson", ATTRIBUTION_METHOD_SINGLE_PERIOD.lower())

    def test_reconciliation_threshold_reasonable(self) -> None:
        """The WARN threshold must be small enough to catch genuine
        bugs but wide enough that normal path dependence doesn't spam."""
        self.assertGreater(RECONCILIATION_WARN_THRESHOLD, 0.0)
        self.assertLess(RECONCILIATION_WARN_THRESHOLD, 0.05)

    def test_result_carries_method_and_residual(self) -> None:
        """An ``AttributionResult`` constructed with the new fields
        must expose them with the expected semantics."""
        result = AttributionResult(
            sector_attribution=(),
            total_allocation_effect=0.01,
            total_selection_effect=0.02,
            total_interaction_effect=-0.005,
            monthly_returns=(),
            total_portfolio_return=0.10,
            total_benchmark_return=0.05,
            total_excess_return=0.05,
            attribution_method=ATTRIBUTION_METHOD_SINGLE_PERIOD,
            sector_effects_sum=0.025,
            reconciliation_residual=0.025,  # 0.05 excess - 0.025 sum
        )
        self.assertEqual(result.attribution_method, ATTRIBUTION_METHOD_SINGLE_PERIOD)
        self.assertAlmostEqual(result.sector_effects_sum, 0.025)
        self.assertAlmostEqual(result.reconciliation_residual, 0.025)

    def test_print_report_warns_on_large_residual(self) -> None:
        """When ``|reconciliation_residual|`` exceeds the threshold,
        ``print_report`` must emit a WARNING — the gap must not be
        displayed as though the decomposition were exact."""
        from unittest.mock import patch as _patch

        result = AttributionResult(
            sector_attribution=(),
            total_allocation_effect=0.0,
            total_selection_effect=0.0,
            total_interaction_effect=0.0,
            monthly_returns=(),
            total_portfolio_return=0.10,
            total_benchmark_return=0.0,
            total_excess_return=0.10,
            attribution_method=ATTRIBUTION_METHOD_SINGLE_PERIOD,
            sector_effects_sum=0.0,
            reconciliation_residual=0.10,  # well above threshold
        )
        with _patch("src.core.performance_attribution._logger") as mock_logger:
            PerformanceAttribution.print_report(result)
        mock_logger.warning.assert_called_once()
        msg = mock_logger.warning.call_args[0][0]
        self.assertIn("reconciliation residual", msg.lower())

    def test_print_report_no_warn_on_small_residual(self) -> None:
        """Within-threshold residuals are expected for path-dependent
        portfolios — no WARNING needed; INFO is enough."""
        from unittest.mock import patch as _patch

        tiny = RECONCILIATION_WARN_THRESHOLD / 10.0
        result = AttributionResult(
            sector_attribution=(),
            total_allocation_effect=0.0,
            total_selection_effect=0.0,
            total_interaction_effect=0.0,
            monthly_returns=(),
            total_portfolio_return=tiny,
            total_benchmark_return=0.0,
            total_excess_return=tiny,
            attribution_method=ATTRIBUTION_METHOD_SINGLE_PERIOD,
            sector_effects_sum=0.0,
            reconciliation_residual=tiny,
        )
        with _patch("src.core.performance_attribution._logger") as mock_logger:
            PerformanceAttribution.print_report(result)
        mock_logger.warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
