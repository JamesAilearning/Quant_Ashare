import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.benchmark_data_contract import (
    BENCHMARK_OPERATOR_STATUS_FIELDS,
    BENCHMARK_SOURCE_OF_TRUTH,
    ISSUE_INCOMPLETE_COVERAGE,
    ISSUE_MISSING_ARTIFACT,
    ISSUE_MISSING_MANIFEST,
    ISSUE_SCHEMA_MISMATCH,
    ISSUE_STALE_DATA,
    ISSUE_TEMPORAL_ISSUE,
    BenchmarkArtifactProfile,
    BenchmarkContractInput,
    BenchmarkDataContract,
    BenchmarkDataContractError,
)


def _valid_profile(**overrides) -> BenchmarkArtifactProfile:
    payload = {
        "artifact_path": "artifacts/benchmark/SH000300.csv",
        "manifest_path": "artifacts/benchmark/SH000300.csv.manifest.json",
        "artifact_present": True,
        "manifest_present": True,
        "metadata": {
            "benchmark_code": "SH000300",
            "source_name": "mock-source",
            "source_uri": "file://benchmark.csv",
            "snapshot_at": "2026-03-26",
            "schema_version": "v1",
        },
        "rows": 300,
        "columns_present": ("date", "close"),
        "snapshot_start": "2025-01-01",
        "snapshot_end": "2026-03-26",
        "stale_days": 0,
        "coverage_ratio": 1.0,
    }
    payload.update(overrides)
    return BenchmarkArtifactProfile(**payload)


def _valid_request(**overrides) -> BenchmarkContractInput:
    payload = {
        "benchmark_code": "SH000300",
        "profile": _valid_profile(),
        "reference_date": "2026-03-26",
    }
    payload.update(overrides)
    return BenchmarkContractInput(**payload)


class BenchmarkDataContractTests(unittest.TestCase):
    def test_source_of_truth_is_singular_and_explicit(self):
        self.assertEqual(
            BenchmarkDataContract.list_source_of_truth_options(),
            (BENCHMARK_SOURCE_OF_TRUTH,),
        )

    def test_operator_status_fields_are_stable(self):
        self.assertEqual(
            BenchmarkDataContract.operator_status_fields(),
            BENCHMARK_OPERATOR_STATUS_FIELDS,
        )

    def test_no_implicit_source_fallback_allowed(self):
        req = _valid_request(allow_implicit_source_fallback=True)
        with self.assertRaisesRegex(BenchmarkDataContractError, "Implicit benchmark-source fallback"):
            BenchmarkDataContract.validate_input_boundary(req)

    def test_runtime_selection_controls_are_out_of_scope(self):
        req = _valid_request(runtime_selection_controls={"provider_precedence": "prefer_upload"})
        with self.assertRaisesRegex(BenchmarkDataContractError, "out of scope"):
            BenchmarkDataContract.validate_input_boundary(req)

    def test_missing_files_report_as_explicit_errors(self):
        req = _valid_request(profile=_valid_profile(artifact_present=False, manifest_present=False))
        status = BenchmarkDataContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_MISSING_ARTIFACT, status.errors)
        self.assertIn(ISSUE_MISSING_MANIFEST, status.errors)

    def test_schema_mismatch_reports_error(self):
        req = _valid_request(profile=_valid_profile(columns_present=("date",)))
        status = BenchmarkDataContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_SCHEMA_MISMATCH, status.errors)

    def test_stale_and_coverage_gaps_report_warnings(self):
        req = _valid_request(profile=_valid_profile(stale_days=9, coverage_ratio=0.70))
        status = BenchmarkDataContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "warning")
        self.assertIn(ISSUE_STALE_DATA, status.warnings)
        self.assertIn(ISSUE_INCOMPLETE_COVERAGE, status.warnings)

    def test_temporal_issue_is_reported_as_error(self):
        req = _valid_request(profile=_valid_profile(has_future_data=True))
        status = BenchmarkDataContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_TEMPORAL_ISSUE, status.errors)

    def test_snapshot_at_mismatch_is_reported_as_error(self):
        # The loader is responsible for computing has_snapshot_at_mismatch
        # by comparing manifest snapshot_at against the artifact's true max
        # row date. Here we simulate that the loader has already detected
        # the mismatch and the contract must surface it as a temporal error.
        req = _valid_request(profile=_valid_profile(has_snapshot_at_mismatch=True))
        status = BenchmarkDataContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_TEMPORAL_ISSUE, status.errors)

    def test_status_is_informational_and_not_runtime_selection(self):
        req = _valid_request()
        status = BenchmarkDataContract.validate_and_build_status(req)
        self.assertFalse(status.selection_semantics_in_scope)
        self.assertIn("Informational contract health", status.governance_note)


if __name__ == "__main__":
    unittest.main()
