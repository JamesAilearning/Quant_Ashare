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
    CANONICAL_OFFICIAL_BACKTEST_PATH,
    CANONICAL_OUTPUT_FIELDS,
    CanonicalBacktestContract,
    CanonicalBacktestContractError,
    CanonicalBacktestInput,
)


def _valid_request(**overrides) -> CanonicalBacktestInput:
    payload = {
        "predictions_ref": "predictions://run-001",
        "evaluation_start": "2025-01-01",
        "evaluation_end": "2025-12-31",
        "account_config": {"init_cash": 1_000_000},
        "exchange_config": {"freq": "day"},
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

    def test_no_implicit_fallback_allowed(self):
        req = _valid_request(allow_implicit_fallback=True)
        with self.assertRaisesRegex(CanonicalBacktestContractError, "Implicit fallback is forbidden"):
            CanonicalBacktestContract.validate_input(req)

    def test_experimental_layer_rejected_by_canonical_contract(self):
        req = _valid_request(source_layer=EXPERIMENTAL_RUNTIME_LAYER)
        with self.assertRaisesRegex(CanonicalBacktestContractError, "accepts layer"):
            CanonicalBacktestContract.validate_input(req)

    def test_research_layer_rejected_by_canonical_contract(self):
        req = _valid_request(source_layer=RESEARCH_FACTOR_LAB_LAYER)
        with self.assertRaisesRegex(CanonicalBacktestContractError, "accepts layer"):
            CanonicalBacktestContract.validate_input(req)

    def test_research_artifacts_rejected_by_canonical_contract(self):
        req = _valid_request(research_artifact_refs=("factor://alpha-1",))
        with self.assertRaisesRegex(CanonicalBacktestContractError, "research_artifact_refs"):
            CanonicalBacktestContract.validate_input(req)

    def test_experimental_controls_rejected_by_canonical_contract(self):
        req = _valid_request(experimental_controls={"max_position_ratio": 0.1})
        with self.assertRaisesRegex(CanonicalBacktestContractError, "experimental_controls"):
            CanonicalBacktestContract.validate_input(req)

    def test_placeholder_run_is_intentionally_unimplemented(self):
        req = _valid_request()
        with self.assertRaisesRegex(NotImplementedError, "intentionally unimplemented"):
            CanonicalBacktestContract.run_placeholder(req)


if __name__ == "__main__":
    unittest.main()
