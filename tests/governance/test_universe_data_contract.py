import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.universe_data_contract import (
    ISSUE_INCOMPLETE_COVERAGE,
    ISSUE_INCONSISTENT_MEMBERSHIP,
    ISSUE_MISSING_ARTIFACT,
    ISSUE_MISSING_MANIFEST,
    ISSUE_SCHEMA_MISMATCH,
    ISSUE_STALE_DATA,
    ISSUE_TEMPORAL_LEAKAGE,
    UNIVERSE_MODE_RANGE,
    UNIVERSE_MODE_STATIC,
    UNIVERSE_MODE_TRADE_DATE,
    UNIVERSE_OPERATOR_STATUS_FIELDS,
    UNIVERSE_SOURCE_OF_TRUTH,
    UniverseArtifactProfile,
    UniverseContractInput,
    UniverseDataContract,
    UniverseDataContractError,
)


def _valid_profile(**overrides) -> UniverseArtifactProfile:
    payload = {
        "artifact_path": "artifacts/universe/csi300.csv",
        "manifest_path": "artifacts/universe/csi300.csv.manifest.json",
        "artifact_present": True,
        "manifest_present": True,
        "metadata": {
            "universe_name": "csi300",
            "source_name": "mock-source",
            "source_uri": "file://csi300.csv",
            "snapshot_at": "2026-03-26",
            "schema_version": "v1",
            "temporal_mode": UNIVERSE_MODE_STATIC,
        },
        "rows": 15000,
        "columns_present": ("instrument", "in_universe"),
        "snapshot_start": "2025-01-01",
        "snapshot_end": "2026-03-26",
        "stale_days": 0,
        "coverage_ratio": 1.0,
    }
    payload.update(overrides)
    return UniverseArtifactProfile(**payload)


def _valid_request(**overrides) -> UniverseContractInput:
    payload = {
        "universe_name": "csi300",
        "temporal_mode": UNIVERSE_MODE_STATIC,
        "profile": _valid_profile(),
        "reference_date": "2026-03-26",
    }
    payload.update(overrides)
    return UniverseContractInput(**payload)


class UniverseDataContractTests(unittest.TestCase):
    def test_source_of_truth_is_singular_and_explicit(self):
        self.assertEqual(
            UniverseDataContract.list_source_of_truth_options(),
            (UNIVERSE_SOURCE_OF_TRUTH,),
        )

    def test_supported_temporal_modes_are_stable(self):
        self.assertEqual(
            UniverseDataContract.supported_temporal_modes(),
            (UNIVERSE_MODE_STATIC, UNIVERSE_MODE_TRADE_DATE, UNIVERSE_MODE_RANGE),
        )

    def test_operator_status_fields_are_stable(self):
        self.assertEqual(
            UniverseDataContract.operator_status_fields(),
            UNIVERSE_OPERATOR_STATUS_FIELDS,
        )

    def test_no_implicit_source_fallback_allowed(self):
        req = _valid_request(allow_implicit_source_fallback=True)
        with self.assertRaisesRegex(UniverseDataContractError, "Implicit universe-source fallback"):
            UniverseDataContract.validate_input_boundary(req)

    def test_runtime_controls_are_out_of_scope(self):
        req = _valid_request(runtime_universe_controls={"selection_mode": "prefer_uploaded"})
        with self.assertRaisesRegex(UniverseDataContractError, "out of scope"):
            UniverseDataContract.validate_input_boundary(req)

    def test_missing_files_report_as_explicit_errors(self):
        req = _valid_request(profile=_valid_profile(artifact_present=False, manifest_present=False))
        status = UniverseDataContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_MISSING_ARTIFACT, status.errors)
        self.assertIn(ISSUE_MISSING_MANIFEST, status.errors)

    def test_temporal_mode_schema_boundary_for_trade_date(self):
        req = _valid_request(
            temporal_mode=UNIVERSE_MODE_TRADE_DATE,
            profile=_valid_profile(
                metadata={**_valid_profile().metadata, "temporal_mode": UNIVERSE_MODE_TRADE_DATE},
                columns_present=("instrument", "in_universe"),
            ),
        )
        status = UniverseDataContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_SCHEMA_MISMATCH, status.errors)

    def test_temporal_mode_schema_boundary_for_range(self):
        req = _valid_request(
            temporal_mode=UNIVERSE_MODE_RANGE,
            profile=_valid_profile(
                metadata={**_valid_profile().metadata, "temporal_mode": UNIVERSE_MODE_RANGE},
                columns_present=("instrument", "in_universe", "effective_start"),
            ),
        )
        status = UniverseDataContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_SCHEMA_MISMATCH, status.errors)

    def test_stale_and_coverage_gaps_report_warnings(self):
        req = _valid_request(profile=_valid_profile(stale_days=10, coverage_ratio=0.8))
        status = UniverseDataContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "warning")
        self.assertIn(ISSUE_STALE_DATA, status.warnings)
        self.assertIn(ISSUE_INCOMPLETE_COVERAGE, status.warnings)

    def test_membership_inconsistency_reports_error(self):
        req = _valid_request(profile=_valid_profile(has_inconsistent_membership=True))
        status = UniverseDataContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_INCONSISTENT_MEMBERSHIP, status.errors)
        self.assertEqual(status.membership_consistency_status, "inconsistent")

    def test_temporal_leakage_reports_error(self):
        req = _valid_request(profile=_valid_profile(has_future_effective_data=True))
        status = UniverseDataContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_TEMPORAL_LEAKAGE, status.errors)

    def test_status_is_informational_and_not_runtime_selection_semantics(self):
        req = _valid_request()
        status = UniverseDataContract.validate_and_build_status(req)
        self.assertFalse(status.runtime_selection_semantics_in_scope)
        self.assertIn("Informational universe contract health", status.governance_note)


if __name__ == "__main__":
    unittest.main()
