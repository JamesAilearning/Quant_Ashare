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
    ADJUST_MODE_POST,
    ADJUST_MODE_PRE,
    CanonicalAccountConfig,
    CanonicalBacktestContractError,
    CanonicalBacktestInput,
    CanonicalExchangeConfig,
    CanonicalExchangeCostModel,
    EXECUTION_PRICE_CLOSE,
)
from src.core.backtest_runner import (
    BacktestRunner,
    BacktestRunnerError,
    _positions_to_weight_map,
    _risk_analysis_to_flat_dict,
    _series_to_dict,
)
from src.core.qlib_runtime import (
    QlibRuntimeConfig,
    _reset_canonical_qlib_runtime_for_tests,
)


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
    """Structural validation tests; qlib itself does not need to be importable."""

    def setUp(self) -> None:
        _reset_canonical_qlib_runtime_for_tests()

    def tearDown(self) -> None:
        _reset_canonical_qlib_runtime_for_tests()

    def test_empty_predictions_rejected(self) -> None:
        with self.assertRaisesRegex(BacktestRunnerError, "predictions"):
            BacktestRunner.run(
                request=_make_request(),
                predictions=None,
            )

    def test_missing_canonical_init_rejected_before_qlib_import(self) -> None:
        with self.assertRaisesRegex(BacktestRunnerError, "Canonical qlib runtime"):
            BacktestRunner.run(
                request=_make_request(),
                predictions="dummy",
            )

    def test_adjust_mode_mismatch_rejected_before_qlib_import(self) -> None:
        from src.core import qlib_runtime as _rt

        _rt._CANONICAL_CONFIG = QlibRuntimeConfig(
            provider_uri="./fake_provider",
            region="cn",
            data_adjust_mode=ADJUST_MODE_POST,
        )
        _rt._CANONICAL_QLIB_INITIALIZED = True
        with self.assertRaisesRegex(BacktestRunnerError, "adjust_mode"):
            BacktestRunner.run(
                request=_make_request(adjust_mode=ADJUST_MODE_PRE),
                predictions="dummy",
            )

    def test_invalid_input_rejected_by_contract(self) -> None:
        with self.assertRaises(CanonicalBacktestContractError):
            BacktestRunner.run(
                request=_make_request(predictions_ref=""),
                predictions="dummy",
            )

    def test_zero_lag_reaches_canonical_init_guard(self) -> None:
        with self.assertRaisesRegex(BacktestRunnerError, "Canonical qlib runtime"):
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


class SignalLagTests(unittest.TestCase):
    def _predictions(self):
        import pandas as pd

        dates = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"])
        index = pd.MultiIndex.from_product(
            [dates, ["SH600000", "SH600001"]],
            names=["datetime", "instrument"],
        )
        return pd.Series(range(1, 7), index=index, dtype=float)

    def test_lag_zero_is_noop(self) -> None:
        predictions = self._predictions()
        shifted = BacktestRunner._apply_lag(predictions, 0)
        self.assertTrue(shifted.equals(predictions))

    def test_lag_one_delays_one_trading_row_per_instrument(self) -> None:
        import pandas as pd

        predictions = self._predictions()
        shifted = BacktestRunner._apply_lag(predictions, 1)

        self.assertNotIn(("2025-01-02", "SH600000"), {
            (str(dt.date()), inst) for dt, inst in shifted.index
        })
        self.assertEqual(
            float(shifted.loc[(pd.Timestamp("2025-01-03"), "SH600000")]),
            1.0,
        )
        self.assertEqual(
            float(shifted.loc[(pd.Timestamp("2025-01-06"), "SH600001")]),
            4.0,
        )

    def test_lag_two_delays_two_trading_rows_per_instrument(self) -> None:
        import pandas as pd

        predictions = self._predictions()
        shifted = BacktestRunner._apply_lag(predictions, 2)

        self.assertEqual(len(shifted), 2)
        self.assertEqual(
            float(shifted.loc[(pd.Timestamp("2025-01-06"), "SH600000")]),
            1.0,
        )

    def test_non_series_input_raises_loudly(self) -> None:
        """The previous implementation silently fell through and returned
        the input unchanged when ``predictions`` was not a Series — so a
        research script that fed in a list / DataFrame / numpy array
        would think lag was applied while actually getting a no-op
        T-execution backtest. We now raise ``BacktestRunnerError``."""
        from src.core.backtest_runner import BacktestRunnerError

        with self.assertRaisesRegex(BacktestRunnerError, "MultiIndex"):
            BacktestRunner._apply_lag([1, 2, 3], 1)
        with self.assertRaisesRegex(BacktestRunnerError, "MultiIndex"):
            BacktestRunner._apply_lag({"a": 1}, 1)

    def test_single_index_series_raises_loudly(self) -> None:
        """A pandas Series with only a date index (no instrument level)
        cannot be unstacked the way the lag logic needs it. The previous
        implementation silently returned it unchanged and dropped the
        lag; we now refuse."""
        import pandas as pd

        from src.core.backtest_runner import BacktestRunnerError

        single_index = pd.Series(
            [1.0, 2.0, 3.0],
            index=pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"]),
        )
        with self.assertRaisesRegex(BacktestRunnerError, "MultiIndex"):
            BacktestRunner._apply_lag(single_index, 1)

    def test_lag_zero_validates_shape_too(self) -> None:
        """Regression guard: the same-day-execution path (``lag=0``)
        used to short-circuit before the shape check, so a wrong-shape
        DataFrame / list would pass through to qlib silently. Validate
        uniformly across lag values."""
        from src.core.backtest_runner import BacktestRunnerError

        with self.assertRaisesRegex(BacktestRunnerError, "MultiIndex|forward"):
            BacktestRunner._apply_lag([1, 2, 3], 0)

    def test_lag_zero_rejects_swapped_index_names(self) -> None:
        """``(instrument, datetime)`` MultiIndex would silently feed
        instruments to the date axis. Pin the order check at lag=0 too."""
        import pandas as pd
        from src.core.backtest_runner import BacktestRunnerError

        idx = pd.MultiIndex.from_product(
            [["SH600000", "SH600001"], pd.to_datetime(["2025-01-02", "2025-01-03"])],
            names=["instrument", "datetime"],  # swapped order
        )
        swapped = pd.Series([1.0, 2.0, 3.0, 4.0], index=idx)
        with self.assertRaisesRegex(BacktestRunnerError, "names must be"):
            BacktestRunner._apply_lag(swapped, 0)
        with self.assertRaisesRegex(BacktestRunnerError, "names must be"):
            BacktestRunner._apply_lag(swapped, 1)


