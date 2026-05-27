"""Tests for src.core.walk_forward — walk-forward rolling backtest engine."""

import unittest
from pathlib import Path
from typing import Any
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
        with patch("src.core.walk_forward.engine.is_canonical_qlib_initialized", return_value=False):
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
        self.assertEqual(cfg.compute_device, "cpu")

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

    def test_accepts_zero_signal_to_execution_lag_as_explicit_same_day(self):
        cfg = WalkForwardConfig(signal_to_execution_lag=0)
        self.assertEqual(cfg.signal_to_execution_lag, 0)

    def test_rejects_negative_signal_to_execution_lag(self):
        with self.assertRaisesRegex(WalkForwardError, "signal_to_execution_lag"):
            WalkForwardConfig(signal_to_execution_lag=-1)

    def test_rejects_unknown_adjust_mode(self):
        with self.assertRaisesRegex(WalkForwardError, "adjust_mode"):
            WalkForwardConfig(adjust_mode="auto")

    def test_rejects_unknown_compute_device(self):
        with self.assertRaisesRegex(WalkForwardError, "compute_device"):
            WalkForwardConfig(compute_device="cuda")

    def test_rejects_gpu_for_non_lgb_model(self):
        with self.assertRaisesRegex(WalkForwardError, "silently fall"):
            WalkForwardConfig(model_type="CatBoostModel", compute_device="gpu")

    # ----------------------------------------------------------------
    # Regression for bug.md P1-8: ``model_type`` was previously only
    # validated when ``compute_device == "gpu"``. CPU runs with a
    # typo (``"LGBModle"``) passed config construction and only failed
    # hours later inside ``ModelTrainer._create_model`` after the
    # feature build, wasting compute.
    # ----------------------------------------------------------------

    def test_rejects_unknown_model_type_in_cpu_mode(self):
        """Even with ``compute_device="cpu"`` (the default), an
        unknown ``model_type`` must surface at config-construction."""
        with self.assertRaisesRegex(WalkForwardError, "model_type"):
            WalkForwardConfig(model_type="LGBModle", compute_device="cpu")

    def test_rejects_unknown_model_type_with_default_device(self):
        """The default device is CPU; the model_type check must fire
        without needing ``compute_device`` to be set explicitly."""
        with self.assertRaisesRegex(WalkForwardError, "model_type"):
            WalkForwardConfig(model_type="UnknownModel")

    def test_accepts_each_supported_model_type(self):
        """The whitelist is the source of truth; this test pins the
        currently-supported set so adding/removing one is visible in
        the diff."""
        for mt in ("LGBModel", "XGBModel", "CatBoostModel"):
            with self.subTest(model_type=mt):
                # Should NOT raise.
                WalkForwardConfig(model_type=mt)

    def test_rejects_unknown_execution_price_kind(self):
        with self.assertRaisesRegex(WalkForwardError, "execution_price_kind"):
            WalkForwardConfig(execution_price_kind="limit")

    def test_rejects_negative_min_cost(self):
        with self.assertRaisesRegex(WalkForwardError, "min_cost"):
            WalkForwardConfig(min_cost=-1.0)

    def test_rejects_invalid_limit_threshold(self):
        with self.assertRaisesRegex(WalkForwardError, "limit_threshold"):
            WalkForwardConfig(limit_threshold=0.0)


def _stub_signal_result(ic_summary=None):
    """Build a minimally valid SignalAnalysisResult-shaped stub.

    The real result is a frozen dataclass; constructing one keeps the
    fold report writer (which iterates ``ic_decay`` / ``turnover_stats``
    by name) working without resorting to MagicMock attribute fishing.
    """
    from src.core.signal_analyzer import SignalAnalysisResult
    return SignalAnalysisResult(
        ic_summary=ic_summary or {1: {"mean_ic": 0.01}, 5: {"mean_ic": 0.02}},
        ic_series={},
        ic_decay=[0.01, 0.005, 0.0],
        turnover_stats={"mean_turnover": 0.3},
    )


def _stub_backtest_output():
    """Real-shape ``CanonicalBacktestOutput`` for fold-report writes.

    Walk-forward now persists ``backtest_output.report`` /
    ``risk_analysis`` / ``provenance`` / ``positions`` to JSON; a
    MagicMock would either pass non-dict types into ``json.dump`` or
    silently spawn nested MagicMocks. Use a real instance so the writer
    sees the contract it was designed against.
    """
    from src.core.canonical_backtest_contract import CanonicalBacktestOutput
    return CanonicalBacktestOutput(
        metric_status="official",
        official_backtest_path="qlib.backtest.backtest",
        return_series={"return": {}, "bench": {}, "cost": {}},
        risk_analysis={
            "excess_return_with_cost": {
                "annualized_return": 0.11,
                "max_drawdown": -0.08,
                "information_ratio": 1.2,
            }
        },
        report={"total_days": 60},
        provenance={"adjust_mode": "pre_adjusted"},
        positions={"2024-10-01": {"SH600000": 1.0}},
    )


