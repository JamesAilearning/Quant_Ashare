"""Fold 0 regression baseline test.

After every code change that touches the backtest path, this test re-runs
fold 0 through ``BacktestRunner.run`` against a frozen predictions fixture
and asserts that headline metrics have not drifted beyond tolerance.

The fixture is produced once from a known-good walk-forward run (see
``fixtures/README.md``) and committed as a binary artifact.
"""

from __future__ import annotations

import json
import os
import pickle
import unittest
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
PREDICTIONS_FIXTURE = FIXTURES_DIR / "fold0_predictions.pkl"
EXPECTED_METRICS_FIXTURE = FIXTURES_DIR / "fold0_expected_metrics.json"


def _fixtures_available() -> bool:
    return PREDICTIONS_FIXTURE.is_file() and EXPECTED_METRICS_FIXTURE.is_file()


def _e2e_enabled() -> bool:
    return os.environ.get("RUN_E2E", "").strip() in ("1", "true", "yes")


class Fold0RegressionBaselineTests(unittest.TestCase):
    """Re-run fold 0 backtest against frozen predictions and assert
    headline metrics have not drifted.

    Requires qlib runtime + bundle.  Skipped unless both fixture files
    exist *and* ``RUN_E2E=1`` is set.
    """

    def setUp(self) -> None:
        if not _fixtures_available():
            self.skipTest(
                f"Fixtures not found at {FIXTURES_DIR}. "
                "Run a known-good walk-forward to produce them "
                "(see fixtures/README.md)."
            )
        if not _e2e_enabled():
            self.skipTest("RUN_E2E=1 not set — skipping heavy backtest test.")

    @staticmethod
    def _load_fixtures() -> tuple[Any, dict[str, Any]]:
        with open(PREDICTIONS_FIXTURE, "rb") as f:
            predictions = pickle.load(f)
        with open(EXPECTED_METRICS_FIXTURE, encoding="utf-8") as f:
            metrics = json.load(f)
        return predictions, metrics

    def test_fold0_metrics_match_baseline(self) -> None:
        """Run BacktestRunner against frozen predictions, assert within tolerance."""
        predictions, expected = self._load_fixtures()

        # These must match the fold 0 config from the known-good run.
        topk = expected.get("config", {}).get("topk", 50)
        n_drop = expected.get("config", {}).get("n_drop", 5)
        lag = expected.get("config", {}).get("signal_to_execution_lag", 1)
        benchmark = expected.get("config", {}).get("benchmark_code", "SH000300")
        evaluation_start = expected["config"]["test_start"]
        evaluation_end = expected["config"]["test_end"]

        from src.core.backtest_runner import BacktestRunner
        from src.core.canonical_backtest_contract import (
            ADJUST_MODE_PRE,
            CN_STAMP_TAX_SCHEDULE_DEFAULT,
            CanonicalAccountConfig,
            CanonicalBacktestInput,
            CanonicalExchangeConfig,
            CanonicalExchangeCostModel,
        )

        request = CanonicalBacktestInput(
            predictions_ref="regression_fixture",
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            account_config=CanonicalAccountConfig(
                init_cash=100_000_000,
            ),
            exchange_config=CanonicalExchangeConfig(
                freq="day",
                execution_price_kind="close",
                cost_model=CanonicalExchangeCostModel(
                    commission_rate=0.0005,
                    # Migrated from ``stamp_tax_bps=10.0``. The
                    # default schedule applies 10 bps pre-2023-08-28
                    # and 5 bps after, with the runtime computing a
                    # trading-day-weighted scalar when the window
                    # crosses the reform date. If the fixture window
                    # crosses 2023-08-28, the baseline expected
                    # metrics may need a tolerance bump
                    # (annualized_return tolerance from 0.005 to
                    # 0.010). Audit P0-4 / add-stamp-tax-schedule.
                    stamp_tax_schedule=CN_STAMP_TAX_SCHEDULE_DEFAULT,
                    slippage_bps=5.0,
                    min_cost=5.0,
                ),
                limit_threshold=0.095,
            ),
            adjust_mode=ADJUST_MODE_PRE,
            signal_to_execution_lag=lag,
            benchmark_code=benchmark,
        )

        output = BacktestRunner.run(
            request=request,
            predictions=predictions,
            topk=topk,
            n_drop=n_drop,
            compute_baselines=False,
        )

        tolerance = expected.get("tolerance", {})
        risk = output.risk_analysis.get("excess_return_with_cost", {})

        self._assert_within(
            "annualized_return",
            risk.get("annualized_return"),
            expected["metrics"]["annualized_return"],
            tolerance.get("annualized_return_absolute", 0.005),
        )
        self._assert_within(
            "max_drawdown",
            risk.get("max_drawdown"),
            expected["metrics"]["max_drawdown"],
            tolerance.get("max_drawdown_absolute", 0.005),
        )
        self._assert_within(
            "information_ratio",
            risk.get("information_ratio"),
            expected["metrics"]["information_ratio"],
            tolerance.get("information_ratio_absolute", 0.05),
        )

    def _assert_within(
        self,
        label: str,
        actual: float | None,
        expected_value: float,
        tolerance: float,
    ) -> None:
        if actual is None:
            self.fail(f"{label}: got None, expected {expected_value!r}")
        if abs(float(actual) - expected_value) > tolerance:
            self.fail(
                f"{label}: {actual!r} differs from baseline "
                f"{expected_value!r} by more than {tolerance!r}"
            )


class Fold0FixtureSchemaTests(unittest.TestCase):
    """Validate the expected-metrics fixture schema without running a
    full backtest (no qlib required).
    """

    def test_fixture_has_required_top_level_keys(self) -> None:
        if not EXPECTED_METRICS_FIXTURE.is_file():
            self.skipTest("Fixture file not found.")
        with open(EXPECTED_METRICS_FIXTURE, encoding="utf-8") as f:
            data = json.load(f)
        for key in ("config", "metrics", "tolerance"):
            self.assertIn(key, data, f"Fixture missing key: {key}")

    def test_tolerance_values_are_positive(self) -> None:
        if not EXPECTED_METRICS_FIXTURE.is_file():
            self.skipTest("Fixture file not found.")
        with open(EXPECTED_METRICS_FIXTURE, encoding="utf-8") as f:
            data = json.load(f)
        for key, val in data.get("tolerance", {}).items():
            self.assertGreater(
                val, 0,
                f"tolerance.{key} must be > 0; got {val!r}",
            )
