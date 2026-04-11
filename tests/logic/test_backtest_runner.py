"""Unit tests for BacktestRunner."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.canonical_backtest_contract import (
    ADJUST_MODE_PRE,
    CanonicalAccountConfig,
    CanonicalBacktestContractError,
    CanonicalBacktestInput,
    CanonicalExchangeConfig,
    CanonicalExchangeCostModel,
    EXECUTION_PRICE_CLOSE,
)
from src.core.backtest_runner import BacktestRunner, BacktestRunnerError


def _make_request(**overrides) -> CanonicalBacktestInput:
    defaults = dict(
        predictions_ref="model_v1",
        evaluation_start="2025-10-01",
        evaluation_end="2025-12-31",
        account_config=CanonicalAccountConfig(init_cash=100_000_000),
        exchange_config=CanonicalExchangeConfig(
            freq="day",
            execution_price_kind=EXECUTION_PRICE_CLOSE,
            cost_model=CanonicalExchangeCostModel(
                commission_rate=0.0005,
                stamp_tax_bps=10.0,
                slippage_bps=5.0,
                min_cost=5.0,
            ),
        ),
        adjust_mode=ADJUST_MODE_PRE,
        signal_to_execution_lag=1,
        benchmark_code="SH000300",
    )
    defaults.update(overrides)
    return CanonicalBacktestInput(**defaults)


class BacktestRunnerStructuralTests(unittest.TestCase):
    """Structural validation — no qlib needed."""

    def test_empty_predictions_rejected(self) -> None:
        with self.assertRaisesRegex(BacktestRunnerError, "predictions"):
            BacktestRunner.run(
                request=_make_request(),
                predictions=None,
            )

    def test_invalid_input_rejected_by_contract(self) -> None:
        with self.assertRaises(CanonicalBacktestContractError):
            BacktestRunner.run(
                request=_make_request(predictions_ref=""),
                predictions="dummy",
            )

    def test_zero_lag_rejected_by_contract(self) -> None:
        with self.assertRaises(CanonicalBacktestContractError):
            BacktestRunner.run(
                request=_make_request(signal_to_execution_lag=0),
                predictions="dummy",
            )

    def test_experimental_controls_rejected(self) -> None:
        with self.assertRaises(CanonicalBacktestContractError):
            BacktestRunner.run(
                request=_make_request(experimental_controls={"key": "val"}),
                predictions="dummy",
            )


_QLIB_DATA_DIR = Path(r"D:/qlib_data/my_cn_data")
_HAS_QLIB_DATA = _QLIB_DATA_DIR.exists() and (_QLIB_DATA_DIR / "calendars").exists()


@unittest.skipUnless(_HAS_QLIB_DATA, "qlib data bundle not available")
class BacktestRunnerE2ETests(unittest.TestCase):
    """E2E tests that require real qlib data + trained model."""

    _predictions = None

    @classmethod
    def setUpClass(cls) -> None:
        from src.core.qlib_runtime import (
            QlibRuntimeConfig,
            init_qlib_canonical,
            is_canonical_qlib_initialized,
        )
        if not is_canonical_qlib_initialized():
            init_qlib_canonical(QlibRuntimeConfig(
                provider_uri=str(_QLIB_DATA_DIR), region="cn",
            ))

        from src.data.feature_dataset_builder import (
            FeatureDatasetBuilder,
            FeatureDatasetConfig,
        )
        from src.core.model_trainer import ModelTrainer, ModelTrainConfig
        import tempfile

        ds_result = FeatureDatasetBuilder.build(FeatureDatasetConfig(
            instruments="csi300",
            feature_handler="Alpha158",
            train_start="2024-01-01", train_end="2025-06-30",
            valid_start="2025-07-01", valid_end="2025-09-30",
            test_start="2025-10-01", test_end="2025-12-31",
        ))

        tmp = tempfile.mkdtemp()
        model_result = ModelTrainer.train_and_predict(
            config=ModelTrainConfig(model_type="LGBModel", num_boost_round=20, early_stopping_rounds=5),
            dataset=ds_result.dataset,
            model_artifact_path=str(Path(tmp) / "model.pkl"),
        )
        cls._predictions = model_result.predictions

    def test_canonical_backtest_runs_successfully(self) -> None:
        # Use SH600000 as benchmark since index data (SH000300) is not
        # in the local data bundle.
        output = BacktestRunner.run(
            request=_make_request(benchmark_code="SH600000"),
            predictions=self._predictions,
            topk=30,
            n_drop=3,
        )
        self.assertEqual(output.metric_status, "official")
        self.assertEqual(output.official_backtest_path, "qlib.backtest.backtest")
        self.assertIn("excess_return_without_cost", output.risk_analysis)
        self.assertIn("excess_return_with_cost", output.risk_analysis)
        self.assertIn("return", output.return_series)
        self.assertIn("config_fingerprint", output.provenance)
        self.assertGreater(output.report["total_days"], 0)


if __name__ == "__main__":
    unittest.main()
