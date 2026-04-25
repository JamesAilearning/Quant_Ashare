"""Tests for src.core.walk_forward — walk-forward rolling backtest engine."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.core.canonical_backtest_contract import ADJUST_MODE_NONE, EXECUTION_PRICE_OPEN

from src.core.walk_forward import (
    WalkForwardConfig,
    WalkForwardEngine,
    WalkForwardError,
)


class WalkForwardValidationTests(unittest.TestCase):
    """Unit tests that do NOT require qlib."""

    def test_rejects_when_qlib_not_initialized(self):
        with patch("src.core.walk_forward.is_canonical_qlib_initialized", return_value=False):
            with self.assertRaises(WalkForwardError):
                WalkForwardEngine.run(WalkForwardConfig())

    def test_window_generation(self):
        config = WalkForwardConfig(
            overall_start="2022-01-01",
            overall_end="2025-12-31",
            train_months=24,
            valid_months=3,
            test_months=3,
            step_months=3,
        )
        windows = WalkForwardEngine._generate_windows(config)
        # First fold: train 2022-01-01~2023-12-31, valid 2024-01-01~2024-03-31, test 2024-04-01~2024-06-30
        self.assertGreater(len(windows), 0)
        first = windows[0]
        self.assertEqual(first[0], "2022-01-01")  # train_start
        self.assertEqual(first[1], "2023-12-31")  # train_end
        self.assertEqual(first[2], "2024-01-01")  # valid_start
        self.assertEqual(first[3], "2024-03-31")  # valid_end
        self.assertEqual(first[4], "2024-04-01")  # test_start
        self.assertEqual(first[5], "2024-06-30")  # test_end

    def test_window_generation_too_short(self):
        config = WalkForwardConfig(
            overall_start="2024-01-01",
            overall_end="2024-06-30",  # too short for 24m train + 3m valid + 3m test
            train_months=24,
            valid_months=3,
            test_months=3,
        )
        windows = WalkForwardEngine._generate_windows(config)
        self.assertEqual(len(windows), 0)

    def test_default_config_values(self):
        cfg = WalkForwardConfig()
        self.assertEqual(cfg.train_months, 24)
        self.assertEqual(cfg.step_months, 3)
        self.assertEqual(cfg.model_type, "LGBModel")
        self.assertEqual(cfg.adjust_mode, "pre_adjusted")
        self.assertEqual(cfg.signal_to_execution_lag, 1)
        self.assertEqual(cfg.execution_price_kind, "close")

    def test_multiple_folds_generated(self):
        config = WalkForwardConfig(
            overall_start="2020-01-01",
            overall_end="2025-12-31",
            train_months=24,
            valid_months=3,
            test_months=3,
            step_months=3,
        )
        windows = WalkForwardEngine._generate_windows(config)
        # Should have multiple folds
        self.assertGreater(len(windows), 3)
        # Non-overlapping test periods
        for i in range(1, len(windows)):
            prev_test_end = windows[i-1][5]
            curr_test_start = windows[i][4]
            self.assertLessEqual(prev_test_end, curr_test_start)


class WalkForwardConfigValidationTests(unittest.TestCase):
    """Regression guard for P1b: zero-length windows would hang the engine.

    ``step_months=0`` used to put ``_generate_windows`` into an infinite
    loop; ``train_months=0`` would silently make folds with no fit data.
    """

    def test_rejects_zero_step_months(self):
        with self.assertRaisesRegex(WalkForwardError, "step_months"):
            WalkForwardConfig(step_months=0)

    def test_rejects_zero_train_months(self):
        with self.assertRaisesRegex(WalkForwardError, "train_months"):
            WalkForwardConfig(train_months=0)

    def test_rejects_zero_valid_months(self):
        with self.assertRaisesRegex(WalkForwardError, "valid_months"):
            WalkForwardConfig(valid_months=0)

    def test_rejects_zero_test_months(self):
        with self.assertRaisesRegex(WalkForwardError, "test_months"):
            WalkForwardConfig(test_months=0)

    def test_rejects_negative_months(self):
        with self.assertRaisesRegex(WalkForwardError, "step_months"):
            WalkForwardConfig(step_months=-1)

    def test_rejects_non_int_months(self):
        """Also catches bool → would silently act as 1."""
        with self.assertRaisesRegex(WalkForwardError, "step_months must be int"):
            WalkForwardConfig(step_months=True)
        with self.assertRaisesRegex(WalkForwardError, "train_months must be int"):
            WalkForwardConfig(train_months=24.0)

    def test_rejects_bad_overall_start(self):
        with self.assertRaisesRegex(WalkForwardError, "overall_start"):
            WalkForwardConfig(overall_start="not-a-date")

    def test_rejects_bad_overall_end(self):
        with self.assertRaisesRegex(WalkForwardError, "overall_end"):
            WalkForwardConfig(overall_end="2024/01/01")

    def test_rejects_end_before_start(self):
        with self.assertRaisesRegex(WalkForwardError, "must be strictly after"):
            WalkForwardConfig(
                overall_start="2025-01-01", overall_end="2024-12-31",
            )

    def test_rejects_end_equal_start(self):
        with self.assertRaisesRegex(WalkForwardError, "must be strictly after"):
            WalkForwardConfig(
                overall_start="2025-01-01", overall_end="2025-01-01",
            )

    def test_rejects_n_drop_gte_topk(self):
        """n_drop must leave some positions after a rebalance."""
        with self.assertRaisesRegex(WalkForwardError, "n_drop"):
            WalkForwardConfig(topk=10, n_drop=10)
        with self.assertRaisesRegex(WalkForwardError, "n_drop"):
            WalkForwardConfig(topk=10, n_drop=15)

    def test_rejects_non_positive_topk(self):
        with self.assertRaisesRegex(WalkForwardError, "topk"):
            WalkForwardConfig(topk=0)
        with self.assertRaisesRegex(WalkForwardError, "topk"):
            WalkForwardConfig(topk=-5)

    def test_rejects_negative_n_drop(self):
        with self.assertRaisesRegex(WalkForwardError, "n_drop"):
            WalkForwardConfig(n_drop=-1)

    def test_rejects_zero_signal_to_execution_lag(self):
        with self.assertRaisesRegex(WalkForwardError, "signal_to_execution_lag"):
            WalkForwardConfig(signal_to_execution_lag=0)

    def test_rejects_unknown_adjust_mode(self):
        with self.assertRaisesRegex(WalkForwardError, "adjust_mode"):
            WalkForwardConfig(adjust_mode="auto")

    def test_rejects_unknown_execution_price_kind(self):
        with self.assertRaisesRegex(WalkForwardError, "execution_price_kind"):
            WalkForwardConfig(execution_price_kind="limit")

    def test_rejects_negative_min_cost(self):
        with self.assertRaisesRegex(WalkForwardError, "min_cost"):
            WalkForwardConfig(min_cost=-1.0)

    def test_rejects_invalid_limit_threshold(self):
        with self.assertRaisesRegex(WalkForwardError, "limit_threshold"):
            WalkForwardConfig(limit_threshold=0.0)


class WalkForwardBacktestPassthroughTests(unittest.TestCase):
    def test_fold_backtest_request_uses_configured_controls(self) -> None:
        config = WalkForwardConfig(
            execution_price_kind=EXECUTION_PRICE_OPEN,
            adjust_mode=ADJUST_MODE_NONE,
            signal_to_execution_lag=3,
            min_cost=7.5,
            limit_threshold=0.195,
            commission_rate=0.0007,
            stamp_tax_bps=8.0,
            slippage_bps=3.0,
        )

        fake_feature_result = MagicMock()
        fake_feature_result.dataset = object()
        fake_model_result = MagicMock()
        fake_model_result.predictions = "predictions"
        fake_model_result.prediction_shape = (10,)
        fake_signal_result = MagicMock()
        fake_signal_result.ic_summary = {
            1: {"mean_ic": 0.01},
            5: {"mean_ic": 0.02},
        }
        fake_backtest_output = MagicMock()
        fake_backtest_output.risk_analysis = {
            "excess_return_with_cost": {
                "annualized_return": 0.11,
                "max_drawdown": -0.08,
                "information_ratio": 1.2,
            }
        }

        with patch(
            "src.core.walk_forward.FeatureDatasetBuilder.build",
            return_value=fake_feature_result,
        ), patch(
            "src.core.walk_forward.ModelTrainer.train_and_predict",
            return_value=fake_model_result,
        ), patch(
            "src.core.walk_forward.SignalAnalyzer.analyze",
            return_value=fake_signal_result,
        ), patch(
            "src.core.walk_forward.BacktestRunner.run",
            return_value=fake_backtest_output,
        ) as mock_backtest:
            WalkForwardEngine._run_single_fold(
                config=config,
                fold_index=0,
                train_start="2024-01-01",
                train_end="2024-06-30",
                valid_start="2024-07-01",
                valid_end="2024-09-30",
                test_start="2024-10-01",
                test_end="2024-12-31",
                output_dir=Path("D:/tmp/walk_forward_test"),
            )

        request = mock_backtest.call_args.kwargs["request"]
        self.assertEqual(request.adjust_mode, ADJUST_MODE_NONE)
        self.assertEqual(request.signal_to_execution_lag, 3)
        self.assertEqual(request.exchange_config.execution_price_kind, EXECUTION_PRICE_OPEN)
        self.assertEqual(request.exchange_config.limit_threshold, 0.195)
        self.assertEqual(request.exchange_config.cost_model.min_cost, 7.5)
        self.assertEqual(request.exchange_config.cost_model.commission_rate, 0.0007)


class ComputeAggregateNaNSafetyTests(unittest.TestCase):
    """Regression guards for P1c: ``_compute_aggregate`` must tolerate
    folds whose metrics are NaN (e.g. a fold whose validation period was
    too short for SignalAnalyzer to produce a valid IC — surfaced as
    NaN after P2c in batch 6 rather than a silent 0.0).

    Old code used ``np.mean``/``np.std``/``np.min``; a single NaN fold
    would poison every aggregate into NaN, making a 4/5 healthy study
    look identical to a 0/5 broken study.
    """

    def _fold(self, idx, ic_1d, ic_5d, ann_ret, max_dd, ir):
        from src.core.walk_forward import WalkForwardFold
        return WalkForwardFold(
            fold_index=idx,
            train_period="2024-01-01 ~ 2024-06-30",
            valid_period="2024-07-01 ~ 2024-09-30",
            test_period="2024-10-01 ~ 2024-12-31",
            ic_1d=ic_1d,
            ic_5d=ic_5d,
            annualized_return=ann_ret,
            max_drawdown=max_dd,
            information_ratio=ir,
            prediction_shape=(1000,),
        )

    def test_single_nan_fold_does_not_poison_aggregate(self) -> None:
        import math
        folds = [
            self._fold(0, 0.04, 0.05, 0.12, -0.08, 1.1),
            self._fold(1, math.nan, math.nan, 0.09, -0.10, 0.9),
            self._fold(2, 0.02, 0.03, 0.07, -0.05, 0.6),
        ]
        agg = WalkForwardEngine._compute_aggregate(folds)
        # Means must be finite — NaN fold excluded.
        self.assertFalse(math.isnan(agg["mean_ic_1d"]))
        self.assertFalse(math.isnan(agg["mean_ic_5d"]))
        self.assertFalse(math.isnan(agg["mean_annualized_return"]))
        # Mean of 0.04 and 0.02 is 0.03.
        self.assertAlmostEqual(agg["mean_ic_1d"], 0.03, places=4)

    def test_valid_fold_counts_surface_nan_folds(self) -> None:
        """The output must disclose how many folds fed each mean — a
        count of 2 in a 3-fold study means 1 fold came back as NaN."""
        import math
        folds = [
            self._fold(0, 0.04, 0.05, 0.12, -0.08, 1.1),
            self._fold(1, math.nan, 0.03, 0.09, -0.10, 0.9),
            self._fold(2, 0.02, math.nan, 0.07, -0.05, math.nan),
        ]
        agg = WalkForwardEngine._compute_aggregate(folds)
        self.assertEqual(agg["valid_folds_ic_1d"], 2.0)
        self.assertEqual(agg["valid_folds_ic_5d"], 2.0)
        self.assertEqual(agg["valid_folds_annualized_return"], 3.0)
        self.assertEqual(agg["valid_folds_information_ratio"], 2.0)
        self.assertEqual(agg["num_folds"], 3.0)

    def test_all_nan_metric_returns_nan(self) -> None:
        """If *every* fold is NaN for a metric, the mean must propagate
        as NaN — a loud signal, not a silent zero."""
        import math
        folds = [
            self._fold(0, math.nan, 0.05, 0.12, -0.08, 1.1),
            self._fold(1, math.nan, 0.03, 0.09, -0.10, 0.9),
        ]
        agg = WalkForwardEngine._compute_aggregate(folds)
        self.assertTrue(math.isnan(agg["mean_ic_1d"]))
        self.assertEqual(agg["valid_folds_ic_1d"], 0.0)
        # Other metrics still fine.
        self.assertFalse(math.isnan(agg["mean_ic_5d"]))

    def test_worst_drawdown_ignores_nan(self) -> None:
        import math
        folds = [
            self._fold(0, 0.04, 0.05, 0.12, -0.08, 1.1),
            self._fold(1, 0.02, 0.03, 0.09, math.nan, 0.9),
            self._fold(2, 0.01, 0.02, 0.07, -0.20, 0.6),
        ]
        agg = WalkForwardEngine._compute_aggregate(folds)
        # Worst (most negative) valid drawdown is -0.20.
        self.assertAlmostEqual(agg["worst_drawdown"], -0.20, places=6)
        self.assertEqual(agg["valid_folds_max_drawdown"], 2.0)


class ExtractCostMetricsTests(unittest.TestCase):
    """Regression guards for P2f: ``_extract_cost_metrics`` must not
    silently coerce missing risk metrics to 0.0.

    Old code used ``cost_metrics.get("annualized_return", 0.0)`` in-line
    inside ``_run_single_fold``. That meant any qlib output shape change
    — or a normalizer that failed and routed data into ``{"raw": str(df)}``
    — invisibly turned every fold into a zero-return run. The helper
    now lives as a standalone method and raises on any shape mismatch.
    """

    def test_happy_path_returns_three_floats(self) -> None:
        risk = {
            "excess_return_with_cost": {
                "annualized_return": 0.12,
                "max_drawdown": -0.08,
                "information_ratio": 1.1,
                "mean": 0.0004,
            }
        }
        ann, dd, ir = WalkForwardEngine._extract_cost_metrics(risk, fold_index=0)
        self.assertAlmostEqual(ann, 0.12)
        self.assertAlmostEqual(dd, -0.08)
        self.assertAlmostEqual(ir, 1.1)

    def test_raises_when_excess_return_with_cost_missing(self) -> None:
        """Simulate a future qlib shape change where the top-level
        ``excess_return_with_cost`` block disappears."""
        risk = {"return_no_cost": {"annualized_return": 0.05}}
        with self.assertRaisesRegex(
            WalkForwardError, "excess_return_with_cost"
        ):
            WalkForwardEngine._extract_cost_metrics(risk, fold_index=7)

    def test_raises_when_normalizer_fell_back_to_raw(self) -> None:
        """If the risk_analysis normalizer ever regressed and produced
        ``{"raw": "<DataFrame repr>"}``, the extractor must refuse."""
        risk = {"raw": "pd.DataFrame repr"}
        with self.assertRaisesRegex(
            WalkForwardError, "excess_return_with_cost"
        ):
            WalkForwardEngine._extract_cost_metrics(risk, fold_index=0)

    def test_raises_when_cost_metrics_not_dict(self) -> None:
        """qlib returning a scalar or a DataFrame-as-string must raise,
        not silently coerce to zeros."""
        risk = {"excess_return_with_cost": "some string"}
        with self.assertRaisesRegex(
            WalkForwardError, "expected dict"
        ):
            WalkForwardEngine._extract_cost_metrics(risk, fold_index=3)

    def test_raises_when_required_metric_missing(self) -> None:
        """A present-but-incomplete ``excess_return_with_cost`` must
        also raise — the 0.0 default silently masked this."""
        risk = {
            "excess_return_with_cost": {
                "annualized_return": 0.05,
                # max_drawdown and information_ratio missing
            }
        }
        with self.assertRaisesRegex(WalkForwardError, "missing"):
            WalkForwardEngine._extract_cost_metrics(risk, fold_index=0)


_QLIB_DATA_DIR = Path("D:/qlib_data/my_cn_data")


def _qlib_available():
    try:
        import qlib  # noqa: F401
        return _QLIB_DATA_DIR.exists()
    except ImportError:
        return False


from tests.e2e_guard import skip_unless_e2e

@skip_unless_e2e
@unittest.skipUnless(_qlib_available(), "requires qlib + local data bundle")
class WalkForwardE2ETests(unittest.TestCase):
    """E2E test with 2 folds using short windows."""

    @classmethod
    def setUpClass(cls):
        from src.core.qlib_runtime import (
            QlibRuntimeConfig,
            init_qlib_canonical,
            is_canonical_qlib_initialized,
        )
        if not is_canonical_qlib_initialized():
            init_qlib_canonical(QlibRuntimeConfig(
                provider_uri=str(_QLIB_DATA_DIR),
                region="cn",
                data_adjust_mode="pre_adjusted",
            ))

    def test_two_fold_walk_forward(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            config = WalkForwardConfig(
                instruments="csi300",
                overall_start="2024-01-01",
                overall_end="2025-06-30",
                train_months=6,
                valid_months=3,
                test_months=3,
                step_months=3,
                num_boost_round=30,  # fast
                benchmark_code="SH600000",
                output_dir=tmpdir,
            )
            result = WalkForwardEngine.run(config)
            self.assertGreaterEqual(result.num_folds, 2)
            self.assertEqual(len(result.folds), result.num_folds)
            # Check fold structure
            for fold in result.folds:
                self.assertIsNotNone(fold.ic_1d)
                self.assertIsNotNone(fold.annualized_return)
            # Aggregate metrics
            self.assertIn("mean_ic_1d", result.aggregate_metrics)
            self.assertIn("mean_annualized_return", result.aggregate_metrics)


if __name__ == "__main__":
    unittest.main()
