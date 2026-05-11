import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.run_artifact_contract import (
    ISSUE_LINEAGE_INCONSISTENCY,
    ISSUE_MISSING_ARTIFACT,
    ISSUE_MISSING_MANIFEST,
    ISSUE_MISSING_REPRO_METADATA,
    ISSUE_SCHEMA_MISMATCH,
    ISSUE_TEMPORAL_PROVENANCE_ANOMALY,
    RUN_ARTIFACT_OPERATOR_STATUS_FIELDS,
    RUN_ARTIFACT_SOURCE_OF_TRUTH,
    RunArtifactContract,
    RunArtifactContractError,
    RunArtifactContractInput,
    RunArtifactProfile,
)


def _valid_profile(**overrides) -> RunArtifactProfile:
    payload = {
        "artifact_path": "artifacts/runs/run_001/results.json",
        "manifest_path": "artifacts/runs/run_001/results.json.manifest.json",
        "artifact_present": True,
        "manifest_present": True,
        "metadata": {
            "run_id": "run_001",
            "run_kind": "train_backtest",
            "produced_at": "2026-03-26",
            "config_fingerprint": "cfg-abc123",
            "code_ref": "commit:deadbeef",
            "input_contract_snapshots": "benchmark=v1;taxonomy=v1;universe=v1",
            "schema_version": "v1",
        },
    }
    payload.update(overrides)
    return RunArtifactProfile(**payload)


def _valid_request(**overrides) -> RunArtifactContractInput:
    payload = {
        "run_id": "run_001",
        "profile": _valid_profile(),
        "reference_date": "2026-03-26",
    }
    payload.update(overrides)
    return RunArtifactContractInput(**payload)


class RunArtifactContractTests(unittest.TestCase):
    def test_source_of_truth_is_singular_and_explicit(self):
        self.assertEqual(
            RunArtifactContract.list_source_of_truth_options(),
            (RUN_ARTIFACT_SOURCE_OF_TRUTH,),
        )

    def test_operator_status_fields_are_stable(self):
        self.assertEqual(
            RunArtifactContract.operator_status_fields(),
            RUN_ARTIFACT_OPERATOR_STATUS_FIELDS,
        )

    def test_no_implicit_source_fallback_allowed(self):
        req = _valid_request(allow_implicit_source_fallback=True)
        with self.assertRaisesRegex(RunArtifactContractError, "Implicit run-artifact source fallback"):
            RunArtifactContract.validate_input_boundary(req)

    def test_runtime_execution_controls_are_out_of_scope(self):
        req = _valid_request(runtime_execution_controls={"executor": "qlib"})
        with self.assertRaisesRegex(RunArtifactContractError, "out of scope"):
            RunArtifactContract.validate_input_boundary(req)

    def test_missing_files_report_as_explicit_errors(self):
        req = _valid_request(profile=_valid_profile(artifact_present=False, manifest_present=False))
        status = RunArtifactContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_MISSING_ARTIFACT, status.errors)
        self.assertIn(ISSUE_MISSING_MANIFEST, status.errors)

    def test_missing_reproducibility_metadata_reports_error(self):
        req = _valid_request(
            profile=_valid_profile(
                metadata={
                    "run_id": "run_001",
                    "produced_at": "2026-03-26",
                }
            )
        )
        status = RunArtifactContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_MISSING_REPRO_METADATA, status.errors)

    def test_schema_mismatch_reports_error(self):
        req = _valid_request(profile=_valid_profile(has_schema_mismatch=True))
        status = RunArtifactContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_SCHEMA_MISMATCH, status.errors)

    def test_lineage_inconsistency_reports_error(self):
        req = _valid_request(profile=_valid_profile(has_lineage_inconsistency=True))
        status = RunArtifactContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_LINEAGE_INCONSISTENCY, status.errors)
        self.assertEqual(status.lineage_consistency_status, "inconsistent")

    def test_temporal_provenance_anomaly_reports_error(self):
        req = _valid_request(profile=_valid_profile(has_temporal_provenance_anomaly=True))
        status = RunArtifactContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_TEMPORAL_PROVENANCE_ANOMALY, status.errors)

    def test_status_is_informational_and_not_runtime_execution_semantics(self):
        req = _valid_request()
        status = RunArtifactContract.validate_and_build_status(req)
        self.assertFalse(status.runtime_execution_semantics_in_scope)
        self.assertIn("Informational run-artifact contract health", status.governance_note)


if __name__ == "__main__":
    unittest.main()