class WalkForwardBacktestPassthroughTests(unittest.TestCase):
    def test_fold_backtest_request_uses_configured_controls(self) -> None:
        import tempfile

        config = WalkForwardConfig(
            execution_price_kind=EXECUTION_PRICE_OPEN,
            adjust_mode=ADJUST_MODE_NONE,
            signal_to_execution_lag=3,
            min_cost=7.5,
            limit_threshold=0.195,
            commission_rate=0.0007,
            # Single-entry schedule pins the rate at 8 bps for the
            # entire window — replaces the legacy
            # ``stamp_tax_bps=8.0`` scalar test fixture.
            stamp_tax_schedule=[
                {"effective_from": "2008-09-19", "bps": 8.0},
            ],
            slippage_bps=3.0,
        )

        fake_feature_result = MagicMock()
        fake_feature_result.dataset = object()
        fake_model_result = MagicMock()
        fake_model_result.predictions = "predictions"
        fake_model_result.prediction_shape = (10,)
        fake_model_result.best_iteration = 3
        fake_model_result.final_valid_loss = 0.95

        with tempfile.TemporaryDirectory() as tmp, patch(
            "src.core.walk_forward.engine.FeatureDatasetBuilder.build",
            return_value=fake_feature_result,
        ), patch(
            "src.core.walk_forward.engine.ModelTrainer.train_and_predict",
            return_value=fake_model_result,
        ), patch(
            "src.core.walk_forward.engine.SignalAnalyzer.analyze",
            return_value=_stub_signal_result(),
        ), patch(
            "src.core.walk_forward.engine.BacktestRunner.run",
            return_value=_stub_backtest_output(),
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
                output_dir=Path(tmp),
            )

        request = mock_backtest.call_args.kwargs["request"]
        self.assertEqual(request.adjust_mode, ADJUST_MODE_NONE)
        self.assertEqual(request.signal_to_execution_lag, 3)
        self.assertEqual(request.exchange_config.execution_price_kind, EXECUTION_PRICE_OPEN)
        self.assertEqual(request.exchange_config.limit_threshold, 0.195)
        self.assertEqual(request.exchange_config.cost_model.min_cost, 7.5)
        self.assertEqual(request.exchange_config.cost_model.commission_rate, 0.0007)

    def test_fold_lgb_regularisation_passed_to_trainer(self) -> None:
        """``WalkForwardConfig.lambda_l2`` etc. must reach
        ``ModelTrainConfig`` — without this passthrough the new tunable
        knobs at the YAML layer would silently no-op and ``best_iteration``
        would still hit the un-tuned plateau.
        """
        import tempfile

        config = WalkForwardConfig(
            lambda_l1=0.2,
            lambda_l2=1.5,
            min_data_in_leaf=42,
            feature_fraction=0.7,
            bagging_fraction=0.8,
            bagging_freq=5,
        )

        fake_feature_result = MagicMock()
        fake_feature_result.dataset = object()
        fake_model_result = MagicMock(
            predictions="predictions",
            prediction_shape=(10,),
            best_iteration=120,
            final_valid_loss=0.93,
        )

        with tempfile.TemporaryDirectory() as tmp, patch(
            "src.core.walk_forward.engine.FeatureDatasetBuilder.build",
            return_value=fake_feature_result,
        ), patch(
            "src.core.walk_forward.engine.ModelTrainer.train_and_predict",
            return_value=fake_model_result,
        ) as mock_trainer, patch(
            "src.core.walk_forward.engine.SignalAnalyzer.analyze",
            return_value=_stub_signal_result(),
        ), patch(
            "src.core.walk_forward.engine.BacktestRunner.run",
            return_value=_stub_backtest_output(),
        ):
            WalkForwardEngine._run_single_fold(
                config=config,
                fold_index=0,
                train_start="2024-01-01", train_end="2024-06-30",
                valid_start="2024-07-01", valid_end="2024-09-30",
                test_start="2024-10-01", test_end="2024-12-31",
                output_dir=Path(tmp),
            )

        train_cfg = mock_trainer.call_args.kwargs["config"]
        self.assertEqual(train_cfg.lambda_l1, 0.2)
        self.assertEqual(train_cfg.lambda_l2, 1.5)
        self.assertEqual(train_cfg.min_data_in_leaf, 42)
        self.assertEqual(train_cfg.feature_fraction, 0.7)
        self.assertEqual(train_cfg.bagging_fraction, 0.8)
        self.assertEqual(train_cfg.bagging_freq, 5)


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

    # ── bootstrap CI ────────────────────────────────────────────────

    def test_bootstrap_ci_keys_are_present(self) -> None:
        folds = [
            self._fold(0, 0.04, 0.05, 0.12, -0.08, 1.1),
            self._fold(1, 0.02, 0.03, 0.09, -0.10, 0.9),
        ]
        agg = WalkForwardEngine._compute_aggregate(folds)
        self.assertIn("mean_ic_1d_ci_low", agg)
        self.assertIn("mean_ic_1d_ci_high", agg)
        self.assertIn("mean_information_ratio_ci_low", agg)
        self.assertIn("mean_information_ratio_ci_high", agg)
        self.assertIn("std_information_ratio", agg)
        self.assertIn("bootstrap_seed", agg)
        self.assertIn("bootstrap_n", agg)

    def test_bootstrap_ci_nan_with_single_fold(self) -> None:
        import math
        folds = [self._fold(0, 0.04, 0.05, 0.12, -0.08, 1.1)]
        agg = WalkForwardEngine._compute_aggregate(folds)
        self.assertTrue(math.isnan(agg["mean_ic_1d_ci_low"]))
        self.assertTrue(math.isnan(agg["mean_ic_1d_ci_high"]))

    def test_bootstrap_ci_deterministic_given_seed(self) -> None:
        folds = [
            self._fold(i, 0.01 * i, 0.01 * i, 0.05 * i, -0.05 * i, 0.5 * i)
            for i in range(1, 9)
        ]
        agg1 = WalkForwardEngine._compute_aggregate(folds)
        agg2 = WalkForwardEngine._compute_aggregate(folds)
        self.assertEqual(agg1["mean_ic_1d_ci_low"], agg2["mean_ic_1d_ci_low"])
        self.assertEqual(agg1["mean_ic_1d_ci_high"], agg2["mean_ic_1d_ci_high"])

    def test_bootstrap_ci_nan_with_all_nan_input(self) -> None:
        import math
        folds = [
            self._fold(0, math.nan, math.nan, math.nan, math.nan, math.nan),
            self._fold(1, math.nan, math.nan, math.nan, math.nan, math.nan),
        ]
        agg = WalkForwardEngine._compute_aggregate(folds)
        self.assertTrue(math.isnan(agg["mean_ic_1d_ci_low"]))


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


class FoldReportSerialisationTests(unittest.TestCase):
    """Per-fold report contract.

    Walk-forward used to leave only ``model_foldN.pkl`` files behind
    after a multi-fold run — every IC, return, drawdown, signal-analysis
    block, and positions snapshot was discarded the moment the engine
    returned. Comparing two runs (different hyperparams, different
    feature handler) was impossible without re-running both. The
    per-fold report fixes that.

    These tests pin the JSON schema so dashboards / diff tools can
    rely on the field set.
    """

    def _build_args(self) -> dict:
        return dict(
            fold_index=0,
            train_start="2024-01-01", train_end="2024-06-30",
            valid_start="2024-07-01", valid_end="2024-09-30",
            test_start="2024-10-01", test_end="2024-12-31",
            model_artifact_path="/tmp/model_fold0.pkl",
            model_result=MagicMock(
                best_iteration=3,
                final_valid_loss=0.95,
                prediction_shape=(123,),
            ),
            signal_result=_stub_signal_result(),
            backtest_output=_stub_backtest_output(),
            positions_path=Path("/tmp/fold_00_positions.json"),
            ic_1d=0.02,
            ic_5d=0.04,
            annualized_return=0.11,
            max_drawdown=-0.08,
            information_ratio=1.2,
        )

    def test_top_level_fields_present(self) -> None:
        d = WalkForwardEngine._build_fold_report(**self._build_args())
        for key in (
            "fold_index", "windows", "model", "signal_analysis",
            "backtest", "metrics", "positions_path", "generated_at",
        ):
            self.assertIn(key, d, f"missing top-level field: {key}")

    def test_windows_shape(self) -> None:
        d = WalkForwardEngine._build_fold_report(**self._build_args())
        self.assertEqual(d["windows"]["train"]["start"], "2024-01-01")
        self.assertEqual(d["windows"]["train"]["end"], "2024-06-30")
        self.assertEqual(d["windows"]["valid"]["start"], "2024-07-01")
        self.assertEqual(d["windows"]["test"]["end"], "2024-12-31")

    def test_model_block_carries_diagnostics(self) -> None:
        d = WalkForwardEngine._build_fold_report(**self._build_args())
        self.assertEqual(d["model"]["best_iteration"], 3)
        self.assertEqual(d["model"]["final_valid_loss"], 0.95)
        self.assertEqual(d["model"]["prediction_shape"], [123])
        self.assertEqual(d["model"]["artifact_path"], "/tmp/model_fold0.pkl")

    def test_signal_analysis_keys_coerced_to_strings(self) -> None:
        """``ic_summary`` is keyed by int forward-period; JSON keys must
        be strings, so the report builder must coerce them — otherwise
        ``json.dumps`` would either raise (strict mode) or silently
        emit non-string keys some parsers reject."""
        d = WalkForwardEngine._build_fold_report(**self._build_args())
        keys = list(d["signal_analysis"]["ic_summary"].keys())
        self.assertTrue(
            all(isinstance(k, str) for k in keys),
            f"ic_summary keys must be strings; got {keys!r}",
        )
        self.assertIn("1", keys)
        self.assertIn("5", keys)

    def test_metrics_block_mirrors_walk_forward_fold(self) -> None:
        d = WalkForwardEngine._build_fold_report(**self._build_args())
        self.assertAlmostEqual(d["metrics"]["ic_1d"], 0.02)
        self.assertAlmostEqual(d["metrics"]["ic_5d"], 0.04)
        self.assertAlmostEqual(d["metrics"]["annualized_return"], 0.11)
        self.assertAlmostEqual(d["metrics"]["max_drawdown"], -0.08)
        self.assertAlmostEqual(d["metrics"]["information_ratio"], 1.2)

    def test_positions_path_optional(self) -> None:
        args = self._build_args()
        args["positions_path"] = None
        d = WalkForwardEngine._build_fold_report(**args)
        self.assertIsNone(d["positions_path"])

    def test_write_fold_report_round_trips_through_strict_json(self) -> None:
        """The JSON written must parse with ``allow_nan=False``.
        ``_sanitize_for_json`` should turn any NaN IC / return into
        ``null`` so the report stays standard JSON regardless of the
        underlying analyser's NaN encoding."""
        import json
        import tempfile

        args = self._build_args()
        # Inject NaN to confirm the sanitizer engages.
        args["ic_1d"] = float("nan")
        args["information_ratio"] = float("inf")
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "fold_report.json"
            WalkForwardEngine._write_fold_report(report_path=report_path, **args)
            with open(report_path) as f:
                loaded = json.load(f)
        self.assertIsNone(loaded["metrics"]["ic_1d"])
        self.assertIsNone(loaded["metrics"]["information_ratio"])
        # Other fields untouched.
        self.assertEqual(loaded["fold_index"], 0)
        self.assertEqual(loaded["windows"]["test"]["start"], "2024-10-01")


