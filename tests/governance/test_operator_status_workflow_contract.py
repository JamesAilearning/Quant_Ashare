import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.operator_status_workflow_contract import (
    BOUNDARY_CANONICAL_RUNTIME,
    BOUNDARY_DATA_CONTRACT,
    BOUNDARY_RUNTIME_PLACEHOLDER,
    OPERATOR_STATUS_SUMMARY_FIELDS,
    STATUS_NOT_READY,
    STATUS_OK,
    STATUS_WARNING,
    OperatorStatusEntry,
    OperatorStatusWorkflowContract,
    OperatorStatusWorkflowContractError,
    OperatorWorkflowStatusInput,
)


def _entry(**overrides) -> OperatorStatusEntry:
    payload = {
        "component_id": "core.canonical",
        "boundary_type": BOUNDARY_CANONICAL_RUNTIME,
        "status_category": STATUS_OK,
        "summary": "Canonical boundary is defined.",
    }
    payload.update(overrides)
    return OperatorStatusEntry(**payload)


def _valid_input() -> OperatorWorkflowStatusInput:
    return OperatorWorkflowStatusInput(
        entries=(
            _entry(
                component_id="core.canonical",
                boundary_type=BOUNDARY_CANONICAL_RUNTIME,
                status_category=STATUS_OK,
                summary="Canonical boundary status is ready.",
            ),
            _entry(
                component_id="contracts.data",
                boundary_type=BOUNDARY_DATA_CONTRACT,
                status_category=STATUS_WARNING,
                summary="One contract dependency is stale.",
                warnings=("stale_data",),
            ),
            _entry(
                component_id="runtime.placeholder",
                boundary_type=BOUNDARY_RUNTIME_PLACEHOLDER,
                status_category=STATUS_NOT_READY,
                summary="Runtime implementation intentionally deferred.",
                is_placeholder=True,
            ),
        )
    )


class OperatorStatusWorkflowContractTests(unittest.TestCase):
    def test_summary_fields_are_stable(self):
        self.assertEqual(
            OperatorStatusWorkflowContract.summary_fields(),
            OPERATOR_STATUS_SUMMARY_FIELDS,
        )

    def test_no_implicit_status_fallback_allowed(self):
        req = _valid_input()
        req = OperatorWorkflowStatusInput(
            entries=req.entries,
            allow_implicit_status_fallback=True,
        )
        with self.assertRaisesRegex(OperatorStatusWorkflowContractError, "Implicit status fallback"):
            OperatorStatusWorkflowContract.validate_input_boundary(req)

    def test_placeholder_must_be_not_ready(self):
        req = OperatorWorkflowStatusInput(
            entries=(
                _entry(
                    component_id="runtime.placeholder",
                    boundary_type=BOUNDARY_RUNTIME_PLACEHOLDER,
                    status_category=STATUS_OK,
                    summary="invalid placeholder status",
                    is_placeholder=True,
                ),
            )
        )
        with self.assertRaisesRegex(OperatorStatusWorkflowContractError, "Placeholder status entries"):
            OperatorStatusWorkflowContract.validate_input_boundary(req)

    def test_not_ready_must_be_placeholder(self):
        req = OperatorWorkflowStatusInput(
            entries=(
                _entry(
                    component_id="runtime.placeholder",
                    boundary_type=BOUNDARY_RUNTIME_PLACEHOLDER,
                    status_category=STATUS_NOT_READY,
                    summary="not ready",
                    is_placeholder=False,
                ),
            )
        )
        with self.assertRaisesRegex(OperatorStatusWorkflowContractError, "must be represented as placeholder"):
            OperatorStatusWorkflowContract.validate_input_boundary(req)

    def test_informational_and_governance_are_explicitly_separated(self):
        req = OperatorWorkflowStatusInput(
            entries=(
                _entry(
                    governance_meaning_from_status=True,
                ),
            )
        )
        with self.assertRaisesRegex(OperatorStatusWorkflowContractError, "cannot redefine governance"):
            OperatorStatusWorkflowContract.validate_input_boundary(req)

    def test_missing_required_boundary_requires_explicit_not_ready_representation(self):
        req = OperatorWorkflowStatusInput(
            entries=(
                _entry(
                    component_id="core.canonical",
                    boundary_type=BOUNDARY_CANONICAL_RUNTIME,
                    status_category=STATUS_OK,
                    summary="ok",
                ),
            )
        )
        with self.assertRaisesRegex(OperatorStatusWorkflowContractError, "explicitly represented with not_ready"):
            OperatorStatusWorkflowContract.validate_input_boundary(req)

    def test_snapshot_builds_overall_status(self):
        req = _valid_input()
        snapshot = OperatorStatusWorkflowContract.build_snapshot(req)
        self.assertEqual(snapshot.overall_status_category, STATUS_WARNING)
        self.assertEqual(
            snapshot.represented_boundary_types,
            (
                BOUNDARY_CANONICAL_RUNTIME,
                BOUNDARY_DATA_CONTRACT,
                BOUNDARY_RUNTIME_PLACEHOLDER,
            ),
        )
        self.assertEqual(snapshot.missing_boundary_types, ())


if __name__ == "__main__":
    unittest.main()