class BacktestRunnerNDropValidationTests(unittest.TestCase):
    """``BacktestRunner.run`` must reject ``n_drop >= topk`` even when
    callers bypass ``WalkForwardConfig`` / ``PipelineConfig``. Without
    this defence-in-depth check, a research script that calls
    ``BacktestRunner.run(...)`` directly with ``topk=5, n_drop=5``
    would land qlib's ``TopkDropoutStrategy`` in a state that rotates
    out every position and silently returns an empty backtest."""

    def _make_request(self):
        from src.core.canonical_backtest_contract import (
            ADJUST_MODE_PRE,
            EXECUTION_PRICE_CLOSE,
            CanonicalAccountConfig,
            CanonicalBacktestInput,
            CanonicalExchangeConfig,
            CanonicalExchangeCostModel,
        )
        return CanonicalBacktestInput(
            predictions_ref="/tmp/x.pkl",
            evaluation_start="2025-01-01",
            evaluation_end="2025-03-31",
            account_config=CanonicalAccountConfig(init_cash=1_000_000),
            exchange_config=CanonicalExchangeConfig(
                freq="day",
                execution_price_kind=EXECUTION_PRICE_CLOSE,
                cost_model=CanonicalExchangeCostModel(
                    commission_rate=0.0005, stamp_tax_bps=10.0,
                    slippage_bps=5.0, min_cost=5.0,
                ),
                limit_threshold=0.095,
            ),
            adjust_mode=ADJUST_MODE_PRE,
            signal_to_execution_lag=1,
            benchmark_code="SH000300",
        )

    def test_rejects_n_drop_equal_topk(self) -> None:
        from src.core.backtest_runner import BacktestRunner, BacktestRunnerError
        with self.assertRaisesRegex(BacktestRunnerError, "n_drop"):
            BacktestRunner.run(
                request=self._make_request(),
                predictions="dummy",
                topk=5, n_drop=5,
            )

    def test_rejects_negative_n_drop(self) -> None:
        from src.core.backtest_runner import BacktestRunner, BacktestRunnerError
        with self.assertRaisesRegex(BacktestRunnerError, "n_drop"):
            BacktestRunner.run(
                request=self._make_request(),
                predictions="dummy",
                topk=5, n_drop=-1,
            )

    def test_rejects_zero_topk(self) -> None:
        from src.core.backtest_runner import BacktestRunner, BacktestRunnerError
        with self.assertRaisesRegex(BacktestRunnerError, "topk"):
            BacktestRunner.run(
                request=self._make_request(),
                predictions="dummy",
                topk=0, n_drop=0,
            )