class AggregateReportSerialisationTests(unittest.TestCase):
    """Aggregate report contract: a single index file pointing at every
    per-fold report plus cross-fold aggregates and a config snapshot.

    Without an aggregate file, comparing two walk-forward runs requires
    listing per-fold JSONs and hand-aggregating — exactly the friction
    the ``walk_forward_report.json`` is meant to remove.
    """

    def _build_folds(self) -> list:
        from src.core.walk_forward import WalkForwardFold
        return [
            WalkForwardFold(
                fold_index=0,
                train_period="2024-01-01 ~ 2024-06-30",
                valid_period="2024-07-01 ~ 2024-09-30",
                test_period="2024-10-01 ~ 2024-12-31",
                ic_1d=0.02, ic_5d=0.04,
                annualized_return=0.10, max_drawdown=-0.05,
                information_ratio=1.5,
                prediction_shape=(100,),
                report_path="/tmp/fold_00_report.json",
            ),
            WalkForwardFold(
                fold_index=1,
                train_period="2024-04-01 ~ 2024-09-30",
                valid_period="2024-10-01 ~ 2024-12-31",
                test_period="2025-01-01 ~ 2025-03-31",
                ic_1d=float("nan"),  # would poison json.dumps without sanitize
                ic_5d=0.01,
                annualized_return=-0.02, max_drawdown=-0.10,
                information_ratio=-0.3,
                prediction_shape=(100,),
                report_path="/tmp/fold_01_report.json",
            ),
        ]

    def _fold_with_test_period(self, idx: int, test_period: str):
        from src.core.walk_forward import WalkForwardFold
        return WalkForwardFold(
            fold_index=idx,
            train_period="2024-01-01 ~ 2024-06-30",
            valid_period="2024-07-01 ~ 2024-09-30",
            test_period=test_period,
            ic_1d=0.02,
            ic_5d=0.04,
            annualized_return=0.10,
            max_drawdown=-0.05,
            information_ratio=1.5,
            prediction_shape=(100,),
            report_path=f"/tmp/fold_{idx:02d}_report.json",
        )

    def test_top_level_fields_present(self) -> None:
        d = WalkForwardEngine._build_aggregate_report(
            config=WalkForwardConfig(),
            folds=self._build_folds(),
            aggregate_metrics={"mean_ic_1d": 0.01, "num_folds": 2.0},
        )
        for key in (
            "generated_at",
            "config",
            "folds",
            "aggregate_metrics",
            "test_window_coverage",
            "num_folds",
        ):
            self.assertIn(key, d)

    def test_test_window_coverage_reports_continuous_periods(self) -> None:
        d = WalkForwardEngine._build_aggregate_report(
            config=WalkForwardConfig(),
            folds=self._build_folds(),
            aggregate_metrics={},
        )
        coverage = d["test_window_coverage"]
        self.assertEqual(coverage["mode"], "continuous")
        self.assertEqual(coverage["gap_count"], 0)
        self.assertEqual(coverage["overlap_count"], 0)
        self.assertEqual(coverage["max_overlap_depth"], 1)

    def test_test_window_coverage_reports_gaps(self) -> None:
        d = WalkForwardEngine._build_aggregate_report(
            config=WalkForwardConfig(),
            folds=[
                self._fold_with_test_period(0, "2024-01-01 ~ 2024-01-31"),
                self._fold_with_test_period(1, "2024-02-03 ~ 2024-02-29"),
            ],
            aggregate_metrics={},
        )
        coverage = d["test_window_coverage"]
        self.assertEqual(coverage["mode"], "gapped")
        self.assertEqual(coverage["gap_count"], 1)
        self.assertEqual(coverage["max_gap_days"], 2)

    def test_test_window_coverage_reports_overlaps(self) -> None:
        d = WalkForwardEngine._build_aggregate_report(
            config=WalkForwardConfig(),
            folds=[
                self._fold_with_test_period(0, "2024-01-01 ~ 2024-01-31"),
                self._fold_with_test_period(1, "2024-01-15 ~ 2024-02-15"),
            ],
            aggregate_metrics={},
        )
        coverage = d["test_window_coverage"]
        self.assertEqual(coverage["mode"], "overlapping")
        self.assertEqual(coverage["overlap_count"], 1)
        self.assertEqual(coverage["max_overlap_days"], 17)
        self.assertEqual(coverage["max_overlap_depth"], 2)

    def test_per_fold_summaries_carry_report_path(self) -> None:
        """Each entry in ``folds`` must point at the per-fold JSON so a
        consumer can drill in without re-globbing the directory."""
        d = WalkForwardEngine._build_aggregate_report(
            config=WalkForwardConfig(),
            folds=self._build_folds(),
            aggregate_metrics={},
        )
        self.assertEqual(len(d["folds"]), 2)
        self.assertEqual(d["folds"][0]["report_path"], "/tmp/fold_00_report.json")
        self.assertEqual(d["folds"][1]["fold_index"], 1)

    def test_config_snapshot_round_trips(self) -> None:
        """``config`` must capture every field of ``WalkForwardConfig``
        so the run is reproducible from the report alone."""
        cfg = WalkForwardConfig(num_boost_round=42, topk=33)
        d = WalkForwardEngine._build_aggregate_report(
            config=cfg, folds=[], aggregate_metrics={},
        )
        self.assertEqual(d["config"]["num_boost_round"], 42)
        self.assertEqual(d["config"]["topk"], 33)
        self.assertEqual(d["config"]["instruments"], cfg.instruments)

    def test_write_aggregate_report_strict_json_round_trips(self) -> None:
        """The aggregate file must parse with strict JSON, even when
        a fold has NaN IC. Without ``_sanitize_for_json`` the report
        would emit the non-standard ``NaN`` token."""
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "walk_forward_report.json"
            WalkForwardEngine._write_aggregate_report(
                path=path,
                config=WalkForwardConfig(),
                folds=self._build_folds(),
                aggregate_metrics={"mean_ic_1d": float("nan"), "num_folds": 2.0},
            )
            with open(path) as f:
                loaded = json.load(f)
        self.assertEqual(loaded["num_folds"], 2)
        self.assertEqual(len(loaded["folds"]), 2)
        # NaN fold metric must come through as null, not crash json.load
        self.assertIsNone(loaded["folds"][1]["ic_1d"])
        self.assertIsNone(loaded["aggregate_metrics"]["mean_ic_1d"])


