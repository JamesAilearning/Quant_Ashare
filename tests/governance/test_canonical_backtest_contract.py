import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.canonical_boundaries import (
    EXPERIMENTAL_RUNTIME_LAYER,
    RESEARCH_FACTOR_LAB_LAYER,
)
from src.core.canonical_backtest_contract import (
    ADJUST_MODE_POST,
    ADJUST_MODE_PRE,
    CANONICAL_INPUT_REQUIRED_FIELDS,
    CANONICAL_OFFICIAL_BACKTEST_PATH,
    CANONICAL_OUTPUT_FIELDS,
    COMMISSION_RATE_MAX,
    EXECUTION_PRICE_CLOSE,
    CanonicalAccountConfig,
    CanonicalBacktestContract,
    CanonicalBacktestContractError,
    CanonicalBacktestInput,
    CanonicalExchangeConfig,
    CanonicalExchangeCostModel,
)


def _valid_cost_model(**overrides) -> CanonicalExchangeCostModel:
    payload = {
        "commission_rate": 0.0003,
        "stamp_tax_bps": 10.0,
        "slippage_bps": 5.0,
        "min_cost": 5.0,
    }
    payload.update(overrides)
    return CanonicalExchangeCostModel(**payload)


def _valid_exchange_config(**overrides) -> CanonicalExchangeConfig:
    payload = {
        "freq": "day",
        "execution_price_kind": EXECUTION_PRICE_CLOSE,
        "cost_model": _valid_cost_model(),
    }
    payload.update(overrides)
    return CanonicalExchangeConfig(**payload)


def _valid_request(**overrides) -> CanonicalBacktestInput:
    payload = {
        "predictions_ref": "predictions://run-001",
        "evaluation_start": "2025-01-01",
        "evaluation_end": "2025-12-31",
        "account_config": CanonicalAccountConfig(init_cash=1_000_000.0),
        "exchange_config": _valid_exchange_config(),
        "adjust_mode": ADJUST_MODE_PRE,
        "signal_to_execution_lag": 1,
        "benchmark_code": "SH000300",
    }
    payload.update(overrides)
    return CanonicalBacktestInput(**payload)


class CanonicalBacktestContractTests(unittest.TestCase):
    def test_single_canonical_official_metrics_path(self):
        self.assertEqual(
            CanonicalBacktestContract.list_official_paths(),
            (CANONICAL_OFFICIAL_BACKTEST_PATH,),
        )

    def test_canonical_output_schema_is_stable(self):
        self.assertEqual(
            CanonicalBacktestContract.output_schema(),
            CANONICAL_OUTPUT_FIELDS,
        )

    def test_valid_request_passes_validation(self):
        # Positive-path sanity check: a well-formed strict request must not raise.
        CanonicalBacktestContract.validate_input(_valid_request())

    def test_no_implicit_fallback_allowed(self):
        # ``allow_implicit_fallback=True`` is now rejected at construction
        # time, not at validate_input. The previous "construct, then
        # validate, then reject" sequence was a misnomer trap — callers
        # could build and pass around an object that the contract was
        # going to refuse anyway, deferring the failure. Now the
        # forbidden flag fails fast.
        with self.assertRaisesRegex(
            CanonicalBacktestContractError, "allow_implicit_fallback"
        ):
            _valid_request(allow_implicit_fallback=True)

    def test_experimental_layer_rejected_by_canonical_contract(self):
        req = _valid_request(source_layer=EXPERIMENTAL_RUNTIME_LAYER)
        with self.assertRaisesRegex(CanonicalBacktestContractError, "accepts layer"):
            CanonicalBacktestContract.validate_input(req)

    def test_research_layer_rejected_by_canonical_contract(self):
        req = _valid_request(source_layer=RESEARCH_FACTOR_LAB_LAYER)
        with self.assertRaisesRegex(CanonicalBacktestContractError, "accepts layer"):
            CanonicalBacktestContract.validate_input(req)

    def test_research_artifacts_rejected_by_canonical_contract(self):
        # Construction-time rejection (see test_no_implicit_fallback_allowed
        # for the rationale).
        with self.assertRaisesRegex(
            CanonicalBacktestContractError, "research_artifact_refs"
        ):
            _valid_request(research_artifact_refs=("factor://alpha-1",))

    def test_experimental_controls_rejected_by_canonical_contract(self):
        with self.assertRaisesRegex(
            CanonicalBacktestContractError, "experimental_controls"
        ):
            _valid_request(experimental_controls={"max_position_ratio": 0.1})

    def test_placeholder_run_is_intentionally_unimplemented(self):
        req = _valid_request()
        with self.assertRaisesRegex(NotImplementedError, "intentionally unimplemented"):
            CanonicalBacktestContract.run_placeholder(req)