class PositionsSerializationTests(unittest.TestCase):
    """Unit tests for the ``_positions_to_weight_map`` helper."""

    def test_empty_input_returns_empty_dict(self) -> None:
        self.assertEqual(_positions_to_weight_map(None), {})
        self.assertEqual(_positions_to_weight_map({}), {})

    def test_extracts_explicit_weight_field(self) -> None:
        import pandas as pd

        class _Pos:
            def __init__(self, d):
                self.position = d

        positions = pd.Series({
            pd.Timestamp("2025-10-01"): _Pos({
                "SH600000": {"amount": 100, "price": 10.0, "weight": 0.4},
                "SH600001": {"amount": 200, "price": 20.0, "weight": 0.6},
                "cash": 0.0,
            }),
        })
        result = _positions_to_weight_map(positions)
        self.assertIn("2025-10-01", result)
        self.assertAlmostEqual(result["2025-10-01"]["SH600000"], 0.4)
        self.assertAlmostEqual(result["2025-10-01"]["SH600001"], 0.6)
        self.assertNotIn("cash", result["2025-10-01"])

    def test_falls_back_to_amount_times_price(self) -> None:
        import pandas as pd

        class _Pos:
            def __init__(self, d):
                self.position = d

        # No 'weight' key — must compute from amount * price / total_value
        positions = pd.Series({
            pd.Timestamp("2025-10-02"): _Pos({
                "SH600000": {"amount": 100, "price": 10.0},  # value = 1000
                "SH600001": {"amount": 100, "price": 30.0},  # value = 3000
                "cash": 0.0,
            }),
        })
        result = _positions_to_weight_map(positions)
        self.assertAlmostEqual(result["2025-10-02"]["SH600000"], 0.25)
        self.assertAlmostEqual(result["2025-10-02"]["SH600001"], 0.75)

    def test_malformed_input_raises(self) -> None:
        """Non-iterable input must raise loudly.

        Previously this test asserted ``{}`` — i.e. it *locked in* the
        silent-swallow behavior that made
        ``BacktestRunner`` → ``Pipeline`` → ``PerformanceAttribution``
        switch to prediction-based attribution under the same metric
        name. The new contract raises ``BacktestRunnerError`` so the
        upstream contract violation surfaces at the boundary.
        """
        with self.assertRaisesRegex(BacktestRunnerError, "not iterable"):
            _positions_to_weight_map("not-a-dict")
        with self.assertRaisesRegex(BacktestRunnerError, "not iterable"):
            _positions_to_weight_map(42)

    def test_items_iteration_failure_raises(self) -> None:
        """If ``.items()`` exists but raises during iteration, surface it."""
        class _Broken:
            def items(self):
                raise RuntimeError("simulated qlib shape change")

        with self.assertRaisesRegex(BacktestRunnerError, "failed to iterate"):
            _positions_to_weight_map(_Broken())

    def test_none_input_returns_empty_without_raising(self) -> None:
        """``None`` is a legitimate "no positions generated" signal
        (e.g. backtest run without ``generate_portfolio_metrics=True``);
        it must NOT raise."""
        self.assertEqual(_positions_to_weight_map(None), {})

    def test_malformed_day_is_logged_not_silently_dropped(self) -> None:
        """A single malformed day must be skipped with a WARNING log —
        the previous bare ``except Exception: continue`` dropped it
        silently, hiding partial data loss."""
        import pandas as pd

        class _Pos:
            def __init__(self, d): self.position = d

        positions = pd.Series({
            pd.Timestamp("2025-10-01"): _Pos("not-a-dict"),  # malformed
            pd.Timestamp("2025-10-02"): _Pos({
                "SH600000": {"amount": 100, "price": 10.0, "weight": 1.0},
                "cash": 0.0,
            }),
        })
        with self.assertLogs("src.core.backtest_runner", level="WARNING") as cm:
            result = _positions_to_weight_map(positions)
        self.assertIn("2025-10-02", result)
        self.assertNotIn("2025-10-01", result)
        joined = "\n".join(cm.output)
        self.assertIn("non-dict position payload", joined)
        self.assertIn("1 of 2 days were skipped", joined)


_QLIB_DATA_DIR = Path(r"D:/qlib_data/my_cn_data")
_HAS_QLIB_DATA = _QLIB_DATA_DIR.exists() and (_QLIB_DATA_DIR / "calendars").exists()


from tests.e2e_guard import skip_unless_e2e