class FoldReportPersistenceFlowTests(unittest.TestCase):
    """End-to-end (within ``_run_single_fold``): given mocked feature /
    model / signal / backtest dependencies, the engine writes
    ``fold_NN_report.json`` + ``fold_NN_positions.json`` to ``output_dir``
    and the returned ``WalkForwardFold.report_path`` matches.
    """

    def test_run_single_fold_persists_report_and_positions(self) -> None:
        import json
        import tempfile

        config = WalkForwardConfig()
        fake_feature_result = MagicMock()
        fake_feature_result.dataset = object()
        fake_model_result = MagicMock(
            predictions="predictions",
            prediction_shape=(10,),
            best_iteration=4,
            final_valid_loss=0.97,
        )

        with tempfile.TemporaryDirectory() as tmp, patch(
            "src.core.walk_forward.engine.FeatureDatasetBuilder.build",
            return_value=fake_feature_result,
        ), patch(
            "src.core.walk_forward.engine.ModelTrainer.train_and_predict",
            return_value=fake_model_result,
        ), patch(
            "src.core.walk_forward.engine.SignalAnalyzer.analyze",
            return_value=_stub_signal_result(),
        ), patch(
            "src.core.walk_forward.engine.BacktestRunner.run",
            return_value=_stub_backtest_output(),
        ):
            fold = WalkForwardEngine._run_single_fold(
                config=config,
                fold_index=0,
                train_start="2024-01-01", train_end="2024-06-30",
                valid_start="2024-07-01", valid_end="2024-09-30",
                test_start="2024-10-01", test_end="2024-12-31",
                output_dir=Path(tmp),
            )
            report_path = Path(tmp) / "fold_00_report.json"
            positions_path = Path(tmp) / "fold_00_positions.json"
            predictions_path = Path(tmp) / "fold_00_predictions.pkl"

            self.assertTrue(report_path.exists())
            self.assertTrue(positions_path.exists())
            self.assertTrue(predictions_path.exists())
            self.assertEqual(fold.report_path, str(report_path))
            with open(report_path) as f:
                report = json.load(f)
            self.assertEqual(report["fold_index"], 0)
            self.assertEqual(report["positions_path"], str(positions_path))
            self.assertEqual(
                report["ensemble"]["prediction_artifact_path"],
                str(predictions_path),
            )
            with open(positions_path) as f:
                positions = json.load(f)
            self.assertIn("2024-10-01", positions)


class WalkForwardConfigIndustryTaxonomyTests(unittest.TestCase):
    """Boundary contract for the four ``industry_*`` fields on
    :class:`WalkForwardConfig`.

    Mirrors :class:`PipelineConfigPostInitTests` — the same partial-
    config rejection runs through
    :func:`assert_industry_config_complete_or_empty`. If the two
    callers ever diverge a fold could silently load a half-configured
    artifact with a confusing "no such file" deep in the loader.
    """

    def test_default_uses_board_heuristic(self) -> None:
        cfg = WalkForwardConfig()
        self.assertIsNone(cfg.industry_artifact_path)
        self.assertIsNone(cfg.industry_manifest_path)
        self.assertEqual(cfg.industry_taxonomy_id, "")

    def test_all_three_set_passes(self) -> None:
        cfg = WalkForwardConfig(
            industry_artifact_path="output/taxonomy/sw_l2.csv",
            industry_manifest_path="output/taxonomy/sw_l2.json",
            industry_taxonomy_id="tushare_sw_l2",
        )
        self.assertEqual(cfg.industry_taxonomy_id, "tushare_sw_l2")

    def test_only_artifact_set_rejected(self) -> None:
        with self.assertRaisesRegex(WalkForwardError, "explicit triple"):
            WalkForwardConfig(industry_artifact_path="a.csv")

    def test_only_manifest_set_rejected(self) -> None:
        with self.assertRaisesRegex(WalkForwardError, "explicit triple"):
            WalkForwardConfig(industry_manifest_path="a.json")

    def test_only_taxonomy_id_set_rejected(self) -> None:
        with self.assertRaisesRegex(WalkForwardError, "explicit triple"):
            WalkForwardConfig(industry_taxonomy_id="t")

    def test_unsupported_temporal_mode_rejected(self) -> None:
        from src.contracts.taxonomy_data_contract import TAXONOMY_MODE_TRADE_DATE
        with self.assertRaisesRegex(WalkForwardError, "industry_temporal_mode"):
            WalkForwardConfig(industry_temporal_mode=TAXONOMY_MODE_TRADE_DATE)

    def test_run_attribution_default_true(self) -> None:
        """Per-fold attribution is the main observability win after the
        per-fold report wiring; default-true so a vanilla walk-forward
        run carries the block out of the box."""
        cfg = WalkForwardConfig()
        self.assertTrue(cfg.run_attribution)


class _AttributionForFoldTests(unittest.TestCase):
    """Direct-call coverage of ``_run_attribution_for_fold``.

    Avoids the full ``_run_single_fold`` setup so each branch (disabled
    / no-positions / artifact-load-failed / engine-error / happy-path)
    is exercised in isolation. Each branch returns a specific
    ``(result, skipped_reason)`` tuple the fold report serialiser
    depends on.
    """

    def _backtest_output_with_positions(self, positions=None):
        from src.core.canonical_backtest_contract import CanonicalBacktestOutput
        return CanonicalBacktestOutput(
            metric_status="official",
            official_backtest_path="qlib.backtest.backtest",
            return_series={"return": {}, "bench": {}, "cost": {}},
            risk_analysis={"excess_return_with_cost": {
                "annualized_return": 0.1, "max_drawdown": -0.05,
                "information_ratio": 1.0,
            }},
            report={}, provenance={},
            positions=positions if positions is not None else {
                "2024-10-01": {"SH600000": 1.0},
            },
        )

    def test_disabled_by_config_short_circuits(self) -> None:
        config = WalkForwardConfig(run_attribution=False)
        result, reason = WalkForwardEngine._run_attribution_for_fold(
            config=config, fold_index=0,
            test_start="2024-10-01", test_end="2024-12-31",
            predictions=MagicMock(),
            backtest_output=self._backtest_output_with_positions(),
        )
        self.assertIsNone(result)
        self.assertEqual(reason, "disabled_by_config")

    def test_no_positions_short_circuits_without_calling_engine(self) -> None:
        """Refusing to silently fall back to a prediction-score proxy
        when the backtest produced no positions — same no-implicit-fallback
        rule that's already in :class:`Pipeline`."""
        config = WalkForwardConfig()
        with patch(
            "src.core.walk_forward.engine.PerformanceAttribution.analyze"
        ) as mock_analyze:
            result, reason = WalkForwardEngine._run_attribution_for_fold(
                config=config, fold_index=0,
                test_start="2024-10-01", test_end="2024-12-31",
                predictions=MagicMock(),
                backtest_output=self._backtest_output_with_positions(positions={}),
            )
        self.assertIsNone(result)
        self.assertEqual(reason, "no_positions_from_backtest")
        mock_analyze.assert_not_called()

    def test_engine_error_yields_skip_with_typed_reason(self) -> None:
        """Degenerate inputs (e.g. all-zero positions) raise
        :class:`PerformanceAttributionError` from the engine. Walk-forward
        downgrades that to skip + WARN with a typed reason so the diff
        tool (PR #29) can flag the degraded fold without aborting the
        rest of the run."""
        from src.core.performance_attribution import PerformanceAttributionError
        config = WalkForwardConfig()
        with patch(
            "src.core.walk_forward.engine.PerformanceAttribution.analyze",
            side_effect=PerformanceAttributionError("all-non-positive"),
        ):
            result, reason = WalkForwardEngine._run_attribution_for_fold(
                config=config, fold_index=3,
                test_start="2024-10-01", test_end="2024-12-31",
                predictions=MagicMock(),
                backtest_output=self._backtest_output_with_positions(),
            )
        self.assertIsNone(result)
        self.assertTrue(reason.startswith("engine_error: "))
        self.assertIn("PerformanceAttributionError", reason)
        self.assertIn("all-non-positive", reason)

    def test_unexpected_attribution_error_yields_skip_with_typed_reason(self) -> None:
        """Unexpected optional attribution failures keep fold outputs."""
        config = WalkForwardConfig()
        with patch(
            "src.core.walk_forward.engine.PerformanceAttribution.analyze",
            side_effect=ValueError("bad attribution shape"),
        ):
            result, reason = WalkForwardEngine._run_attribution_for_fold(
                config=config, fold_index=3,
                test_start="2024-10-01", test_end="2024-12-31",
                predictions=MagicMock(),
                backtest_output=self._backtest_output_with_positions(),
            )
        self.assertIsNone(result)
        self.assertTrue(reason.startswith("unexpected_error: "))
        self.assertIn("ValueError", reason)
        self.assertIn("bad attribution shape", reason)

    def test_artifact_load_failure_promotes_to_walkforward_error(self) -> None:
        """Industry-artifact load failures are config / file problems —
        every fold will hit the same error, so we promote to a hard
        :class:`WalkForwardError` rather than silently skipping
        attribution. Operator should fix the root cause once."""
        config = WalkForwardConfig(
            industry_artifact_path="/no/such/dir/missing.csv",
            industry_manifest_path="/no/such/dir/missing.json",
            industry_taxonomy_id="tushare_sw_l2",
        )
        with self.assertRaisesRegex(WalkForwardError, "industry taxonomy load failed"):
            WalkForwardEngine._run_attribution_for_fold(
                config=config, fold_index=0,
                test_start="2024-10-01", test_end="2024-12-31",
                predictions=MagicMock(),
                backtest_output=self._backtest_output_with_positions(),
            )


