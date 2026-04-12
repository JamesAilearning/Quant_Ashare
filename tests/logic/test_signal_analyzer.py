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

    def test_default_config(self):
        cfg = SignalAnalysisConfig()
        self.assertEqual(cfg.forward_periods, (1, 5, 10, 20))
        self.assertEqual(cfg.ic_method, "rank")
        self.assertTrue(cfg.compute_turnover)
        self.assertEqual(cfg.topk, 50)


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
                provider_uri=str(_QLIB_DATA_DIR), region="cn",
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