@skip_unless_e2e
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
                provider_uri=str(_QLIB_DATA_DIR),
                region="cn",
                data_adjust_mode=ADJUST_MODE_PRE,
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


class RiskAnalysisNormalizerTests(unittest.TestCase):
    """Regression guards for P2f: ``_risk_analysis_to_flat_dict`` must
    raise on unknown shapes rather than return ``{"raw": str(df)}``.

    The old catch-all turned any future qlib shape change into a
    missing-metrics scenario that downstream consumers
    (``WalkForwardEngine._extract_cost_metrics``) coerced to 0.0 — a
    silent zero-return run. The normalizer now propagates the failure
    as a ``BacktestRunnerError`` so the breakage surfaces at the
    boundary instead of rippling downstream.
    """

    def test_column_oriented_shape(self) -> None:
        """Column-oriented risk_analysis: metrics as columns, index = 'risk'."""
        import pandas as pd
        df = pd.DataFrame(
            {"annualized_return": {"risk": 0.12},
             "information_ratio": {"risk": 1.1},
             "max_drawdown": {"risk": -0.08}}
        )
        flat = _risk_analysis_to_flat_dict(df)
        self.assertAlmostEqual(flat["annualized_return"], 0.12)
        self.assertAlmostEqual(flat["information_ratio"], 1.1)
        self.assertAlmostEqual(flat["max_drawdown"], -0.08)

    def test_row_oriented_shape(self) -> None:
        """Row-oriented risk_analysis: index = metric names, single 'risk' column."""
        import pandas as pd
        df = pd.DataFrame(
            {"risk": {"annualized_return": 0.12,
                      "information_ratio": 1.1,
                      "max_drawdown": -0.08}}
        )
        flat = _risk_analysis_to_flat_dict(df)
        self.assertAlmostEqual(flat["annualized_return"], 0.12)
        self.assertAlmostEqual(flat["max_drawdown"], -0.08)

    def test_raises_on_to_dict_failure(self) -> None:
        """If the input doesn't quack like a DataFrame, raise loudly
        instead of wrapping the failure as ``{"raw": ...}``."""
        class _Broken:
            def to_dict(self):
                raise ValueError("simulated qlib shape change")

        with self.assertRaisesRegex(
            BacktestRunnerError, "shape may have changed"
        ):
            _risk_analysis_to_flat_dict(_Broken())

    def test_no_raw_fallback_key(self) -> None:
        """The normalizer must never produce a ``{"raw": str(df)}``
        envelope — downstream consumers would coerce the empty metrics
        to 0.0 silently."""
        import pandas as pd
        df = pd.DataFrame(
            {"risk": {"annualized_return": 0.1, "max_drawdown": -0.05}}
        )
        flat = _risk_analysis_to_flat_dict(df)
        self.assertNotIn("raw", flat)
        self.assertIn("annualized_return", flat)


class ReturnSeriesNormalizerTests(unittest.TestCase):
    def test_series_to_dict_converts_dates_to_float_values(self) -> None:
        import pandas as pd

        series = pd.Series(
            [0.01, -0.02],
            index=[pd.Timestamp("2026-01-02"), pd.Timestamp("2026-01-05")],
        )
        self.assertEqual(
            _series_to_dict(series, name="return"),
            {"2026-01-02": 0.01, "2026-01-05": -0.02},
        )

    def test_series_to_dict_rejects_non_iterable_shape(self) -> None:
        with self.assertRaisesRegex(BacktestRunnerError, "return_series\\['return'\\]"):
            _series_to_dict(123, name="return")

    def test_series_to_dict_rejects_non_numeric_values_without_raw_fallback(self) -> None:
        import pandas as pd

        series = pd.Series(["not-a-number"], index=[pd.Timestamp("2026-01-02")])
        with self.assertRaisesRegex(BacktestRunnerError, "raw fallback"):
            _series_to_dict(series, name="bench")