class _AttributionSectionForFoldTests(unittest.TestCase):
    """Pin the JSON shape that lands in ``fold_NN_report.json``'s
    ``attribution`` block. Same status / skipped_reason convention as
    :meth:`Pipeline._attribution_section` so a single downstream
    consumer reads both."""

    def test_skipped_section_carries_status_and_reason(self) -> None:
        block = WalkForwardEngine._attribution_section_for_fold(
            None, "no_positions_from_backtest",
        )
        self.assertEqual(block["status"], "skipped")
        self.assertEqual(block["skipped_reason"], "no_positions_from_backtest")
        # No keys leaked from the ok branch — a consumer that branches on
        # ``status`` should not have to defensively check for missing
        # ok-only fields.
        self.assertNotIn("sector_attribution", block)
        self.assertNotIn("sector_taxonomy", block)

    def test_skipped_with_no_reason_falls_back_to_unknown(self) -> None:
        """Defensive: a future bug that calls this with both ``None``
        must still produce a parseable status field, not ``null``."""
        block = WalkForwardEngine._attribution_section_for_fold(None, None)
        self.assertEqual(block["status"], "skipped")
        self.assertEqual(block["skipped_reason"], "unknown_reason")

    def test_ok_section_carries_taxonomy_and_effects(self) -> None:
        from src.core.performance_attribution import (
            ATTRIBUTION_METHOD_SINGLE_PERIOD,
            BENCH_WEIGHT_METHOD_EQUAL,
            AttributionResult,
            SectorAttribution,
        )

        result = AttributionResult(
            sector_attribution=(
                SectorAttribution(
                    sector="银行",
                    portfolio_weight=0.5, benchmark_weight=0.4,
                    portfolio_return=0.10, benchmark_return=0.08,
                    allocation_effect=0.001,
                    selection_effect=0.002,
                    interaction_effect=0.0005,
                    total_effect=0.0035,
                ),
            ),
            total_allocation_effect=0.001, total_selection_effect=0.002,
            total_interaction_effect=0.0005,
            monthly_returns=(),
            total_portfolio_return=0.10, total_benchmark_return=0.08,
            total_excess_return=0.02,
            attribution_method=ATTRIBUTION_METHOD_SINGLE_PERIOD,
            sector_effects_sum=0.0035, reconciliation_residual=0.0165,
            sector_taxonomy="tushare_sw_l2",
            bench_weight_method=BENCH_WEIGHT_METHOD_EQUAL,
        )
        block = WalkForwardEngine._attribution_section_for_fold(result, None)

        self.assertEqual(block["status"], "ok")
        self.assertIsNone(block["skipped_reason"])
        self.assertEqual(block["sector_taxonomy"], "tushare_sw_l2")
        self.assertEqual(block["attribution_method"], ATTRIBUTION_METHOD_SINGLE_PERIOD)
        self.assertEqual(block["bench_weight_method"], BENCH_WEIGHT_METHOD_EQUAL)
        self.assertAlmostEqual(block["total_excess_return"], 0.02)
        self.assertAlmostEqual(block["reconciliation_residual"], 0.0165)
        self.assertEqual(len(block["sector_attribution"]), 1)
        self.assertEqual(block["sector_attribution"][0]["sector"], "银行")


class FoldReportContainsAttributionBlockTests(unittest.TestCase):
    """End-to-end: the persisted ``fold_NN_report.json`` carries the
    ``attribution`` block produced by the section helper. Without this
    test, a future refactor could drop the wiring in
    :meth:`_build_fold_report` and per-fold attribution would silently
    vanish from the on-disk report.
    """

    def test_disk_report_has_attribution_skipped_block_by_default(self) -> None:
        import json
        import tempfile

        config = WalkForwardConfig()  # run_attribution=True, no artifact
        fake_feature_result = MagicMock()
        fake_feature_result.dataset = object()
        fake_model_result = MagicMock(
            predictions="predictions",
            prediction_shape=(10,),
            best_iteration=4,
            final_valid_loss=0.97,
        )

        from src.core.performance_attribution import PerformanceAttributionError

        with tempfile.TemporaryDirectory() as tmp, patch(
            "src.core.walk_forward.engine.FeatureDatasetBuilder.build",
            return_value=fake_feature_result,
        ), patch(
            "src.core.walk_forward.engine.ModelTrainer.train_and_predict",
            return_value=fake_model_result,
        ), patch(
            "src.core.walk_forward.engine.SignalAnalyzer.analyze",
            return_value=_stub_signal_result(),
        ), patch(
            "src.core.walk_forward.engine.BacktestRunner.run",
            return_value=_stub_backtest_output(),
        ), patch(
            # Force a skip path so the test does not depend on a
            # working qlib runtime.
            "src.core.walk_forward.engine.PerformanceAttribution.analyze",
            side_effect=PerformanceAttributionError("qlib not initialized"),
        ):
            WalkForwardEngine._run_single_fold(
                config=config,
                fold_index=0,
                train_start="2024-01-01", train_end="2024-06-30",
                valid_start="2024-07-01", valid_end="2024-09-30",
                test_start="2024-10-01", test_end="2024-12-31",
                output_dir=Path(tmp),
            )
            with open(Path(tmp) / "fold_00_report.json") as f:
                report = json.load(f)

        self.assertIn("attribution", report)
        self.assertEqual(report["attribution"]["status"], "skipped")
        self.assertTrue(
            report["attribution"]["skipped_reason"].startswith("engine_error: "),
            report["attribution"]["skipped_reason"],
        )


