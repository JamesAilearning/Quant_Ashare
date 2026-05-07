"""Tests for PerformanceAttribution — structural + E2E."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from src.core.board_heuristic import (
    BOARD_CHINEXT,
    BOARD_HEURISTIC_TAXONOMY_ID,
    BOARD_SH_MAIN,
)
from src.core.performance_attribution import (
    ATTRIBUTION_METHOD_SINGLE_PERIOD,
    BENCH_WEIGHT_METHOD_EQUAL,
    BENCH_WEIGHT_METHOD_EQUAL_PROXY,
    BENCH_WEIGHT_METHOD_EXPLICIT,
    BENCH_WEIGHT_METHOD_MARKET_CAP,
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
                    return_series={"return": {"2025-10-01": 0.01}, "bench": {"2025-10-01": 0.005}},
                    predictions=[1.0, 2.0],
                )

    def test_rejects_predictions_flat_index(self) -> None:
        """predictions without a MultiIndex must be rejected."""
        flat = pd.Series([1.0, 2.0], index=["SH600000", "SZ000001"])
        with patch("src.core.performance_attribution.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(PerformanceAttributionError, "MultiIndex"):
                PerformanceAttribution.analyze(
                    return_series={"return": {"2025-10-01": 0.01}, "bench": {"2025-10-01": 0.005}},
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
                    return_series={"return": {"2025-10-01": 0.01}, "bench": {"2025-10-01": 0.005}},
                    predictions=preds,
                )

    def test_rejects_empty_predictions(self) -> None:
        empty_idx = pd.MultiIndex.from_tuples([], names=["datetime", "instrument"])
        preds = pd.Series(dtype=float, index=empty_idx)
        with patch("src.core.performance_attribution.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(PerformanceAttributionError, "empty"):
                PerformanceAttribution.analyze(
                    return_series={"return": {"2025-10-01": 0.01}, "bench": {"2025-10-01": 0.005}},
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
            sector=BOARD_SH_MAIN, portfolio_weight=0.5, benchmark_weight=0.3,
            portfolio_return=0.1, benchmark_return=0.05,
            allocation_effect=0.01, selection_effect=0.02,
            interaction_effect=0.005, total_effect=0.035,
        )
        self.assertEqual(sa.sector, BOARD_SH_MAIN)
        self.assertAlmostEqual(sa.total_effect, 0.035)

    def test_monthly_return_dataclass(self) -> None:
        mr = MonthlyReturn(year=2025, month=7, portfolio_return=0.03,
                           benchmark_return=0.01, excess_return=0.02)
        self.assertEqual(mr.year, 2025)
        self.assertAlmostEqual(mr.excess_return, 0.02)

    def test_attribution_result_default_sector_taxonomy(self) -> None:
        """Default ``sector_taxonomy`` must point at the board heuristic.

        This is the contract that lets a downstream consumer of an
        ``AttributionResult`` filter or flag results that came from the
        coarse code-prefix bucketing (vs. a real industry taxonomy).
        Previously there was no way to tell — sector labels like
        ``"SH_Main"`` could be misread as industries.
        """
        result = AttributionResult(
            sector_attribution=(),
            total_allocation_effect=0.0,
            total_selection_effect=0.0,
            total_interaction_effect=0.0,
            monthly_returns=(),
            total_portfolio_return=0.0,
            total_benchmark_return=0.0,
            total_excess_return=0.0,
        )
        self.assertEqual(result.sector_taxonomy, BOARD_HEURISTIC_TAXONOMY_ID)

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

    def test_predictions_to_weights_all_non_positive_raises(self) -> None:
        """All per-instrument averaged scores are non-positive → raise.

        Previously this case silently fell back to a uniform ``1/n``
        weighting, which disguised a model-failure signal ("no long
        signal at all") as a valid equal-weight attribution run.
        The new contract surfaces the failure so the pipeline can
        downgrade visibly (skip attribution + WARNING) instead of
        publishing meaningless sector effects.
        """
        idx = pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2025-10-01"), "A"),
             (pd.Timestamp("2025-10-01"), "B")],
            names=["datetime", "instrument"],
        )
        predictions = pd.Series([-0.5, -1.0], index=idx)
        with self.assertRaisesRegex(
            PerformanceAttributionError, "non-positive"
        ):
            PerformanceAttribution._predictions_to_weights(predictions)

    def test_predictions_to_weights_all_zero_raises(self) -> None:
        """Zero is non-positive too — ``clipped.sum() == 0`` must raise."""
        idx = pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2025-10-01"), "A"),
             (pd.Timestamp("2025-10-01"), "B")],
            names=["datetime", "instrument"],
        )
        predictions = pd.Series([0.0, 0.0], index=idx)
        with self.assertRaises(PerformanceAttributionError):
            PerformanceAttribution._predictions_to_weights(predictions)

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

        # board_SH_Main (SH600000) should have portfolio_weight ≈ 0.1
        # board_ChiNext (SZ300001) should have portfolio_weight ≈ 0.9
        by_sector = {s.sector: s for s in results}
        self.assertIn(BOARD_SH_MAIN, by_sector)
        self.assertIn(BOARD_CHINEXT, by_sector)
        self.assertAlmostEqual(by_sector[BOARD_SH_MAIN].portfolio_weight, 0.1, places=2)
        self.assertAlmostEqual(by_sector[BOARD_CHINEXT].portfolio_weight, 0.9, places=2)


    # ------------------------------------------------------------------
    # P1.2: empty / corrupted positions — explicit error, not silent fallback
    # ------------------------------------------------------------------

    def test_rejects_empty_positions_dict(self) -> None:
        """Passing positions={} must raise, not silently fall back to predictions."""
        with patch("src.core.performance_attribution.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(PerformanceAttributionError, "empty dict"):
                PerformanceAttribution.analyze(
                    return_series={"return": {"2025-01-02": 0.01}, "bench": {"2025-01-02": 0.005}},
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
                    return_series={"return": {"2025-10-01": 0.01}, "bench": {"2025-10-01": 0.005}},
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

    def test_rejects_positions_with_explicit_zero_weight(self) -> None:
        """Inner map non-empty but every weight is 0.0 must also raise.

        Regression for P1: ``{"2025-10-01": {"SH600000": 0.0}}`` used to
        slip past the previous "empty inner map" guard because the dict
        is truthy, then ``total = 0`` flowed through the
        ``port_weights = raw / total if total > 0 else raw`` else-branch
        as a literal all-zero Series. The Brinson math then produced a
        "valid-looking" zero-allocation/zero-selection attribution from
        garbage. The new guard checks ``total <= 0`` after time-averaging.
        """
        import pandas as pd

        idx = pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2025-10-01"), "SH600000")],
            names=["datetime", "instrument"],
        )
        predictions = pd.Series([1.0], index=idx)
        positions = {"2025-10-01": {"SH600000": 0.0}}  # explicit zero weight

        port_returns = pd.Series([0.01], index=[pd.Timestamp("2025-10-01")])
        bench_returns = pd.Series([0.005], index=[pd.Timestamp("2025-10-01")])
        cfg = AttributionConfig(start_date="2025-10-01", end_date="2025-10-01")

        with patch.object(PerformanceAttribution, "_get_instrument_returns",
                          return_value=pd.Series({"SH600000": 0.05})):
            with self.assertRaisesRegex(PerformanceAttributionError, "non-positive aggregate weight"):
                PerformanceAttribution._brinson_attribution(
                    predictions, port_returns, bench_returns, cfg, positions,
                )

    def test_rejects_empty_return_mapping(self) -> None:
        """``return_series['return']`` empty must raise — collapses every
        effect to zero otherwise."""
        with patch("src.core.performance_attribution.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(
                PerformanceAttributionError, "'return'.*non-empty mapping"
            ):
                PerformanceAttribution.analyze(
                    return_series={"return": {}, "bench": {"2025-10-01": 0.005}},
                    predictions=pd.Series(dtype=float),
                )

    def test_rejects_empty_bench_mapping(self) -> None:
        """``return_series['bench']`` empty must raise — would silently
        coerce total_benchmark_return to 0.0 and turn 'excess' into
        'portfolio', which is a label-mismatch trap."""
        with patch("src.core.performance_attribution.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(
                PerformanceAttributionError, "'bench'.*non-empty mapping"
            ):
                PerformanceAttribution.analyze(
                    return_series={"return": {"2025-10-01": 0.01}, "bench": {}},
                    predictions=pd.Series(dtype=float),
                )

    def test_rejects_pandas_series_value_without_truthy_ambiguity(self) -> None:
        """When ``return_series['return']`` is a pandas Series, the old
        ``not value`` check would raise ``ValueError("truth value …
        ambiguous")`` from pandas — surfacing the wrong exception type
        and burying the real problem ("expected a Mapping").

        The new ``isinstance(Mapping) + len()`` check rejects a Series
        with a clean ``PerformanceAttributionError`` naming the type.
        """
        ser = pd.Series([0.01, 0.02], index=["2025-10-01", "2025-10-02"])
        with patch("src.core.performance_attribution.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(
                PerformanceAttributionError, "non-empty mapping.*Series"
            ):
                PerformanceAttribution.analyze(
                    return_series={"return": ser, "bench": {"2025-10-01": 0.005}},
                    predictions=pd.Series(dtype=float),
                )

    def test_rejects_non_mapping_return_value(self) -> None:
        """A plain list also fails the ``isinstance(Mapping)`` test
        with our error, not a downstream KeyError."""
        with patch("src.core.performance_attribution.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(
                PerformanceAttributionError, "non-empty mapping.*list"
            ):
                PerformanceAttribution.analyze(
                    return_series={"return": [0.01], "bench": {"2025-10-01": 0.005}},
                    predictions=pd.Series(dtype=float),
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


class BenchWeightMethodTests(unittest.TestCase):
    """Regression guards for Y2: the benchmark-weighting method is now an
    explicit, validated config field instead of a hardcoded equal-weight
    choice buried inside ``_brinson_attribution``.

    The old behaviour was: every Brinson run used an equal-weight benchmark
    across the predictions universe. That is *not* the same as CSI 300's
    free-float-cap weighting — and there was no field on the result to
    say so, so consumers had no way to tell whether allocation effects
    were comparable to the published index.

    The new contract is:
    - default ``bench_weight_method='equal'`` (matches old behaviour, but
      now visible in config and result + flagged in print_report).
    - ``'market_cap'`` is reserved and raises "not yet supported".
    - any other value raises with the supported list enumerated.
    """

    def test_default_is_equal_weight(self) -> None:
        cfg = AttributionConfig()
        self.assertEqual(cfg.bench_weight_method, BENCH_WEIGHT_METHOD_EQUAL_PROXY)

    def test_validate_accepts_equal_proxy_and_legacy_equal(self) -> None:
        with patch("src.core.performance_attribution.is_canonical_qlib_initialized", return_value=True):
            for method in (BENCH_WEIGHT_METHOD_EQUAL_PROXY, BENCH_WEIGHT_METHOD_EQUAL):
                PerformanceAttribution._validate(
                    config=AttributionConfig(bench_weight_method=method),
                    return_series={"return": {"2025-10-01": 0.01}, "bench": {"2025-10-01": 0.005}},
                    positions=None,
                )

    def test_legacy_equal_normalizes_to_proxy_result_label(self) -> None:
        cfg = AttributionConfig(bench_weight_method=BENCH_WEIGHT_METHOD_EQUAL)
        self.assertEqual(
            PerformanceAttribution._effective_bench_weight_method(cfg),
            BENCH_WEIGHT_METHOD_EQUAL_PROXY,
        )

    def test_validate_rejects_market_cap_without_weights(self) -> None:
        """``'market_cap'`` is reserved but not implemented — must raise.

        This protects against the misnomer trap: if validation silently
        accepted ``'market_cap'`` while the engine still computed equal
        weights, the result would be published under the wrong
        ``bench_weight_method`` label.
        """
        with patch("src.core.performance_attribution.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(
                PerformanceAttributionError, "requires.*benchmark_weights"
            ):
                PerformanceAttribution._validate(
                    config=AttributionConfig(bench_weight_method=BENCH_WEIGHT_METHOD_MARKET_CAP),
                    return_series={"return": {"2025-10-01": 0.01}, "bench": {"2025-10-01": 0.005}},
                    positions=None,
                )

    def test_validate_rejects_explicit_without_weights(self) -> None:
        with patch("src.core.performance_attribution.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(
                PerformanceAttributionError, "requires.*benchmark_weights"
            ):
                PerformanceAttribution._validate(
                    config=AttributionConfig(bench_weight_method=BENCH_WEIGHT_METHOD_EXPLICIT),
                    return_series={"return": {"2025-10-01": 0.01}, "bench": {"2025-10-01": 0.005}},
                    positions=None,
                )

    def test_validate_rejects_unknown_method(self) -> None:
        with patch("src.core.performance_attribution.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(
                PerformanceAttributionError, "bench_weight_method"
            ):
                PerformanceAttribution._validate(
                    config=AttributionConfig(bench_weight_method="garbage"),
                    return_series={"return": {"2025-10-01": 0.01}, "bench": {"2025-10-01": 0.005}},
                    positions=None,
                )

    def test_explicit_weights_flow_into_sector_benchmark_weights(self) -> None:
        idx = pd.MultiIndex.from_tuples(
            [
                (pd.Timestamp("2025-10-01"), "SH600000"),
                (pd.Timestamp("2025-10-01"), "SZ300001"),
            ],
            names=["datetime", "instrument"],
        )
        predictions = pd.Series([0.5, 0.5], index=idx)
        positions = {"2025-10-01": {"SH600000": 0.5, "SZ300001": 0.5}}
        port_returns = pd.Series([0.01], index=[pd.Timestamp("2025-10-01")])
        bench_returns = pd.Series([0.005], index=[pd.Timestamp("2025-10-01")])
        cfg = AttributionConfig(
            start_date="2025-10-01",
            end_date="2025-10-01",
            bench_weight_method=BENCH_WEIGHT_METHOD_EXPLICIT,
            benchmark_weights={"SH600000": 0.8, "SZ300001": 0.2},
        )

        with patch.object(
            PerformanceAttribution,
            "_get_instrument_returns",
            return_value=pd.Series({"SH600000": 0.01, "SZ300001": 0.02}),
        ):
            results = PerformanceAttribution._brinson_attribution(
                predictions, port_returns, bench_returns, cfg, positions,
            )

        by_sector = {s.sector: s for s in results}
        self.assertAlmostEqual(by_sector[BOARD_SH_MAIN].benchmark_weight, 0.8)
        self.assertAlmostEqual(by_sector[BOARD_CHINEXT].benchmark_weight, 0.2)
        self.assertEqual(
            PerformanceAttribution._effective_bench_weight_method(cfg),
            BENCH_WEIGHT_METHOD_EXPLICIT,
        )

    def test_explicit_weights_require_positive_overlap(self) -> None:
        with self.assertRaisesRegex(PerformanceAttributionError, "positive overlap"):
            PerformanceAttribution._resolve_benchmark_weights(
                ["SH600000"],
                AttributionConfig(benchmark_weights={"SZ300001": 1.0}),
            )

    def test_result_carries_bench_weight_method(self) -> None:
        """The chosen method is exposed on the result so consumers
        don't need to look at the config object to know what
        weighting produced the effects."""
        result = AttributionResult(
            sector_attribution=(),
            total_allocation_effect=0.0,
            total_selection_effect=0.0,
            total_interaction_effect=0.0,
            monthly_returns=(),
            total_portfolio_return=0.0,
            total_benchmark_return=0.0,
            total_excess_return=0.0,
            bench_weight_method=BENCH_WEIGHT_METHOD_EXPLICIT,
        )
        self.assertEqual(result.bench_weight_method, BENCH_WEIGHT_METHOD_EXPLICIT)

    def test_print_report_flags_equal_weight_caveat(self) -> None:
        """``print_report`` must explicitly note that equal-weight
        benchmark != real index weighting. Without this line readers
        could misread the allocation effects as exact contributions
        vs. CSI 300's actual cap-weighted composition."""
        from unittest.mock import patch as _patch

        result = AttributionResult(
            sector_attribution=(),
            total_allocation_effect=0.0,
            total_selection_effect=0.0,
            total_interaction_effect=0.0,
            monthly_returns=(),
            total_portfolio_return=0.0,
            total_benchmark_return=0.0,
            total_excess_return=0.0,
            bench_weight_method=BENCH_WEIGHT_METHOD_EQUAL_PROXY,
        )
        with _patch("src.core.performance_attribution._logger") as mock_logger:
            PerformanceAttribution.print_report(result)
        # Walk every info call looking for the caveat marker. Using a
        # phrase the engine prints unmistakably so a future cosmetic edit
        # has to deliberately re-state the caveat to keep the test green.
        info_calls = [c for c in mock_logger.info.call_args_list]
        joined = " ".join(str(c) for c in info_calls)
        self.assertIn("equal-weight benchmark", joined)
        self.assertIn("free-float-cap", joined)


class IndustryMapOverrideTests(unittest.TestCase):
    """``AttributionConfig.industry_map_override`` lets a caller swap
    the default board-heuristic sector map for a real industry
    classification (Tushare Shenwan L2 in v1).

    Two failure modes the validation must close:

    1. Override map set without ``industry_taxonomy_id`` — the result
       would be stamped with the board-heuristic id while actually
       computed from the override map. Label mismatch trap.
    2. ``industry_taxonomy_id`` set without override — the result
       would be stamped with a real-industry id while actually
       computed from the board heuristic. Same trap, mirror image.
    """

    def test_default_uses_board_heuristic(self) -> None:
        """No override → ``_build_sector_map`` returns board buckets,
        and the result carries ``BOARD_HEURISTIC_TAXONOMY_ID``."""
        cfg = AttributionConfig()
        sector_map = PerformanceAttribution._build_sector_map(
            ["SH600000", "SZ300001"], cfg,
        )
        # SH600000 → board_SH_Main, SZ300001 → board_ChiNext
        self.assertEqual(sector_map["SH600000"], BOARD_SH_MAIN)
        self.assertEqual(sector_map["SZ300001"], BOARD_CHINEXT)

    def test_override_used_verbatim(self) -> None:
        cfg = AttributionConfig(
            industry_map_override={
                "SH600000": "银行",
                "SZ300001": "电子",
            },
            industry_taxonomy_id="tushare_sw_l2",
        )
        sector_map = PerformanceAttribution._build_sector_map(
            ["SH600000", "SZ300001"], cfg,
        )
        self.assertEqual(sector_map, {
            "SH600000": "银行",
            "SZ300001": "电子",
        })

    def test_instrument_missing_from_override_falls_back_to_unknown(self) -> None:
        """Missing instruments must NOT mix in the board heuristic —
        a Brinson run mixing two taxonomies would be uninterpretable.
        ``unknown`` makes the gap explicit."""
        cfg = AttributionConfig(
            industry_map_override={"SH600000": "银行"},
            industry_taxonomy_id="tushare_sw_l2",
        )
        sector_map = PerformanceAttribution._build_sector_map(
            ["SH600000", "SZ300001", "SH688001"], cfg,
        )
        self.assertEqual(sector_map["SH600000"], "银行")
        self.assertEqual(sector_map["SZ300001"], "unknown")
        self.assertEqual(sector_map["SH688001"], "unknown")

    def _validate_with_qlib_init(self, **kwargs):
        cfg = AttributionConfig(**kwargs)
        with patch("src.core.performance_attribution.is_canonical_qlib_initialized", return_value=True):
            PerformanceAttribution._validate(
                config=cfg,
                return_series={"return": {"d": 0.01}, "bench": {"d": 0.005}},
                positions=None,
            )

    def test_rejects_override_without_taxonomy_id(self) -> None:
        with self.assertRaisesRegex(
            PerformanceAttributionError, "industry_taxonomy_id must be"
        ):
            self._validate_with_qlib_init(
                industry_map_override={"SH600000": "银行"},
                industry_taxonomy_id="",
            )

    def test_rejects_taxonomy_id_without_override(self) -> None:
        with self.assertRaisesRegex(
            PerformanceAttributionError,
            "industry_taxonomy_id is set but industry_map_override is None",
        ):
            self._validate_with_qlib_init(
                industry_map_override=None,
                industry_taxonomy_id="tushare_sw_l2",
            )

    def test_rejects_empty_override_mapping(self) -> None:
        """Empty override is a caller error, not 'use the heuristic
        instead'. Pass ``None`` to opt into the default explicitly."""
        with self.assertRaisesRegex(
            PerformanceAttributionError, "empty mapping"
        ):
            self._validate_with_qlib_init(
                industry_map_override={},
                industry_taxonomy_id="tushare_sw_l2",
            )

    def test_result_carries_override_taxonomy_id(self) -> None:
        """End-to-end via ``analyze``: when override is set, the
        result's ``sector_taxonomy`` must be the caller-supplied id,
        not the board-heuristic constant."""
        idx = pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2025-10-01"), "SH600000"),
             (pd.Timestamp("2025-10-01"), "SH601398")],
            names=["datetime", "instrument"],
        )
        predictions = pd.Series([0.5, 0.5], index=idx)
        cfg = AttributionConfig(
            start_date="2025-10-01", end_date="2025-10-01",
            industry_map_override={
                "SH600000": "银行",
                "SH601398": "银行",
            },
            industry_taxonomy_id="tushare_sw_l2",
        )
        return_series = {
            "return": {"2025-10-01": 0.01},
            "bench": {"2025-10-01": 0.005},
        }
        with patch(
            "src.core.performance_attribution.is_canonical_qlib_initialized",
            return_value=True,
        ), patch.object(
            PerformanceAttribution, "_get_instrument_returns",
            return_value=pd.Series({"SH600000": 0.01, "SH601398": 0.02}),
        ):
            result = PerformanceAttribution.analyze(
                return_series=return_series,
                predictions=predictions,
                config=cfg,
            )
        self.assertEqual(result.sector_taxonomy, "tushare_sw_l2")


if __name__ == "__main__":
    unittest.main()
