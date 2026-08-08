"""Tests for the shared canonical-request assembly (src/core/_canonical_request.py).

The refactor's contract is EQUIVALENCE: the builder must produce exactly
what each engine's hand-written assembly produced, and — the point of the
change — the two engines must now produce byte-identical requests from
equivalent configs, so an exchange/cost/execution field cannot drift
between them ("two engines, one schema").
"""
from __future__ import annotations

import sys
import unittest
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core._canonical_request import (  # noqa: E402
    build_canonical_request,
    resolve_risk_constraints,
)
from src.core.canonical_backtest_contract import (  # noqa: E402
    CN_STAMP_TAX_SCHEDULE_DEFAULT,
    resolve_stamp_tax_schedule,
)
from src.core.pipeline import PipelineConfig  # noqa: E402
from src.core.risk_constraints import (  # noqa: E402
    MinimalRiskConstraints,
    RiskConstraintMode,
    campaign_risk_constraints_v1,
)
from src.core.walk_forward import WalkForwardConfig  # noqa: E402

_WINDOW = {
    "predictions_ref": "artifacts/model.pkl",
    "evaluation_start": "2024-01-01",
    "evaluation_end": "2024-06-30",
}


class RequestFieldMappingTests(unittest.TestCase):
    """Every config field lands where the hand-written assembly put it."""

    def test_maps_account_exchange_and_execution_fields(self) -> None:
        config = PipelineConfig(
            provider_uri="x",
            init_cash=12_345.0,
            execution_price_kind="close",
            commission_rate=0.0007,
            slippage_bps=7.5,
            min_cost=3.0,
            limit_threshold=0.195,
            signal_to_execution_lag=2,
            benchmark_code="SH000905TR",
        )
        req = build_canonical_request(config, **_WINDOW)

        self.assertEqual(req.predictions_ref, _WINDOW["predictions_ref"])
        self.assertEqual(req.evaluation_start, _WINDOW["evaluation_start"])
        self.assertEqual(req.evaluation_end, _WINDOW["evaluation_end"])
        self.assertEqual(req.account_config.init_cash, 12_345.0)
        self.assertEqual(req.exchange_config.freq, "day")
        self.assertEqual(req.exchange_config.execution_price_kind, "close")
        self.assertEqual(req.exchange_config.limit_threshold, 0.195)
        cost = req.exchange_config.cost_model
        self.assertEqual(cost.commission_rate, 0.0007)
        self.assertEqual(cost.slippage_bps, 7.5)
        self.assertEqual(cost.min_cost, 3.0)
        self.assertEqual(req.adjust_mode, config.adjust_mode)
        self.assertEqual(req.signal_to_execution_lag, 2)
        self.assertEqual(req.benchmark_code, "SH000905TR")

    def test_stamp_tax_schedule_is_resolved_not_passed_raw(self) -> None:
        # None must resolve to the CN default schedule — passing the raw
        # None through would silently drop the 2023-08-28 reform.
        req = build_canonical_request(
            PipelineConfig(provider_uri="x", stamp_tax_schedule=None),
            **_WINDOW,
        )
        self.assertEqual(
            req.exchange_config.cost_model.stamp_tax_schedule,
            resolve_stamp_tax_schedule(None),
        )
        self.assertEqual(
            req.exchange_config.cost_model.stamp_tax_schedule,
            CN_STAMP_TAX_SCHEDULE_DEFAULT,
        )


class TwoEngineIdentityTests(unittest.TestCase):
    """THE reason the builder exists: equivalent configs on the two engines
    now produce byte-identical requests by construction."""

    _SHARED = {
        "init_cash": 5_000_000.0,
        "commission_rate": 0.0003,
        "slippage_bps": 2.5,
        "min_cost": 1.0,
        "limit_threshold": 0.195,
        "signal_to_execution_lag": 3,
        "benchmark_code": "SH000906TR",
        "execution_price_kind": "close",
    }

    def test_identical_requests_from_equivalent_configs(self) -> None:
        pipe = build_canonical_request(
            PipelineConfig(provider_uri="x", **self._SHARED), **_WINDOW)
        walk = build_canonical_request(
            WalkForwardConfig(**self._SHARED), **_WINDOW)
        self.assertEqual(asdict(pipe), asdict(walk))

    def test_engine_specific_arguments_are_the_only_difference(self) -> None:
        pipe = build_canonical_request(
            PipelineConfig(provider_uri="x", **self._SHARED),
            predictions_ref="model.pkl",
            evaluation_start="2024-01-01", evaluation_end="2024-06-30")
        walk = build_canonical_request(
            WalkForwardConfig(**self._SHARED),
            predictions_ref="fold_03_predictions.parquet",
            evaluation_start="2024-07-01", evaluation_end="2024-12-31")
        a, b = asdict(pipe), asdict(walk)
        differing = {k for k in a if a[k] != b[k]}
        self.assertEqual(
            differing,
            {"predictions_ref", "evaluation_start", "evaluation_end"},
            "only the run-shape arguments may differ between engines",
        )


class RiskConstraintsResolutionTests(unittest.TestCase):
    def test_disabled_resolves_to_none(self) -> None:
        self.assertIsNone(
            resolve_risk_constraints(PipelineConfig(provider_uri="x")))

    def test_default_calibration_keeps_p0_1_defaults(self) -> None:
        resolved = resolve_risk_constraints(PipelineConfig(
            provider_uri="x", risk_constraints_enabled=True))
        assert resolved is not None
        baseline = MinimalRiskConstraints()
        # same calibration, only the mode is (re)stamped
        self.assertEqual(
            {k: v for k, v in asdict(resolved).items() if k != "mode"},
            {k: v for k, v in asdict(baseline).items() if k != "mode"},
        )
        self.assertEqual(resolved.mode, RiskConstraintMode.RAISE)

    def test_campaign_v1_calibration_selected_by_config(self) -> None:
        resolved = resolve_risk_constraints(PipelineConfig(
            provider_uri="x", risk_constraints_enabled=True,
            risk_constraints_calibration="campaign_v1"))
        assert resolved is not None
        expected = campaign_risk_constraints_v1()
        self.assertEqual(
            {k: v for k, v in asdict(resolved).items() if k != "mode"},
            {k: v for k, v in asdict(expected).items() if k != "mode"},
        )

    def test_both_engines_resolve_identically(self) -> None:
        for calibration in ("default", "campaign_v1"):
            pipe = resolve_risk_constraints(PipelineConfig(
                provider_uri="x", risk_constraints_enabled=True,
                risk_constraints_calibration=calibration))
            walk = resolve_risk_constraints(WalkForwardConfig(
                risk_constraints_enabled=True,
                risk_constraints_calibration=calibration))
            assert pipe is not None and walk is not None
            self.assertEqual(asdict(pipe), asdict(walk), calibration)


if __name__ == "__main__":
    unittest.main()