class EnsembleWindowConfigValidationTests(unittest.TestCase):
    """Pin the boundary contract for ``ensemble_window``.

    The ``frozen=True`` dataclass means a typo in YAML lands at
    config-construction; reject zero / negative / non-int up front so
    the engine never silently disables current-fold predictions.
    """

    def test_default_is_one_no_op(self) -> None:
        cfg = WalkForwardConfig()
        self.assertEqual(cfg.ensemble_window, 1)

    def test_window_three_passes(self) -> None:
        cfg = WalkForwardConfig(ensemble_window=3)
        self.assertEqual(cfg.ensemble_window, 3)

    def test_rejects_zero(self) -> None:
        with self.assertRaisesRegex(WalkForwardError, "ensemble_window"):
            WalkForwardConfig(ensemble_window=0)

    def test_rejects_negative(self) -> None:
        with self.assertRaisesRegex(WalkForwardError, "ensemble_window"):
            WalkForwardConfig(ensemble_window=-2)

    def test_rejects_bool(self) -> None:
        """``True`` would silently behave as ``1`` (no-op) and ``False``
        as ``0`` (would disable current fold). Catch both."""
        with self.assertRaisesRegex(WalkForwardError, "ensemble_window"):
            WalkForwardConfig(ensemble_window=True)
        with self.assertRaisesRegex(WalkForwardError, "ensemble_window"):
            WalkForwardConfig(ensemble_window=False)

    def test_rejects_float(self) -> None:
        with self.assertRaisesRegex(WalkForwardError, "ensemble_window"):
            WalkForwardConfig(ensemble_window=2.5)


def _pandas_available() -> bool:
    try:
        import pandas  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(_pandas_available(), "requires pandas")
class MaybeApplyEnsembleTests(unittest.TestCase):
    """Direct-call coverage of :meth:`WalkForwardEngine._maybe_apply_ensemble`.

    Each branch (window=1 no-op, no priors, fold 0 graceful, all priors
    fail, success) is exercised in isolation so a future refactor does
    not silently change the averaging semantics.

    The ``_pandas_available`` skip keeps the file collectible in
    minimal-deps envs (e.g. lint-only CI) — the engine itself imports
    pandas lazily so the source module loads fine without it.
    """

    def _build_predictions(self, value: float) -> Any:
        """Build a (datetime, instrument) MultiIndex Series of ``value``s.

        The actual averaging logic operates on these Series via
        ``pd.concat(..., axis=1).mean(axis=1)`` so a constant-value Series
        is the cleanest way to assert "averaged correctly" — the result's
        scalar is the arithmetic mean of the inputs.
        """
        import pandas as pd
        idx = pd.MultiIndex.from_tuples(
            [("2024-10-01", "SH600000"), ("2024-10-01", "SH600001")],
            names=("datetime", "instrument"),
        )
        return pd.Series([value, value], index=idx, name="score")

    def test_window_one_returns_current_unchanged(self) -> None:
        current = self._build_predictions(0.5)
        result, meta = WalkForwardEngine._maybe_apply_ensemble(
            current_predictions=current,
            current_dataset=object(),
            prior_model_paths=("/tmp/m0.pkl", "/tmp/m1.pkl"),
            ensemble_window=1,
            current_fold_index=2,
        )
        self.assertIs(result, current)
        self.assertFalse(meta["used"])
        self.assertEqual(meta["n_models"], 1)
        self.assertEqual(meta["window"], 1)
        self.assertEqual(meta["contributing_folds"], [2])
        self.assertEqual(meta["prior_models_attempted"], 0)

    def test_no_priors_falls_back_gracefully(self) -> None:
        """Fold 0 of a multi-fold run has no priors yet — the natural
        degradation path."""
        current = self._build_predictions(0.5)
        result, meta = WalkForwardEngine._maybe_apply_ensemble(
            current_predictions=current,
            current_dataset=object(),
            prior_model_paths=(),
            ensemble_window=3,
            current_fold_index=0,
        )
        self.assertIs(result, current)
        self.assertFalse(meta["used"])
        self.assertEqual(meta["window"], 3)
        self.assertEqual(meta["n_models"], 1)
        self.assertEqual(meta["contributing_folds"], [0])

    def test_window_three_averages_current_plus_two_priors(self) -> None:
        """Happy path: current model returns 0.6, two priors return 0.3
        and 0.0; the average is 0.3."""

        current = self._build_predictions(0.6)
        prior1_pred = self._build_predictions(0.3)
        prior2_pred = self._build_predictions(0.0)

        prior1 = MagicMock()
        prior1.predict.return_value = prior1_pred
        prior2 = MagicMock()
        prior2.predict.return_value = prior2_pred

        # Patch ``open`` + ``pickle.load`` so the loader yields one of
        # our mocks per path. Order: oldest first → fold 1, fold 2.
        load_seq = [prior1, prior2]
        with patch(
            "builtins.open",
            new=MagicMock(),  # context-manager protocol provided by MagicMock
        ), patch(
            "pickle.load",
            side_effect=load_seq,
        ):
            result, meta = WalkForwardEngine._maybe_apply_ensemble(
                current_predictions=current,
                current_dataset=object(),
                prior_model_paths=("/tmp/m1.pkl", "/tmp/m2.pkl"),
                ensemble_window=3,
                current_fold_index=3,
            )

        self.assertTrue(meta["used"])
        self.assertEqual(meta["window"], 3)
        self.assertEqual(meta["n_models"], 3)
        self.assertEqual(meta["prior_models_attempted"], 2)
        self.assertEqual(meta["prior_models_loaded"], 2)
        self.assertEqual(meta["contributing_folds"], [1, 2, 3])

        # Average of 0.6 + 0.3 + 0.0 = 0.3.
        self.assertAlmostEqual(float(result.iloc[0]), 0.3)
        self.assertAlmostEqual(float(result.iloc[1]), 0.3)
        # ``predict`` was called with the current dataset and the
        # canonical "test" segment — same as the trainer wrote.
        for prior in (prior1, prior2):
            prior.predict.assert_called_once()
            args, _ = prior.predict.call_args
            self.assertEqual(args[1], "test")

    def test_only_recent_priors_loaded_when_history_exceeds_window(self) -> None:
        """``ensemble_window=2`` with 4 priors must only load the most
        recent 1 (= window-1)."""
        current = self._build_predictions(0.4)
        prior_pred = self._build_predictions(0.2)
        prior = MagicMock()
        prior.predict.return_value = prior_pred

        with patch("builtins.open", new=MagicMock()), patch(
            "pickle.load", return_value=prior,
        ):
            result, meta = WalkForwardEngine._maybe_apply_ensemble(
                current_predictions=current,
                current_dataset=object(),
                prior_model_paths=(
                    "/tmp/m0.pkl", "/tmp/m1.pkl",
                    "/tmp/m2.pkl", "/tmp/m3.pkl",
                ),
                ensemble_window=2,
                current_fold_index=4,
            )
        self.assertEqual(meta["n_models"], 2)
        self.assertEqual(meta["prior_models_attempted"], 1)
        # Most-recent prior is fold 3 (index 4 - 1).
        self.assertEqual(meta["contributing_folds"], [3, 4])
        # Average of 0.4 + 0.2 = 0.3.
        self.assertAlmostEqual(float(result.iloc[0]), 0.3)

    def test_partial_prior_failure_skipped_not_aborted(self) -> None:
        """A single corrupted pickle must not abort the run — it should
        be logged and skipped, with the meta block reflecting the gap."""
        current = self._build_predictions(0.6)
        good_pred = self._build_predictions(0.0)
        good = MagicMock()
        good.predict.return_value = good_pred

        # First load raises (bad pickle), second succeeds.
        load_seq = [RuntimeError("corrupt pickle"), good]

        with patch("builtins.open", new=MagicMock()), patch(
            "pickle.load", side_effect=load_seq,
        ):
            result, meta = WalkForwardEngine._maybe_apply_ensemble(
                current_predictions=current,
                current_dataset=object(),
                prior_model_paths=("/tmp/bad.pkl", "/tmp/good.pkl"),
                ensemble_window=3,
                current_fold_index=2,
            )
        self.assertTrue(meta["used"])
        self.assertEqual(meta["prior_models_attempted"], 2)
        self.assertEqual(meta["prior_models_loaded"], 1)
        # Only fold 1 (good) and fold 2 (current) contributed.
        self.assertEqual(meta["contributing_folds"], [1, 2])
        # Average of 0.6 + 0.0 = 0.3.
        self.assertAlmostEqual(float(result.iloc[0]), 0.3)

    def test_all_priors_fail_falls_back_to_current(self) -> None:
        """Every prior pickle is corrupt → return current unchanged with
        meta showing 0 loaded; no exception escapes."""
        current = self._build_predictions(0.7)

        with patch("builtins.open", new=MagicMock()), patch(
            "pickle.load",
            side_effect=[RuntimeError("bad"), RuntimeError("bad")],
        ):
            result, meta = WalkForwardEngine._maybe_apply_ensemble(
                current_predictions=current,
                current_dataset=object(),
                prior_model_paths=("/tmp/m0.pkl", "/tmp/m1.pkl"),
                ensemble_window=3,
                current_fold_index=2,
            )
        self.assertIs(result, current)
        self.assertFalse(meta["used"])
        self.assertEqual(meta["prior_models_attempted"], 2)
        self.assertEqual(meta["prior_models_loaded"], 0)
        # When fallback fires the contributing-fold list stays at
        # current-only.
        self.assertEqual(meta["contributing_folds"], [2])

    def test_prior_index_mismatch_is_rejected_from_ensemble(self) -> None:
        import pandas as pd

        current = self._build_predictions(0.6)
        mismatched_idx = pd.MultiIndex.from_tuples(
            [("2024-10-02", "SH600000"), ("2024-10-02", "SH600001")],
            names=("datetime", "instrument"),
        )
        prior = MagicMock()
        prior.predict.return_value = pd.Series([0.0, 0.0], index=mismatched_idx)

        with patch("builtins.open", new=MagicMock()), patch(
            "pickle.load", return_value=prior,
        ):
            result, meta = WalkForwardEngine._maybe_apply_ensemble(
                current_predictions=current,
                current_dataset=object(),
                prior_model_paths=((1, "/tmp/m1.pkl"),),
                ensemble_window=3,
                current_fold_index=2,
            )

        self.assertIs(result, current)
        self.assertFalse(meta["used"])
        self.assertEqual(meta["prior_models_loaded"], 0)
        self.assertEqual(meta["prior_models_index_mismatched"], 1)
        self.assertEqual(meta["contributing_folds"], [2])
        self.assertEqual(meta["rejected_priors"][0]["reason"], "index_mismatch")


