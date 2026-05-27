"""Unit tests for BacktestRunner."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.backtest_runner import (
    BacktestRunner,
    BacktestRunnerError,
    _positions_to_weight_map,
    _risk_analysis_to_flat_dict,
    _series_to_dict,
)
from src.core.canonical_backtest_contract import (
    ADJUST_MODE_POST,
    ADJUST_MODE_PRE,
    CN_STAMP_TAX_SCHEDULE_DEFAULT,
    EXECUTION_PRICE_CLOSE,
    CanonicalAccountConfig,
    CanonicalBacktestContractError,
    CanonicalBacktestInput,
    CanonicalExchangeConfig,
    CanonicalExchangeCostModel,
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
                stamp_tax_schedule=CN_STAMP_TAX_SCHEDULE_DEFAULT,
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

    def test_rejects_duplicate_prediction_index_before_unstack(self) -> None:
        import pandas as pd

        from src.core.backtest_runner import BacktestRunnerError

        idx = pd.MultiIndex.from_tuples(
            [
                (pd.Timestamp("2025-01-02"), "SH600000"),
                (pd.Timestamp("2025-01-02"), "SH600000"),
            ],
            names=["datetime", "instrument"],
        )
        predictions = pd.Series([1.0, 2.0], index=idx)
        with self.assertRaisesRegex(BacktestRunnerError, "unique"):
            BacktestRunner._apply_lag(predictions, 1)


class BacktestRunnerPITAlignmentTests(unittest.TestCase):
    """Phase D.3 wiring — when a PIT provider is supplied,
    ``BacktestRunner.run`` MUST assert the canonical qlib config's
    provider_uri matches. Catches the same operator footgun Phase D.2
    guarded for training: silent survivorship bias on the most
    consequential code path (real money decisions).
    """

    def setUp(self) -> None:
        _reset_canonical_qlib_runtime_for_tests()

    def tearDown(self) -> None:
        _reset_canonical_qlib_runtime_for_tests()

    def _pin_canonical(self, provider_uri: str) -> None:
        from src.core import qlib_runtime as _rt
        _rt._CANONICAL_CONFIG = QlibRuntimeConfig(
            provider_uri=provider_uri,
            region="cn",
            data_adjust_mode=ADJUST_MODE_PRE,
        )
        _rt._CANONICAL_QLIB_INITIALIZED = True

    def _make_pit_provider_stub(self, provider_uri: str) -> object:
        class _Stub:
            pass
        stub = _Stub()
        stub._provider_uri = provider_uri  # type: ignore[attr-defined]
        return stub

    def test_pit_provider_mismatch_raises(self) -> None:
        self._pin_canonical("/qlib/legacy_provider")
        pit = self._make_pit_provider_stub("/qlib/pit_provider")
        with self.assertRaisesRegex(BacktestRunnerError, "provider_uri mismatch"):
            BacktestRunner.run(
                request=_make_request(),
                predictions="dummy",
                pit_provider=pit,
            )

    def test_pit_provider_missing_attr_raises(self) -> None:
        self._pin_canonical("/qlib/pit_provider")

        class _Bare:
            pass

        with self.assertRaisesRegex(BacktestRunnerError, "_provider_uri"):
            BacktestRunner.run(
                request=_make_request(),
                predictions="dummy",
                pit_provider=_Bare(),
            )

    def test_pit_provider_aligned_passes_guard(self) -> None:
        """When provider_uri aligns, the guard passes and the call
        proceeds. We just assert no provider_uri mismatch error fires;
        downstream qlib failures (no real provider on disk) are
        tolerated."""
        from src.core.qlib_runtime import _normalize_provider_uri
        provider_uri = "/qlib/pit_provider"
        self._pin_canonical(provider_uri)
        pit = self._make_pit_provider_stub(_normalize_provider_uri(provider_uri))
        try:
            BacktestRunner.run(
                request=_make_request(),
                predictions="dummy",
                pit_provider=pit,
            )
        except BacktestRunnerError as exc:
            msg = str(exc)
            self.assertNotIn("provider_uri mismatch", msg)
            self.assertNotIn("_provider_uri", msg)


class BacktestRunnerEqualWeightBaselinePITTests(unittest.TestCase):
    """The actual data-path swap in D.3: when a PIT provider is
    supplied, ``_compute_equalweight_baseline`` routes the close-panel
    fetch through ``pit_provider.get_features`` instead of direct
    ``D.features``. Mock the provider to verify routing without a
    running qlib.
    """

    def test_pit_provider_call_args(self) -> None:
        from unittest.mock import MagicMock

        import pandas as pd

        # Predictions: 2 dates × 3 tickers, fixed scores so daily topk=2
        # selects ticker A and B (both dates).
        dates = pd.to_datetime(["2025-10-01", "2025-10-02"])
        tickers = ["SH600519", "SH600087", "SZ000001"]
        idx = pd.MultiIndex.from_product([dates, tickers],
                                          names=["datetime", "instrument"])
        scores = [3.0, 2.0, 1.0, 3.0, 2.0, 1.0]
        predictions = pd.Series(scores, index=idx)

        # Mock PIT provider returns a close panel for the chosen tickers
        close_idx = pd.MultiIndex.from_product(
            [["SH600519", "SH600087"], dates], names=["instrument", "datetime"],
        )
        close = pd.DataFrame({"$close": [10.0, 11.0, 20.0, 21.0]}, index=close_idx)
        pit = MagicMock()
        pit.get_features.return_value = close

        BacktestRunner._compute_equalweight_baseline(
            predictions=predictions, topk=2,
            evaluation_start="2025-10-01", evaluation_end="2025-10-02",
            pit_provider=pit,
        )
        self.assertEqual(pit.get_features.call_count, 1)
        args, kwargs = pit.get_features.call_args
        fields_arg = args[0] if args else kwargs.get("fields")
        self.assertEqual(fields_arg, ["$close"])
        instruments_arg = kwargs.get("instruments")
        self.assertIsNotNone(instruments_arg,
                             "pit_provider.get_features must be called with "
                             "explicit instruments= kwarg")
        # Top-2 by score on both days picks SH600519 (3.0) and SH600087 (2.0)
        self.assertEqual(sorted(instruments_arg),
                         ["SH600087", "SH600519"])

    def test_legacy_path_when_no_pit_provider(self) -> None:
        """``pit_provider=None`` (default) falls through to direct
        qlib.D.features — the existing legacy behaviour.

        Implementation note (PR7): previously this test patched
        ``sys.modules["qlib.data"]`` with a top-level MagicMock,
        hiding the real qlib module entirely. That masked any
        API drift in qlib itself — e.g. a renamed module or moved
        attribute would silently pass. We now require qlib to be
        importable (``pytest.importorskip``) and patch the real
        ``qlib.data.D`` attribute directly, so an import-time
        breakage surfaces here too.
        """
        import pytest

        pytest.importorskip("qlib")
        from unittest.mock import MagicMock
        from unittest.mock import patch as mpatch

        import pandas as pd

        dates = pd.to_datetime(["2025-10-01", "2025-10-02"])
        idx = pd.MultiIndex.from_product(
            [dates, ["SH600519", "SH600087"]],
            names=["datetime", "instrument"],
        )
        predictions = pd.Series([3.0, 2.0, 3.0, 2.0], index=idx)

        close_idx = pd.MultiIndex.from_product(
            [["SH600519", "SH600087"], dates], names=["instrument", "datetime"],
        )
        close = pd.DataFrame({"$close": [10.0, 11.0, 20.0, 21.0]}, index=close_idx)

        mock_D = MagicMock()
        mock_D.features.return_value = close
        # Patch the attribute on the real qlib.data module, not the
        # module itself in sys.modules. The production code's
        # ``from qlib.data import D`` then resolves to this mock at
        # call time without the test having to fake qlib's entire
        # module graph.
        with mpatch("qlib.data.D", mock_D):
            BacktestRunner._compute_equalweight_baseline(
                predictions=predictions, topk=2,
                evaluation_start="2025-10-01", evaluation_end="2025-10-02",
                pit_provider=None,
            )
        self.assertEqual(mock_D.features.call_count, 1)


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
                    commission_rate=0.0005, stamp_tax_schedule=CN_STAMP_TAX_SCHEDULE_DEFAULT,
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


class StampTaxScheduleWarnLoggingTests(unittest.TestCase):
    """``BacktestRunner.run`` MUST emit a WARN log when the
    requested period crosses a stamp-tax schedule transition. The
    converse (period entirely within one segment) must NOT emit
    the WARN. Audit P0-4 / openspec/changes/add-stamp-tax-schedule.

    We patch the canonical qlib runtime + the qlib backtest call to
    side-step the real backtest engine; the WARN is emitted long
    before the qlib call so the patch boundary is sufficient.
    """

    def _make_request(self, *, start: str, end: str):
        from src.core.canonical_backtest_contract import (
            ADJUST_MODE_PRE,
            CN_STAMP_TAX_SCHEDULE_DEFAULT,
            EXECUTION_PRICE_CLOSE,
            CanonicalAccountConfig,
            CanonicalBacktestInput,
            CanonicalExchangeConfig,
            CanonicalExchangeCostModel,
        )
        return CanonicalBacktestInput(
            predictions_ref="/tmp/x.pkl",
            evaluation_start=start,
            evaluation_end=end,
            account_config=CanonicalAccountConfig(init_cash=1_000_000),
            exchange_config=CanonicalExchangeConfig(
                freq="day",
                execution_price_kind=EXECUTION_PRICE_CLOSE,
                cost_model=CanonicalExchangeCostModel(
                    commission_rate=0.0005,
                    stamp_tax_schedule=CN_STAMP_TAX_SCHEDULE_DEFAULT,
                    slippage_bps=5.0, min_cost=5.0,
                ),
                limit_threshold=0.095,
            ),
            adjust_mode=ADJUST_MODE_PRE,
            signal_to_execution_lag=1,
            benchmark_code="SH000300",
        )

    def _make_predictions(self):
        """Minimal pd.Series with the (datetime, instrument)
        MultiIndex shape that BacktestRunner._apply_lag's
        validation accepts."""
        import pandas as pd
        idx = pd.MultiIndex.from_tuples(
            [
                (pd.Timestamp("2022-01-04"), "SH600000"),
                (pd.Timestamp("2022-01-05"), "SH600000"),
            ],
            names=("datetime", "instrument"),
        )
        return pd.Series([0.5, 0.6], index=idx)

    def _run_with_patches(self, request, predictions, logger_records):
        """Drive BacktestRunner.run until it raises after the WARN
        has been emitted. We patch enough boundary calls that the
        WARN log line is reached; the run is then allowed to error
        out (we only care about whether the WARN fired).

        We also patch ``qlib.data`` so the stamp-tax weighter can
        fetch a trading calendar — without this patch the runtime
        now hard-fails on calendar errors (Codex P1 on PR #178)
        rather than falling back to calendar-day weighting, so the
        WARN we're testing for would never be reached.
        """
        import logging
        from unittest.mock import MagicMock, patch

        import pandas as pd

        from src.core.backtest_runner import BacktestRunner, BacktestRunnerError

        # A weekly trading calendar covering the test windows (both
        # the cross-period 2022-2024 case and the single-segment
        # 2024 case). Real qlib would give a daily calendar; weekly
        # is enough to weight the two segments.
        fake_calendar = list(pd.date_range("2020-01-01", "2026-12-31", freq="7D"))
        fake_qlib_data = MagicMock()
        fake_qlib_data.D.calendar.return_value = fake_calendar

        with patch(
            "src.core.backtest_runner.is_canonical_qlib_initialized",
            return_value=True,
        ), patch(
            "src.core.backtest_runner.get_canonical_qlib_config",
        ) as get_cfg, patch.dict(
            "sys.modules",
            {
                "qlib.data": fake_qlib_data,
                "qlib.backtest": MagicMock(backtest=MagicMock(
                    side_effect=RuntimeError("test-stop after WARN"),
                )),
                "qlib.backtest.executor": MagicMock(),
                "qlib.contrib.strategy.signal_strategy": MagicMock(),
                "qlib.utils.time": MagicMock(),
            },
        ):
            from src.core.qlib_runtime import QlibRuntimeConfig
            get_cfg.return_value = QlibRuntimeConfig(
                provider_uri="/tmp/qlib_data",
                region="cn",
                data_adjust_mode="pre_adjusted",
            )
            logger = logging.getLogger("src.core.backtest_runner")
            handler = logging.Handler()
            handler.setLevel(logging.WARNING)

            def emit(record):
                logger_records.append(record)
            handler.emit = emit
            logger.addHandler(handler)
            try:
                with self.assertRaises(BacktestRunnerError):
                    BacktestRunner.run(
                        request=request,
                        predictions=predictions,
                        topk=5, n_drop=1,
                    )
            finally:
                logger.removeHandler(handler)

    def test_cross_period_emits_warn_with_both_rates(self) -> None:
        records: list = []
        request = self._make_request(
            start="2022-01-01", end="2024-12-31",  # crosses 2023-08-28
        )
        self._run_with_patches(request, self._make_predictions(), records)
        warns = [r for r in records if r.levelname == "WARNING"]
        self.assertTrue(warns, "expected at least one WARN; got none")
        msgs = [w.getMessage() for w in warns]
        # Must mention the transition date, the pre/post rates, and
        # the weighted scalar.
        cross_warns = [m for m in msgs if "2023-08-28" in m]
        self.assertTrue(cross_warns, f"no WARN mentions 2023-08-28; got {msgs}")
        msg = cross_warns[0]
        self.assertIn("10.0bps", msg)
        self.assertIn("5.0bps", msg)
        self.assertIn("Audit P0-4", msg)

    def test_single_segment_does_not_emit_stamp_tax_warn(self) -> None:
        records: list = []
        request = self._make_request(
            start="2024-01-01", end="2024-12-31",  # entirely post-reform
        )
        self._run_with_patches(request, self._make_predictions(), records)
        warns = [r for r in records if r.levelname == "WARNING"]
        stamp_warns = [
            w for w in warns
            if "stamp-tax transition" in w.getMessage()
        ]
        self.assertEqual(
            stamp_warns, [],
            f"WARN about stamp-tax transition should NOT fire when "
            f"period is within one segment; got: "
            f"{[w.getMessage() for w in stamp_warns]}",
        )

    def test_calendar_fetch_failure_raises_no_silent_fallback(self) -> None:
        """Codex P1 follow-up on PR #178.

        When ``qlib.data.D.calendar`` raises during stamp-tax
        weighting, the runtime MUST raise ``BacktestRunnerError``
        rather than fall back to calendar-day weighting. Calendar-
        day weighting would produce a different ``close_cost``
        scalar than what qlib's executor charges per sell, silently
        degrading the official metrics — which violates the repo's
        no-silent-fallback rule.
        """
        from unittest.mock import MagicMock, patch

        from src.core.backtest_runner import BacktestRunner, BacktestRunnerError

        # Mock qlib.data so the IMPORT succeeds, but make
        # D.calendar raise — simulates a misconfigured provider or
        # a calendar that cannot be loaded.
        fake_qlib_data = MagicMock()
        fake_qlib_data.D.calendar.side_effect = RuntimeError(
            "simulated provider misconfiguration"
        )

        request = self._make_request(
            start="2022-01-01", end="2024-12-31",  # crosses reform
        )
        predictions = self._make_predictions()

        with patch(
            "src.core.backtest_runner.is_canonical_qlib_initialized",
            return_value=True,
        ), patch(
            "src.core.backtest_runner.get_canonical_qlib_config",
        ) as get_cfg, patch.dict(
            "sys.modules",
            {
                "qlib.data": fake_qlib_data,
                "qlib.backtest": MagicMock(),
                "qlib.backtest.executor": MagicMock(),
                "qlib.contrib.strategy.signal_strategy": MagicMock(),
                "qlib.utils.time": MagicMock(),
            },
        ):
            from src.core.qlib_runtime import QlibRuntimeConfig
            get_cfg.return_value = QlibRuntimeConfig(
                provider_uri="/tmp/qlib_data",
                region="cn",
                data_adjust_mode="pre_adjusted",
            )
            with self.assertRaisesRegex(
                BacktestRunnerError,
                "failed to fetch qlib trading calendar",
            ):
                BacktestRunner.run(
                    request=request,
                    predictions=predictions,
                    topk=5, n_drop=1,
                )


class BacktestRunnerRiskConstraintsKwargTests(unittest.TestCase):
    """Audit P0-1 / openspec/changes/add-minimal-risk-constraints.

    ``BacktestRunner.run`` MUST accept a ``risk_constraints``
    keyword argument that defaults to ``None`` (preserves existing
    callers). A future refactor that removes the kwarg or changes
    its default would break the OpenSpec contract.

    Deep integration (mocking qlib enough to exercise the post-
    trade apply call) is covered by ``test_minimal_risk_constraints``
    on the engine side plus an E2E regression test gated by
    ``RUN_E2E=1`` on the runner side. The lightweight signature
    + default-value test below is the everyday CI guard.
    """

    def test_run_has_risk_constraints_kwarg_with_default_none(self) -> None:
        import inspect

        from src.core.backtest_runner import BacktestRunner

        sig = inspect.signature(BacktestRunner.run)
        self.assertIn(
            "risk_constraints", sig.parameters,
            msg="BacktestRunner.run must accept ``risk_constraints`` "
                "kwarg per audit P0-1; if you removed it, "
                "openspec/changes/add-minimal-risk-constraints needs "
                "to be archived first.",
        )
        param = sig.parameters["risk_constraints"]
        self.assertIs(
            param.default, None,
            msg="``risk_constraints`` default must be None so existing "
                "callers preserve their previous behaviour (with a "
                "WARN about no constraints active).",
        )
        self.assertIs(
            param.kind, inspect.Parameter.KEYWORD_ONLY,
            msg="``risk_constraints`` must be keyword-only — same "
                "convention as the other run() kwargs.",
        )

    def test_canonical_backtest_output_has_positions_clipped(self) -> None:
        """The output dataclass gains a sibling field for the
        constraint-respecting allocation. Default factory must
        produce an empty dict so existing constructions without
        the kwarg keep working. Codex P1 follow-up on PR #179
        renamed this from ``positions_pre_clip`` to
        ``positions_clipped`` and swapped the semantics — see
        the dataclass docstring."""
        from dataclasses import fields

        from src.core.canonical_backtest_contract import CanonicalBacktestOutput

        names = {f.name for f in fields(CanonicalBacktestOutput)}
        self.assertIn("positions_clipped", names)
        # Construct a minimal instance without the new field —
        # default factory must produce empty dict.
        out = CanonicalBacktestOutput(
            metric_status="official",
            official_backtest_path="qlib.backtest.backtest",
            return_series={},
            risk_analysis={},
            report={},
            provenance={},
            positions={},
        )
        self.assertEqual(out.positions_clipped, {})


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

    def test_non_finite_position_fields_do_not_poison_day_weights(self) -> None:
        import pandas as pd

        class _Pos:
            def __init__(self, d):
                self.position = d

        positions = pd.Series({
            pd.Timestamp("2025-10-03"): _Pos({
                "BAD_AMOUNT": {"amount": float("nan"), "price": 10.0},
                "BAD_PRICE": {"amount": 100, "price": float("nan")},
                "GOOD": {"amount": 100, "price": 20.0},
                "BAD_WEIGHT": {"amount": 100, "price": 10.0, "weight": float("nan")},
                "cash": float("nan"),
            }),
        })
        result = _positions_to_weight_map(positions)
        self.assertIn("2025-10-03", result)
        day = result["2025-10-03"]
        self.assertAlmostEqual(day["GOOD"], 2.0 / 3.0)
        self.assertAlmostEqual(day["BAD_AMOUNT"], 0.0)
        self.assertAlmostEqual(day["BAD_PRICE"], 0.0)
        self.assertNotIn("BAD_WEIGHT", day)

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

        import tempfile

        from src.core.model_trainer import ModelTrainConfig, ModelTrainer
        from src.data.feature_dataset_builder import (
            FeatureDatasetBuilder,
            FeatureDatasetConfig,
        )

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
                    commission_rate=0.0005, stamp_tax_schedule=CN_STAMP_TAX_SCHEDULE_DEFAULT,
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


class MicrostructureMaskIntegrationTests(unittest.TestCase):
    """Audit P0-3: BacktestRunner.run drops suspended /
    one-price-lock candidates from predictions before qlib sees
    them. We patch the helper directly to verify integration
    plumbing without standing up a full mocked qlib OHLCV stack.
    """

    def _make_predictions_panel(self) -> object:
        """Build a 3-ticker × 3-day predictions Series with
        sequential scores so the order of remaining rows after
        masking is unambiguous."""
        import pandas as pd

        dates = pd.to_datetime(["2024-03-14", "2024-03-15", "2024-03-16"])
        tickers = ["SH600000", "SZ300001", "SH600519"]
        idx = pd.MultiIndex.from_product(
            [dates, tickers], names=["datetime", "instrument"],
        )
        return pd.Series(
            [9, 8, 7, 6, 5, 4, 3, 2, 1], index=idx, dtype="float64",
        )

    def _make_request(self):
        from src.core.canonical_backtest_contract import (
            ADJUST_MODE_PRE,
            CN_STAMP_TAX_SCHEDULE_DEFAULT,
            EXECUTION_PRICE_CLOSE,
            CanonicalAccountConfig,
            CanonicalBacktestInput,
            CanonicalExchangeConfig,
            CanonicalExchangeCostModel,
        )
        return CanonicalBacktestInput(
            predictions_ref="model.pkl",
            evaluation_start="2024-03-14",
            evaluation_end="2024-03-16",
            account_config=CanonicalAccountConfig(init_cash=100_000_000),
            exchange_config=CanonicalExchangeConfig(
                freq="day",
                execution_price_kind=EXECUTION_PRICE_CLOSE,
                cost_model=CanonicalExchangeCostModel(
                    commission_rate=0.0005,
                    stamp_tax_schedule=CN_STAMP_TAX_SCHEDULE_DEFAULT,
                    slippage_bps=5.0,
                    min_cost=5.0,
                ),
            ),
            adjust_mode=ADJUST_MODE_PRE,
            signal_to_execution_lag=1,
            benchmark_code="SH000300",
        )

    def _drive_until_strategy_construction(
        self,
        mask_pairs: frozenset[tuple[str, str]],
        n_suspended: int = 0,
        n_one_price_days: int = 0,
        logger_records: list | None = None,
    ) -> object:
        """Run BacktestRunner.run with a patched mask helper and
        a patched ``TopkDropoutStrategy`` that records the
        predictions it receives. Stop the run at qlib.backtest
        with a benign exception so we can inspect what reached
        the strategy. Returns the captured predictions Series.
        """
        import logging
        from unittest.mock import MagicMock, patch

        import pandas as pd

        from src.core.backtest_runner import BacktestRunner
        from src.core.microstructure_mask import MicrostructureMaskResult

        captured: dict = {}

        class _CapturingStrategy:
            def __init__(self, signal, topk, n_drop):
                captured["signal"] = signal
                captured["topk"] = topk
                captured["n_drop"] = n_drop

        # Build a 7-day calendar covering 2024-03-14..16 (3 days
        # of data + buffer). compute_effective_stamp_tax_bps needs
        # at least one trading day per segment.
        fake_calendar = list(pd.date_range("2024-03-01", "2024-03-31"))
        fake_qlib_data = MagicMock()
        fake_qlib_data.D.calendar.return_value = fake_calendar

        mask_result = MicrostructureMaskResult(
            masked=mask_pairs,
            n_suspended=n_suspended,
            n_one_price_days=n_one_price_days,
        )

        if logger_records is not None:
            handler = logging.Handler()
            handler.emit = logger_records.append
            handler.setLevel(logging.WARNING)
            logger = logging.getLogger("src.core.backtest_runner")
            logger.addHandler(handler)
            logger.setLevel(logging.WARNING)
        else:
            handler = None
            logger = None

        try:
            with patch(
                "src.core.backtest_runner.is_canonical_qlib_initialized",
                return_value=True,
            ), patch(
                "src.core.backtest_runner.get_canonical_qlib_config",
            ) as get_cfg, patch(
                "src.core.backtest_runner.compute_unavailable_mask",
                return_value=mask_result,
            ), patch.dict(
                "sys.modules",
                {
                    "qlib.data": fake_qlib_data,
                    "qlib.backtest": MagicMock(backtest=MagicMock(
                        side_effect=RuntimeError("test-stop after strategy"),
                    )),
                    "qlib.backtest.executor": MagicMock(),
                    "qlib.contrib.strategy.signal_strategy": MagicMock(
                        TopkDropoutStrategy=_CapturingStrategy,
                    ),
                    "qlib.utils.time": MagicMock(),
                },
            ):
                from src.core.qlib_runtime import QlibRuntimeConfig
                get_cfg.return_value = QlibRuntimeConfig(
                    provider_uri="/tmp/qlib_data",
                    region="cn",
                    data_adjust_mode="pre_adjusted",
                )
                try:
                    BacktestRunner.run(
                        request=self._make_request(),
                        predictions=self._make_predictions_panel(),
                        topk=2, n_drop=1,
                    )
                except Exception:
                    # Run is intentionally stopped at qlib.backtest;
                    # we only need what reached the strategy.
                    pass
        finally:
            if handler is not None and logger is not None:
                logger.removeHandler(handler)

        return captured.get("signal")

    def test_mask_drops_suspended_and_one_price_rows(self) -> None:
        """A mask containing (2024-03-15, SH600000) as suspended
        and (2024-03-15, SZ300001) as one-price-locked. After
        ``_apply_lag(lag=1)`` shifts predictions, the rows the
        strategy sees on those dates MUST exclude those tickers."""
        # Note: ``_apply_lag(lag=1)`` advances the DATE stamps
        # forward, so predictions originally indexed at
        # 2024-03-14/15/16 land at 2024-03-15/16/17 inside the
        # strategy. We mask the EXECUTION dates 2024-03-15.
        mask = frozenset({
            ("2024-03-15", "SH600000"),
            ("2024-03-15", "SZ300001"),
        })
        signal = self._drive_until_strategy_construction(
            mask, n_suspended=1, n_one_price_days=1,
        )
        self.assertIsNotNone(
            signal,
            "Strategy constructor never received a signal — "
            "the run aborted before strategy construction.",
        )
        date_level = signal.index.get_level_values("datetime")
        inst_level = signal.index.get_level_values("instrument")
        observed = {
            (ts.date().isoformat(), str(inst))
            for ts, inst in zip(date_level, inst_level, strict=False)
        }
        # The two masked rows MUST NOT appear in the signal.
        self.assertNotIn(("2024-03-15", "SH600000"), observed)
        self.assertNotIn(("2024-03-15", "SZ300001"), observed)
        # Other (date, instrument) combos that were NOT masked
        # MUST still be present.
        self.assertIn(("2024-03-15", "SH600519"), observed)

    def test_empty_mask_leaves_predictions_intact(self) -> None:
        """No suspended / one-price days → predictions reach
        qlib unchanged, no WARN about masking is emitted."""
        records: list = []
        signal = self._drive_until_strategy_construction(
            frozenset(), n_suspended=0, n_one_price_days=0,
            logger_records=records,
        )
        # _apply_lag(lag=1) drops the first row when there is no
        # source — 3 source dates × 3 tickers shifts to 2 shifted
        # dates × 3 tickers after stack().dropna(). So 6 rows
        # reach the strategy.
        self.assertEqual(len(signal), 6)
        # No microstructure-mask WARN should fire.
        msgs = [r.getMessage() for r in records if r.levelno >= 30]
        mask_warns = [m for m in msgs if "microstructure mask" in m]
        self.assertEqual(
            mask_warns, [],
            f"Empty mask should not produce a WARN; got {mask_warns}",
        )

    def test_warn_summarises_per_regime_counts(self) -> None:
        """When the mask is non-empty, the run emits exactly one
        WARN containing the per-regime counts AND audit P0-3
        attribution. Operators tailing the log can immediately see
        the magnitude of the silent-fill correction."""
        records: list = []
        mask = frozenset({
            ("2024-03-15", "SH600000"),
            ("2024-03-15", "SZ300001"),
            ("2024-03-16", "SH600519"),
        })
        self._drive_until_strategy_construction(
            mask, n_suspended=2, n_one_price_days=1,
            logger_records=records,
        )
        msgs = [r.getMessage() for r in records if r.levelno >= 30]
        mask_warns = [m for m in msgs if "microstructure mask" in m]
        self.assertEqual(
            len(mask_warns), 1,
            f"Expected exactly 1 mask WARN; got {len(mask_warns)}: "
            f"{mask_warns}",
        )
        msg = mask_warns[0]
        self.assertIn("3", msg)  # total_masked
        self.assertIn("2", msg)  # n_suspended
        self.assertIn("1", msg)  # n_one_price_days
        self.assertIn("Audit P0-3", msg)


if __name__ == "__main__":
    unittest.main()