class ProvenanceFingerprintTests(unittest.TestCase):
    """``_build_provenance`` must fold qlib runtime config into the
    fingerprint so swapping the data bundle changes the fingerprint
    even when the request and strategy params are identical.

    Without this, two runs against different ``provider_uri`` /
    ``data_adjust_mode`` would produce different official metrics but
    the *same* fingerprint, defeating the comparison-tool's ability to
    distinguish "regressed model" from "different bundle".
    """

    def _make_request(self):
        return CanonicalBacktestInput(
            predictions_ref="/tmp/x.pkl",
            evaluation_start="2025-01-01",
            evaluation_end="2025-03-31",
            account_config=CanonicalAccountConfig(init_cash=1_000_000),
            exchange_config=CanonicalExchangeConfig(
                freq="day",
                execution_price_kind=EXECUTION_PRICE_CLOSE,
                cost_model=CanonicalExchangeCostModel(
                    commission_rate=0.0005, stamp_tax_bps=10.0,
                    slippage_bps=5.0, min_cost=5.0,
                ),
                limit_threshold=0.095,
            ),
            adjust_mode=ADJUST_MODE_PRE,
            signal_to_execution_lag=1,
            benchmark_code="SH000300",
        )

    def test_fingerprint_includes_runtime_config_block(self) -> None:
        """The provenance ``config`` must surface ``runtime`` so a
        downstream diff can see provider_uri / region / data_adjust_mode
        without re-deriving from the fingerprint."""
        runtime_cfg = QlibRuntimeConfig(
            provider_uri="/tmp/bundle_a", region="cn",
            data_adjust_mode=ADJUST_MODE_PRE,
        )
        with patch(
            "src.core.backtest_runner.get_canonical_qlib_config",
            return_value=runtime_cfg,
        ):
            prov = BacktestRunner._build_provenance(
                self._make_request(), topk=50, n_drop=5,
            )
        self.assertIn("runtime", prov["config"])
        # ``QlibRuntimeConfig.__post_init__`` normalises the path
        # (``os.path.normcase`` + ``realpath``); on Windows that turns
        # "/tmp/bundle_a" into something like "d:\\tmp\\bundle_a". We
        # only assert the recognisable suffix is present so the test is
        # OS-agnostic.
        self.assertIn("bundle_a", prov["config"]["runtime"]["provider_uri"])
        self.assertEqual(prov["config"]["runtime"]["region"], "cn")
        self.assertEqual(
            prov["config"]["runtime"]["data_adjust_mode"], ADJUST_MODE_PRE,
        )

    def test_fingerprint_changes_with_provider_uri(self) -> None:
        """Same request, different provider_uri → different fingerprint."""
        request = self._make_request()
        runtime_a = QlibRuntimeConfig(
            provider_uri="/tmp/bundle_a", region="cn",
            data_adjust_mode=ADJUST_MODE_PRE,
        )
        runtime_b = QlibRuntimeConfig(
            provider_uri="/tmp/bundle_b", region="cn",
            data_adjust_mode=ADJUST_MODE_PRE,
        )
        with patch(
            "src.core.backtest_runner.get_canonical_qlib_config",
            return_value=runtime_a,
        ):
            prov_a = BacktestRunner._build_provenance(request, topk=50, n_drop=5)
        with patch(
            "src.core.backtest_runner.get_canonical_qlib_config",
            return_value=runtime_b,
        ):
            prov_b = BacktestRunner._build_provenance(request, topk=50, n_drop=5)
        self.assertNotEqual(
            prov_a["config_fingerprint"], prov_b["config_fingerprint"],
            "Different provider_uri must produce different fingerprint",
        )

    def test_fingerprint_changes_with_data_adjust_mode(self) -> None:
        """Same provider, different adjust_mode → different fingerprint."""
        request = self._make_request()
        runtime_pre = QlibRuntimeConfig(
            provider_uri="/tmp/bundle", region="cn",
            data_adjust_mode=ADJUST_MODE_PRE,
        )
        runtime_post = QlibRuntimeConfig(
            provider_uri="/tmp/bundle", region="cn",
            data_adjust_mode=ADJUST_MODE_POST,
        )
        with patch(
            "src.core.backtest_runner.get_canonical_qlib_config",
            return_value=runtime_pre,
        ):
            prov_pre = BacktestRunner._build_provenance(request, topk=50, n_drop=5)
        with patch(
            "src.core.backtest_runner.get_canonical_qlib_config",
            return_value=runtime_post,
        ):
            prov_post = BacktestRunner._build_provenance(request, topk=50, n_drop=5)
        self.assertNotEqual(
            prov_pre["config_fingerprint"], prov_post["config_fingerprint"],
        )

    def test_fingerprint_handles_uninitialised_runtime_defensively(self) -> None:
        """If ``get_canonical_qlib_config()`` returns ``None`` (e.g. a
        stale-state edge case during shutdown), provenance must still
        produce a valid record rather than crash."""
        with patch(
            "src.core.backtest_runner.get_canonical_qlib_config",
            return_value=None,
        ):
            prov = BacktestRunner._build_provenance(
                self._make_request(), topk=50, n_drop=5,
            )
        self.assertIn("config_fingerprint", prov)
        self.assertEqual(prov["config"]["runtime"], {})


if __name__ == "__main__":
    unittest.main()