class EnsembleEndToEndFlowTests(unittest.TestCase):
    """Confirm ``run()`` accumulates ``prior_model_paths`` across folds,
    threads them into ``_run_single_fold``, and that the ensemble block
    lands on the per-fold report.
    """

    def test_run_threads_prior_model_paths_in_chronological_order(self) -> None:
        """After fold N, the engine must call fold N+1 with N pickle
        paths in order (``model_fold0.pkl, model_fold1.pkl, ...``)."""
        import tempfile

        from src.core.walk_forward import WalkForwardFold

        captured_calls: list[dict[str, Any]] = []

        def fake_single_fold(*, prior_model_paths, fold_index, **kwargs):
            captured_calls.append({
                "fold_index": fold_index,
                "prior_model_paths": tuple(prior_model_paths),
            })
            return WalkForwardFold(
                fold_index=fold_index,
                train_period=f"{kwargs['train_start']} ~ {kwargs['train_end']}",
                valid_period=f"{kwargs['valid_start']} ~ {kwargs['valid_end']}",
                test_period=f"{kwargs['test_start']} ~ {kwargs['test_end']}",
                ic_1d=0.01, ic_5d=0.02,
                annualized_return=0.05, max_drawdown=-0.05,
                information_ratio=0.5,
                prediction_shape=(100,),
            )

        # Use tmp_path for output_dir so PR4's per-fold manifest writes
        # don't survive across test runs and trigger the resume logic
        # on the next invocation (which would skip the fold loop
        # entirely → empty captured_calls).
        with tempfile.TemporaryDirectory() as tmp:
            config = WalkForwardConfig(
                overall_start="2022-01-01",
                overall_end="2025-06-30",
                train_months=24,
                valid_months=3,
                test_months=3,
                step_months=3,
                ensemble_window=3,
                output_dir=str(tmp),
            )

            with patch(
                "src.core.walk_forward.engine.is_canonical_qlib_initialized",
                return_value=True,
            ), patch.object(
                WalkForwardEngine, "_run_single_fold",
                side_effect=fake_single_fold,
            ), patch(
                "src.core.walk_forward.engine.WalkForwardEngine._write_aggregate_report"
            ):
                WalkForwardEngine.run(config)

        # Fold 0 sees no priors.
        self.assertGreaterEqual(len(captured_calls), 2)
        self.assertEqual(captured_calls[0]["prior_model_paths"], ())
        # Fold 1 sees fold 0's pickle.
        self.assertEqual(len(captured_calls[1]["prior_model_paths"]), 1)
        self.assertTrue(
            captured_calls[1]["prior_model_paths"][0][1].endswith("model_fold0.pkl")
        )
        # Fold 2 (if it exists) sees fold 0 and fold 1, in order.
        if len(captured_calls) >= 3:
            paths = captured_calls[2]["prior_model_paths"]
            self.assertEqual(len(paths), 2)
            self.assertEqual(paths[0][0], 0)
            self.assertEqual(paths[1][0], 1)
            self.assertTrue(paths[0][1].endswith("model_fold0.pkl"))
            self.assertTrue(paths[1][1].endswith("model_fold1.pkl"))

    def test_disk_report_carries_ensemble_block_with_no_op_default(self) -> None:
        """``ensemble_window=1`` — the default — must still emit the
        ``ensemble`` block on disk so a downstream comparison tool sees a
        uniform shape across runs."""
        import json
        import tempfile

        config = WalkForwardConfig()  # ensemble_window=1 by default
        fake_feature_result = MagicMock()
        fake_feature_result.dataset = object()
        fake_model_result = MagicMock(
            predictions="predictions",
            prediction_shape=(10,),
            best_iteration=4,
            final_valid_loss=0.97,
        )

        from src.core.performance_attribution import PerformanceAttributionError

        with tempfile.TemporaryDirectory() as tmp, patch(
            "src.core.walk_forward.engine.FeatureDatasetBuilder.build",
            return_value=fake_feature_result,
        ), patch(
            "src.core.walk_forward.engine.ModelTrainer.train_and_predict",
            return_value=fake_model_result,
        ), patch(
            "src.core.walk_forward.engine.SignalAnalyzer.analyze",
            return_value=_stub_signal_result(),
        ), patch(
            "src.core.walk_forward.engine.BacktestRunner.run",
            return_value=_stub_backtest_output(),
        ), patch(
            "src.core.walk_forward.engine.PerformanceAttribution.analyze",
            side_effect=PerformanceAttributionError("skip for test"),
        ):
            WalkForwardEngine._run_single_fold(
                config=config,
                fold_index=0,
                train_start="2024-01-01", train_end="2024-06-30",
                valid_start="2024-07-01", valid_end="2024-09-30",
                test_start="2024-10-01", test_end="2024-12-31",
                output_dir=Path(tmp),
            )
            with open(Path(tmp) / "fold_00_report.json") as f:
                report = json.load(f)

        self.assertIn("ensemble", report)
        self.assertEqual(report["ensemble"]["window"], 1)
        self.assertFalse(report["ensemble"]["used"])
        self.assertEqual(report["ensemble"]["n_models"], 1)
        self.assertEqual(report["ensemble"]["contributing_folds"], [0])
        self.assertEqual(report["ensemble"]["prior_models_loaded"], 0)
        self.assertTrue(report["ensemble"]["prediction_artifact_path"].endswith(
            "fold_00_predictions.pkl"
        ))
        self.assertRegex(report["ensemble"]["prediction_artifact_sha256"], r"^[0-9a-f]{64}$")


