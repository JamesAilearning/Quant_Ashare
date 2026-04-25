"""Tests for src.core.signal_analyzer — IC/IR signal quality analysis."""

import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import numpy as np

from src.core.signal_analyzer import (
    SignalAnalysisConfig,
    SignalAnalysisResult,
    SignalAnalyzer,
    SignalAnalyzerError,
)


class SignalAnalyzerValidationTests(unittest.TestCase):
    """Unit tests that do NOT require qlib (validation logic only)."""

    def test_rejects_when_qlib_not_initialized(self):
        predictions = pd.Series([1.0], index=pd.MultiIndex.from_tuples(
            [("2025-01-01", "SH600000")], names=["datetime", "instrument"]
        ))
        with patch("src.core.signal_analyzer.is_canonical_qlib_initialized", return_value=False):
            with self.assertRaises(SignalAnalyzerError):
                SignalAnalyzer.analyze(predictions)

    def test_rejects_non_series(self):
        with patch("src.core.signal_analyzer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaises(SignalAnalyzerError):
                SignalAnalyzer.analyze([1, 2, 3])

    def test_rejects_non_multiindex(self):
        predictions = pd.Series([1.0, 2.0], index=[0, 1])
        with patch("src.core.signal_analyzer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaises(SignalAnalyzerError):
                SignalAnalyzer.analyze(predictions)

    def test_rejects_empty_series(self):
        predictions = pd.Series(dtype=float, index=pd.MultiIndex.from_tuples(
            [], names=["datetime", "instrument"]
        ))
        with patch("src.core.signal_analyzer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaises(SignalAnalyzerError):
                SignalAnalyzer.analyze(predictions)

    def test_rejects_invalid_ic_method(self):
        predictions = pd.Series([1.0], index=pd.MultiIndex.from_tuples(
            [("2025-01-01", "SH600000")], names=["datetime", "instrument"]
        ))
        with patch("src.core.signal_analyzer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaises(SignalAnalyzerError):
                SignalAnalyzer.analyze(predictions, config=SignalAnalysisConfig(ic_method="invalid"))

    def test_rejects_empty_forward_periods(self):
        predictions = pd.Series([1.0], index=pd.MultiIndex.from_tuples(
            [("2025-01-01", "SH600000")], names=["datetime", "instrument"]
        ))
        with patch("src.core.signal_analyzer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(SignalAnalyzerError, "forward_periods"):
                SignalAnalyzer.analyze(
                    predictions, config=SignalAnalysisConfig(forward_periods=()),
                )

    def test_rejects_zero_forward_period(self):
        predictions = pd.Series([1.0], index=pd.MultiIndex.from_tuples(
            [("2025-01-01", "SH600000")], names=["datetime", "instrument"]
        ))
        with patch("src.core.signal_analyzer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(SignalAnalyzerError, "positive int"):
                SignalAnalyzer.analyze(
                    predictions, config=SignalAnalysisConfig(forward_periods=(0, 5)),
                )

    def test_rejects_negative_forward_period(self):
        predictions = pd.Series([1.0], index=pd.MultiIndex.from_tuples(
            [("2025-01-01", "SH600000")], names=["datetime", "instrument"]
        ))
        with patch("src.core.signal_analyzer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(SignalAnalyzerError, "positive int"):
                SignalAnalyzer.analyze(
                    predictions, config=SignalAnalysisConfig(forward_periods=(-1,)),
                )

    def test_rejects_bool_forward_period(self):
        """bool is a subclass of int — must be rejected so True doesn't silently
        act as 1."""
        predictions = pd.Series([1.0], index=pd.MultiIndex.from_tuples(
            [("2025-01-01", "SH600000")], names=["datetime", "instrument"]
        ))
        with patch("src.core.signal_analyzer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(SignalAnalyzerError, "positive int"):
                SignalAnalyzer.analyze(
                    predictions, config=SignalAnalysisConfig(forward_periods=(True,)),
                )

    def test_rejects_non_int_forward_period(self):
        predictions = pd.Series([1.0], index=pd.MultiIndex.from_tuples(
            [("2025-01-01", "SH600000")], names=["datetime", "instrument"]
        ))
        with patch("src.core.signal_analyzer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(SignalAnalyzerError, "positive int"):
                SignalAnalyzer.analyze(
                    predictions, config=SignalAnalysisConfig(forward_periods=(1.5,)),
                )

    def test_default_config(self):
        cfg = SignalAnalysisConfig()
        self.assertEqual(cfg.forward_periods, (1, 5, 10, 20))
        self.assertEqual(cfg.ic_method, "rank")
        self.assertTrue(cfg.compute_turnover)
        self.assertEqual(cfg.topk, 50)


class NaNPropagationTests(unittest.TestCase):
    """Regression guards for P1c / P2c: no more silent 0.0 when data missing.

    Silent zeros used to make ic_summary and ic_decay indistinguishable
    between "noisy model" and "no data at all", poisoning Optuna and
    walk-forward aggregates downstream.
    """

    def test_ic_decay_returns_nan_when_merge_empty(self):
        """No (date, instrument) overlap between predictions and returns
        → ``merged.empty`` at every lag → decay curve must be all-NaN, not
        all-zero."""
        import math
        import pandas as pd

        dates = pd.date_range("2025-01-01", periods=4, freq="D")
        predictions = pd.Series(
            [1.0, 2.0, 3.0, 4.0],
            index=pd.MultiIndex.from_tuples(
                [(d, "A") for d in dates], names=["datetime", "instrument"],
            ),
        )
        # Returns are for a different instrument → inner-join ends up empty.
        returns_data = pd.DataFrame(
            {"close": [10.0, 11.0, 12.0, 13.0]},
            index=pd.MultiIndex.from_tuples(
                [(d, "B") for d in dates], names=["datetime", "instrument"],
            ),
        )

        decay = SignalAnalyzer._compute_ic_decay(
            predictions, returns_data, max_lag=3, method="rank",
        )
        self.assertEqual(len(decay), 3)
        self.assertTrue(
            all(math.isnan(v) for v in decay),
            f"expected all NaN (missing data); got {decay}",
        )

    def test_ic_summary_returns_nan_when_no_valid_cross_section(self):
        """Single-instrument universe → rank IC is NaN on every day →
        ic_summary[period]['mean_ic'] must be NaN, not 0.0.

        ``_fetch_returns`` is the only reason we'd need qlib here, so we
        stub it. Everything downstream is pure pandas.
        """
        import math
        import pandas as pd
        from unittest.mock import patch

        # 6 dates, *only one instrument* per date → every date's group has
        # < 3 rows → compute_ic_for_group returns NaN for every day.
        dates = pd.date_range("2025-01-01", periods=6, freq="D")
        predictions = pd.Series(
            [float(i) for i in range(6)],
            index=pd.MultiIndex.from_tuples(
                [(d, "A") for d in dates], names=["datetime", "instrument"],
            ),
        )
        fake_returns = pd.DataFrame(
            {"close": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]},
            index=pd.MultiIndex.from_tuples(
                [(d, "A") for d in dates], names=["datetime", "instrument"],
            ),
        )

        with patch(
            "src.core.signal_analyzer.is_canonical_qlib_initialized",
            return_value=True,
        ), patch.object(
            SignalAnalyzer, "_fetch_returns", return_value=fake_returns,
        ):
            result = SignalAnalyzer.analyze(
                predictions,
                config=SignalAnalysisConfig(
                    forward_periods=(1,), compute_turnover=False,
                ),
            )

        self.assertIn(1, result.ic_summary)
        self.assertTrue(
            math.isnan(result.ic_summary[1]["mean_ic"]),
            f"mean_ic should be NaN; got {result.ic_summary[1]['mean_ic']!r}",
        )
        self.assertTrue(math.isnan(result.ic_summary[1]["ir"]))
        self.assertTrue(math.isnan(result.ic_summary[1]["std_ic"]))
        self.assertTrue(math.isnan(result.ic_summary[1]["ic_positive_ratio"]))
        self.assertEqual(result.ic_summary[1]["num_days"], 0)


class CalendarWarningTests(unittest.TestCase):
    """_extend_end_trading_days must log WARNING (not silently fallback).

    D is lazily imported inside the staticmethod, so we inject a mock
    qlib.data module into sys.modules before calling the method.
    """

    def _patch_qlib_D(self, calendar_side_effect=None, calendar_return=None):
        """Context manager: inject a fake qlib.data.D into sys.modules."""
        import sys
        from types import ModuleType
        from unittest.mock import MagicMock

        mock_D = MagicMock(name="D")
        if calendar_side_effect is not None:
            mock_D.calendar.side_effect = calendar_side_effect
        elif calendar_return is not None:
            mock_D.calendar.return_value = calendar_return

        mock_qlib_data = ModuleType("qlib.data")
        mock_qlib_data.D = mock_D

        class _Ctx:
            def __enter__(self):
                sys.modules.setdefault("qlib", ModuleType("qlib"))
                sys.modules["qlib.data"] = mock_qlib_data
                return mock_D
            def __exit__(self, *_):
                sys.modules.pop("qlib.data", None)

        return _Ctx()

    def test_api_exception_logs_warning(self):
        """D.calendar raising RuntimeError must produce a WARNING."""
        import pandas as pd
        end_ts = pd.Timestamp("2025-12-31")

        with self._patch_qlib_D(calendar_side_effect=RuntimeError("provider not available")):
            with patch("src.core.signal_analyzer._logger") as mock_logger:
                result = SignalAnalyzer._extend_end_trading_days(end_ts, max_period=5)

        expected_fallback = end_ts + pd.Timedelta(days=5 * 3)
        self.assertEqual(pd.Timestamp(result), expected_fallback)
        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        self.assertIn("D.calendar lookup failed", warning_msg)

    def test_short_calendar_logs_warning(self):
        """Calendar returning fewer days than needed must log a WARNING."""
        import pandas as pd
        end_ts = pd.Timestamp("2025-12-31")
        short_cal = [end_ts + pd.Timedelta(days=i) for i in range(1, 3)]

        with self._patch_qlib_D(calendar_return=short_cal):
            with patch("src.core.signal_analyzer._logger") as mock_logger:
                result = SignalAnalyzer._extend_end_trading_days(end_ts, max_period=5)

        expected_fallback = end_ts + pd.Timedelta(days=5 * 3)
        self.assertEqual(pd.Timestamp(result), expected_fallback)
        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        self.assertIn("qlib calendar returned only", warning_msg)

    def test_happy_path_no_warning(self):
        """Enough calendar days → no warning emitted."""
        import pandas as pd
        end_ts = pd.Timestamp("2025-12-31")
        ample_cal = [end_ts + pd.Timedelta(days=i) for i in range(1, 30)]

        with self._patch_qlib_D(calendar_return=ample_cal):
            with patch("src.core.signal_analyzer._logger") as mock_logger:
                result = SignalAnalyzer._extend_end_trading_days(end_ts, max_period=5)

        self.assertEqual(pd.Timestamp(result), ample_cal[4])
        mock_logger.warning.assert_not_called()


class PipelineDatasetReuseTests(unittest.TestCase):
    """Pipeline.run must pass feature_result.dataset into FactorAnalyzer.analyze."""

    def test_factor_analyzer_receives_dataset_kwarg(self):
        """When run_factor_analysis=True, FactorAnalyzer.analyze must be
        called with dataset=feature_result.dataset (not dataset=None).
        This locks the dataset-reuse path so it can't regress silently."""
        from unittest.mock import MagicMock, patch, call
        import pandas as pd

        # Build minimal mocks for every pipeline dependency.
        fake_dataset = MagicMock(name="DatasetH")
        fake_feature_result = MagicMock()
        fake_feature_result.dataset = fake_dataset
        fake_feature_result.train_shape = (100, 158)
        fake_feature_result.valid_shape = (50, 158)
        fake_feature_result.test_shape = (50, 158)

        fake_predictions = pd.Series(
            [0.1, 0.2],
            index=pd.MultiIndex.from_tuples(
                [("2025-10-01", "SH600000"), ("2025-10-01", "SH600004")],
                names=["datetime", "instrument"],
            ),
        )
        fake_model_result = MagicMock()
        fake_model_result.predictions = fake_predictions
        fake_model_result.prediction_shape = (2,)
        fake_model_result.model_artifact_path = "/tmp/model.pkl"

        fake_backtest_output = MagicMock()
        fake_backtest_output.positions = {}
        fake_backtest_output.return_series = {
            "return": {"2025-10-01": 0.01},
            "bench": {"2025-10-01": 0.005},
            "cost": {"2025-10-01": 0.001},
        }
        fake_backtest_output.risk_analysis = {}
        fake_backtest_output.report = {
            "total_days": 1, "start_date": "2025-10-01",
            "end_date": "2025-10-01", "positions_days": 0,
        }
        fake_backtest_output.provenance = {}
        fake_backtest_output.metric_status = "official"
        fake_backtest_output.official_backtest_path = "qlib.backtest.backtest"

        fake_factor_result = MagicMock()
        fake_factor_result.total_factors = 158
        fake_factor_result.factor_ic_stats = []
        fake_factor_result.ic_decay = {}
        fake_factor_result.correlation_matrix = {}

        fake_signal_result = MagicMock()
        fake_signal_result.ic_summary = {}
        fake_signal_result.ic_decay = []
        fake_signal_result.turnover_stats = {}

        from src.core.pipeline import Pipeline, PipelineConfig
        config = PipelineConfig(
            provider_uri="/tmp/fake_data",
            run_factor_analysis=True,
            run_attribution=False,
        )

        with patch("src.core.pipeline.init_qlib_canonical") as mock_init, \
             patch("src.core.pipeline.is_canonical_qlib_initialized", return_value=True), \
             patch("src.core.pipeline.FeatureDatasetBuilder.build", return_value=fake_feature_result), \
             patch("src.core.pipeline.ModelTrainer.train_and_predict", return_value=fake_model_result), \
             patch("src.core.pipeline.SignalAnalyzer.analyze", return_value=fake_signal_result), \
             patch("src.core.pipeline.SignalAnalyzer.print_report"), \
             patch("src.core.pipeline.BacktestRunner.run", return_value=fake_backtest_output), \
             patch("src.core.pipeline.FactorAnalyzer.analyze", return_value=fake_factor_result) as mock_fa_analyze, \
             patch("src.core.pipeline.FactorAnalyzer.print_report"), \
             patch("src.core.pipeline.ResultVisualizer.generate"):

            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                cfg = PipelineConfig(
                    provider_uri="/tmp/fake_data",
                    output_dir=tmp,
                    run_factor_analysis=True,
                    run_attribution=False,
                    adjust_mode="unadjusted",
                )
                Pipeline.run(cfg)

        runtime_config = mock_init.call_args.args[0]
        self.assertEqual(runtime_config.data_adjust_mode, "unadjusted")

        # FactorAnalyzer.analyze must have been called with dataset= kwarg.
        mock_fa_analyze.assert_called_once()
        _, kwargs = mock_fa_analyze.call_args
        self.assertIn("dataset", kwargs, "FactorAnalyzer.analyze must be called with dataset=...")
        self.assertIs(kwargs["dataset"], fake_dataset)


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
class SignalAnalyzerE2ETests(unittest.TestCase):
    """E2E tests using real qlib data."""

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

        # Generate predictions from a quick model run
        from src.data.feature_dataset_builder import FeatureDatasetBuilder, FeatureDatasetConfig
        from src.core.model_trainer import ModelTrainer, ModelTrainConfig
        import tempfile

        feature_result = FeatureDatasetBuilder.build(FeatureDatasetConfig(
            instruments="csi300",
            feature_handler="Alpha158",
            train_start="2024-01-01",
            train_end="2024-06-30",
            valid_start="2024-07-01",
            valid_end="2024-09-30",
            test_start="2024-10-01",
            test_end="2024-12-31",
        ))

        cls._tmpdir = tempfile.mkdtemp()
        model_path = str(Path(cls._tmpdir) / "model.pkl")
        model_result = ModelTrainer.train_and_predict(
            config=ModelTrainConfig(model_type="LGBModel", num_boost_round=50),
            dataset=feature_result.dataset,
            model_artifact_path=model_path,
        )
        cls.predictions = model_result.predictions

    def test_analyze_produces_valid_result(self):
        result = SignalAnalyzer.analyze(
            self.predictions,
            config=SignalAnalysisConfig(forward_periods=(1, 5), topk=30),
        )
        self.assertIsInstance(result, SignalAnalysisResult)
        self.assertIn(1, result.ic_summary)
        self.assertIn(5, result.ic_summary)
        self.assertGreater(result.ic_summary[1]["num_days"], 0)
        # IC should be a real number (not NaN)
        self.assertFalse(np.isnan(result.ic_summary[1]["mean_ic"]))
        # IC decay should have entries
        self.assertEqual(len(result.ic_decay), 5)  # max(forward_periods)

    def test_turnover_computed(self):
        result = SignalAnalyzer.analyze(
            self.predictions,
            config=SignalAnalysisConfig(forward_periods=(1,), topk=30),
        )
        self.assertIn("mean_turnover", result.turnover_stats)
        self.assertGreater(result.turnover_stats["mean_turnover"], 0.0)

    def test_print_report_does_not_crash(self):
        result = SignalAnalyzer.analyze(
            self.predictions,
            config=SignalAnalysisConfig(forward_periods=(1, 5), topk=30),
        )
        # Should not raise
        SignalAnalyzer.print_report(result)


if __name__ == "__main__":
    unittest.main()