class CanonicalBacktestStrictInputTests(unittest.TestCase):
    """Quant-risk tightening: typed inputs, bounds, adjust_mode, lag."""

    def test_required_fields_include_new_quant_risk_fields(self):
        required = CanonicalBacktestContract.input_boundary()["required"]
        self.assertIn("adjust_mode", required)
        self.assertIn("signal_to_execution_lag", required)
        self.assertEqual(required, CANONICAL_INPUT_REQUIRED_FIELDS)

    def test_dict_account_config_is_rejected(self):
        req = _valid_request(account_config={"init_cash": 1_000_000})  # type: ignore[arg-type]
        with self.assertRaisesRegex(CanonicalBacktestContractError, "account_config must be a CanonicalAccountConfig"):
            CanonicalBacktestContract.validate_input(req)

    def test_dict_exchange_config_is_rejected(self):
        req = _valid_request(exchange_config={"freq": "day"})  # type: ignore[arg-type]
        with self.assertRaisesRegex(CanonicalBacktestContractError, "exchange_config must be a CanonicalExchangeConfig"):
            CanonicalBacktestContract.validate_input(req)

    def test_unknown_adjust_mode_is_rejected(self):
        req = _valid_request(adjust_mode="auto")
        with self.assertRaisesRegex(CanonicalBacktestContractError, "adjust_mode must be one of"):
            CanonicalBacktestContract.validate_input(req)

    def test_post_adjusted_is_accepted(self):
        CanonicalBacktestContract.validate_input(_valid_request(adjust_mode=ADJUST_MODE_POST))

    def test_zero_signal_to_execution_lag_is_accepted_as_explicit_same_day(self):
        req = _valid_request(signal_to_execution_lag=0)
        CanonicalBacktestContract.validate_input(req)

    def test_negative_signal_to_execution_lag_is_rejected(self):
        req = _valid_request(signal_to_execution_lag=-1)
        with self.assertRaisesRegex(CanonicalBacktestContractError, "signal_to_execution_lag"):
            CanonicalBacktestContract.validate_input(req)

    def test_bool_signal_to_execution_lag_is_rejected(self):
        # Python bools are ints; the contract must still reject them.
        req = _valid_request(signal_to_execution_lag=True)  # type: ignore[arg-type]
        with self.assertRaisesRegex(CanonicalBacktestContractError, "must be an int"):
            CanonicalBacktestContract.validate_input(req)

    def test_commission_rate_above_cap_is_rejected_at_construction(self):
        with self.assertRaisesRegex(CanonicalBacktestContractError, "commission_rate"):
            _valid_cost_model(commission_rate=COMMISSION_RATE_MAX + 0.01)

    def test_negative_min_cost_is_rejected_at_construction(self):
        with self.assertRaisesRegex(CanonicalBacktestContractError, "min_cost"):
            _valid_cost_model(min_cost=-1.0)

    def test_negative_init_cash_is_rejected_at_construction(self):
        with self.assertRaisesRegex(CanonicalBacktestContractError, "init_cash"):
            CanonicalAccountConfig(init_cash=-1.0)

    def test_zero_init_cash_is_rejected_at_construction(self):
        with self.assertRaisesRegex(CanonicalBacktestContractError, "init_cash"):
            CanonicalAccountConfig(init_cash=0)

    def test_unknown_execution_price_kind_is_rejected_at_construction(self):
        with self.assertRaisesRegex(CanonicalBacktestContractError, "execution_price_kind"):
            _valid_exchange_config(execution_price_kind="limit")

    def test_unknown_freq_is_rejected_at_construction(self):
        with self.assertRaisesRegex(CanonicalBacktestContractError, "freq"):
            _valid_exchange_config(freq="minute")

    def test_cost_model_type_check(self):
        with self.assertRaisesRegex(CanonicalBacktestContractError, "cost_model"):
            CanonicalExchangeConfig(
                freq="day",
                execution_price_kind=EXECUTION_PRICE_CLOSE,
                cost_model={"commission_rate": 0.0003},  # type: ignore[arg-type]
            )