class FoldFailureContinuationTests(unittest.TestCase):
    """Per review #5: a single fold's exception must NOT abort the whole
    walk-forward run. The run continues with a NaN-only placeholder
    fold and the aggregate report still gets written so completed folds'
    work is preserved on disk.
    """

    def test_single_fold_exception_records_nan_placeholder_and_continues(self) -> None:
        """Fold 1 raises mid-run; folds 0 and 2+ should still produce
        valid records, the aggregate JSON should be written, and the
        failed fold should appear with NaN metrics rather than be
        silently dropped from ``num_folds``."""
        import math
        import tempfile

        from src.core.walk_forward import WalkForwardFold

        # Fake ``_run_single_fold``: fold 1 raises, others succeed.
        def fake_single_fold(*, fold_index, train_start, train_end,
                             valid_start, valid_end, test_start, test_end,
                             prior_model_paths, **_):
            if fold_index == 1:
                raise RuntimeError("simulated transient qlib hiccup")
            return WalkForwardFold(
                fold_index=fold_index,
                train_period=f"{train_start} ~ {train_end}",
                valid_period=f"{valid_start} ~ {valid_end}",
                test_period=f"{test_start} ~ {test_end}",
                ic_1d=0.02 + fold_index * 0.001,
                ic_5d=0.04,
                annualized_return=0.10,
                max_drawdown=-0.05,
                information_ratio=1.5,
                prediction_shape=(100,),
            )

        with tempfile.TemporaryDirectory() as tmp, patch(
            "src.core.walk_forward.engine.is_canonical_qlib_initialized",
            return_value=True,
        ), patch.object(
            WalkForwardEngine, "_run_single_fold",
            side_effect=fake_single_fold,
        ):
            config = WalkForwardConfig(
                overall_start="2022-01-01",
                overall_end="2025-06-30",
                train_months=24,
                valid_months=3,
                test_months=3,
                step_months=3,
                output_dir=tmp,
            )
            result = WalkForwardEngine.run(config)

        # Run completes — aggregate report exists and num_folds counts
        # the placeholder.
        self.assertIsNotNone(result.report_path)
        self.assertEqual(len(result.folds), result.num_folds)

        # Fold 1 must appear with NaN metrics (placeholder), not absent.
        fold1 = result.folds[1]
        self.assertEqual(fold1.fold_index, 1)
        self.assertTrue(math.isnan(fold1.ic_1d))
        self.assertTrue(math.isnan(fold1.annualized_return))

        # Aggregate ``valid_folds_*`` excludes the placeholder.
        agg = result.aggregate_metrics
        self.assertEqual(
            int(agg["valid_folds_ic_1d"]), len(result.folds) - 1,
            "Failed fold's NaN must drop out of valid_folds count",
        )
        self.assertEqual(int(agg["num_folds"]), len(result.folds))

    def test_failed_fold_does_not_pollute_subsequent_ensemble(self) -> None:
        """When ``ensemble_window > 1``, the failed fold's pickle path
        must NOT be appended to ``prior_model_paths`` (the model
        wasn't trained, the file is missing or partial). Subsequent
        folds' ensemble windows should skip over it."""
        import tempfile

        from src.core.walk_forward import WalkForwardFold

        captured_priors: list[tuple[tuple[int, str], ...]] = []

        def fake_single_fold(*, fold_index, train_start, train_end,
                             valid_start, valid_end, test_start, test_end,
                             prior_model_paths, **_):
            captured_priors.append(tuple(prior_model_paths))
            if fold_index == 1:
                raise RuntimeError("fold 1 boom")
            return WalkForwardFold(
                fold_index=fold_index,
                train_period=f"{train_start} ~ {train_end}",
                valid_period=f"{valid_start} ~ {valid_end}",
                test_period=f"{test_start} ~ {test_end}",
                ic_1d=0.01, ic_5d=0.02,
                annualized_return=0.05, max_drawdown=-0.05,
                information_ratio=0.5,
                prediction_shape=(100,),
            )

        with tempfile.TemporaryDirectory() as tmp, patch(
            "src.core.walk_forward.engine.is_canonical_qlib_initialized",
            return_value=True,
        ), patch.object(
            WalkForwardEngine, "_run_single_fold",
            side_effect=fake_single_fold,
        ):
            config = WalkForwardConfig(
                overall_start="2022-01-01",
                overall_end="2025-06-30",
                train_months=24,
                valid_months=3,
                test_months=3,
                step_months=3,
                ensemble_window=3,
                output_dir=tmp,
            )
            WalkForwardEngine.run(config)

        # Fold 0: no priors. Fold 1: 1 prior (fold 0). Fold 2: still
        # only fold 0 — fold 1 failed and its pickle path is NOT
        # appended. Fold 3: folds 0 and 2.
        self.assertGreaterEqual(len(captured_priors), 4)
        self.assertEqual(len(captured_priors[0]), 0)  # fold 0
        self.assertEqual(len(captured_priors[1]), 1)  # fold 1 sees fold 0
        self.assertEqual(captured_priors[1][0][0], 0)
        self.assertTrue(captured_priors[1][0][1].endswith("model_fold0.pkl"))
        # Fold 2 still sees only fold 0 (fold 1's path was NOT appended).
        self.assertEqual(len(captured_priors[2]), 1)
        self.assertEqual(captured_priors[2][0][0], 0)
        self.assertTrue(captured_priors[2][0][1].endswith("model_fold0.pkl"))
        # Fold 3 sees fold 0 and fold 2 (fold 1 still skipped).
        self.assertEqual(len(captured_priors[3]), 2)
        self.assertEqual(captured_priors[3][0][0], 0)
        self.assertEqual(captured_priors[3][1][0], 2)
        self.assertTrue(captured_priors[3][0][1].endswith("model_fold0.pkl"))
        self.assertTrue(captured_priors[3][1][1].endswith("model_fold2.pkl"))


class CLIUnknownConfigKeyTests(unittest.TestCase):
    """``scripts/run_walk_forward.py`` must hard-fail on unknown YAML
    keys instead of warning + silently dropping them. The previous
    behaviour masked typos like ``top_k`` (no effect — ``topk`` stays
    at default 50) and ``ensemble_window_size`` (vs the real field
    ``ensemble_window``)."""

    def test_unknown_key_raises_value_error(self) -> None:
        import tempfile
        import textwrap

        from scripts.run_walk_forward import _load_config

        yaml_text = textwrap.dedent("""\
            provider_uri: D:/qlib_data/my_cn_data
            region: cn
            top_k: 100  # typo — real field is ``topk``
            overall_start: '2024-01-01'
            overall_end: '2025-12-31'
        """)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_text)
            config_path = f.name

        try:
            with self.assertRaisesRegex(ValueError, "top_k"):
                _load_config(config_path)
        finally:
            Path(config_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