class CanonicalBacktestEvaluationDateBoundaryTests(unittest.TestCase):
    """ISO date format + ordering checks at the validate_input boundary."""

    def test_evaluation_start_bad_format_raises(self):
        req = _valid_request(evaluation_start="banana")
        with self.assertRaisesRegex(CanonicalBacktestContractError, "banana"):
            CanonicalBacktestContract.validate_input(req)

    def test_evaluation_end_bad_format_raises(self):
        req = _valid_request(evaluation_end="2026/02/27")
        with self.assertRaisesRegex(CanonicalBacktestContractError, "2026/02/27"):
            CanonicalBacktestContract.validate_input(req)

    def test_evaluation_start_after_end_raises(self):
        req = _valid_request(
            evaluation_start="2026-02-27",
            evaluation_end="2026-02-01",
        )
        with self.assertRaisesRegex(
            CanonicalBacktestContractError,
            r"evaluation_start.*<= evaluation_end",
        ):
            CanonicalBacktestContract.validate_input(req)

    def test_evaluation_start_equal_end_passes(self):
        # Single-day window is a legitimate use case.
        CanonicalBacktestContract.validate_input(
            _valid_request(
                evaluation_start="2026-02-27",
                evaluation_end="2026-02-27",
            )
        )


class BenchmarkCodeContractTests(unittest.TestCase):
    """P1 alignment: benchmark_code is now required at the contract level."""

    def test_benchmark_code_in_required_fields(self) -> None:
        required = CanonicalBacktestContract.input_boundary()["required"]
        self.assertIn("benchmark_code", required)

    def test_benchmark_code_not_in_optional_fields(self) -> None:
        optional = CanonicalBacktestContract.input_boundary()["optional"]
        self.assertNotIn("benchmark_code", optional)

    def test_none_benchmark_code_rejected_by_post_init(self) -> None:
        with self.assertRaisesRegex(CanonicalBacktestContractError, "benchmark_code must be non-empty"):
            CanonicalBacktestInput(
                predictions_ref="p.pkl",
                evaluation_start="2025-01-01",
                evaluation_end="2025-06-30",
                account_config=CanonicalAccountConfig(init_cash=100_000_000),
                exchange_config=CanonicalExchangeConfig(
                    freq="day",
                    execution_price_kind="close",
                    cost_model=CanonicalExchangeCostModel(
                        commission_rate=0.0005,
                        stamp_tax_bps=10.0,
                        slippage_bps=5.0,
                        min_cost=5.0,
                    ),
                    limit_threshold=0.095,
                ),
                adjust_mode="pre",
                signal_to_execution_lag=1,
                benchmark_code="",
            )

    def test_empty_benchmark_code_rejected_by_validate_input(self) -> None:
        req = _valid_request(benchmark_code="   ")
        with self.assertRaisesRegex(CanonicalBacktestContractError, "benchmark_code is required"):
            CanonicalBacktestContract.validate_input(req)

    def test_valid_benchmark_code_passes(self) -> None:
        req = _valid_request(benchmark_code="SH000300")
        CanonicalBacktestContract.validate_input(req)


if __name__ == "__main__":
    unittest.main()
