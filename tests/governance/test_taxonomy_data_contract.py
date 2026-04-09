import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.taxonomy_data_contract import (
    ISSUE_INCOMPLETE_COVERAGE,
    ISSUE_INCONSISTENT_MAPPINGS,
    ISSUE_MISSING_ARTIFACT,
    ISSUE_MISSING_MANIFEST,
    ISSUE_SCHEMA_MISMATCH,
    ISSUE_STALE_DATA,
    ISSUE_TEMPORAL_LEAKAGE,
    TAXONOMY_MODE_RANGE,
    TAXONOMY_MODE_STATIC,
    TAXONOMY_MODE_TRADE_DATE,
    TAXONOMY_OPERATOR_STATUS_FIELDS,
    TAXONOMY_SOURCE_OF_TRUTH,
    TaxonomyArtifactProfile,
    TaxonomyContractInput,
    TaxonomyDataContract,
    TaxonomyDataContractError,
)


def _valid_profile(**overrides) -> TaxonomyArtifactProfile:
    payload = {
        "artifact_path": "artifacts/taxonomy/sw_l1.csv",
        "manifest_path": "artifacts/taxonomy/sw_l1.csv.manifest.json",
        "artifact_present": True,
        "manifest_present": True,
        "metadata": {
            "taxonomy_name": "sw_l1",
            "source_name": "mock-source",
            "source_uri": "file://sw_l1.csv",
            "snapshot_at": "2026-03-26",
            "schema_version": "v1",
            "temporal_mode": TAXONOMY_MODE_STATIC,
        },
        "rows": 5000,
        "columns_present": ("instrument", "industry_code"),
        "snapshot_start": "2025-01-01",
        "snapshot_end": "2026-03-26",
        "stale_days": 0,
        "coverage_ratio": 1.0,
    }
    payload.update(overrides)
    return TaxonomyArtifactProfile(**payload)


def _valid_request(**overrides) -> TaxonomyContractInput:
    payload = {
        "taxonomy_name": "sw_l1",
        "temporal_mode": TAXONOMY_MODE_STATIC,
        "profile": _valid_profile(),
        "reference_date": "2026-03-26",
    }
    payload.update(overrides)
    return TaxonomyContractInput(**payload)


class TaxonomyDataContractTests(unittest.TestCase):
    def test_source_of_truth_is_singular_and_explicit(self):
        self.assertEqual(
            TaxonomyDataContract.list_source_of_truth_options(),
            (TAXONOMY_SOURCE_OF_TRUTH,),
        )

    def test_supported_temporal_modes_are_stable(self):
        self.assertEqual(
            TaxonomyDataContract.supported_temporal_modes(),
            (TAXONOMY_MODE_STATIC, TAXONOMY_MODE_TRADE_DATE, TAXONOMY_MODE_RANGE),
        )

    def test_operator_status_fields_are_stable(self):
        self.assertEqual(
            TaxonomyDataContract.operator_status_fields(),
            TAXONOMY_OPERATOR_STATUS_FIELDS,
        )

    def test_no_implicit_source_fallback_allowed(self):
        req = _valid_request(allow_implicit_source_fallback=True)
        with self.assertRaisesRegex(TaxonomyDataContractError, "Implicit taxonomy-source fallback"):
            TaxonomyDataContract.validate_input_boundary(req)

    def test_runtime_industry_controls_are_out_of_scope(self):
        req = _valid_request(runtime_industry_controls={"industry_cap": 0.2})
        with self.assertRaisesRegex(TaxonomyDataContractError, "out of scope"):
            TaxonomyDataContract.validate_input_boundary(req)

    def test_missing_files_report_as_explicit_errors(self):
        req = _valid_request(profile=_valid_profile(artifact_present=False, manifest_present=False))
        status = TaxonomyDataContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_MISSING_ARTIFACT, status.errors)
        self.assertIn(ISSUE_MISSING_MANIFEST, status.errors)

    def test_temporal_mode_schema_boundary_for_trade_date(self):
        req = _valid_request(
            temporal_mode=TAXONOMY_MODE_TRADE_DATE,
            profile=_valid_profile(
                metadata={**_valid_profile().metadata, "temporal_mode": TAXONOMY_MODE_TRADE_DATE},
                columns_present=("instrument", "industry_code"),
            ),
        )
        status = TaxonomyDataContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_SCHEMA_MISMATCH, status.errors)

    def test_temporal_mode_schema_boundary_for_range(self):
        req = _valid_request(
            temporal_mode=TAXONOMY_MODE_RANGE,
            profile=_valid_profile(
                metadata={**_valid_profile().metadata, "temporal_mode": TAXONOMY_MODE_RANGE},
                columns_present=("instrument", "industry_code", "effective_start"),
            ),
        )
        status = TaxonomyDataContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_SCHEMA_MISMATCH, status.errors)

    def test_stale_and_coverage_gaps_report_warnings(self):
        req = _valid_request(profile=_valid_profile(stale_days=12, coverage_ratio=0.7))
        status = TaxonomyDataContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "warning")
        self.assertIn(ISSUE_STALE_DATA, status.warnings)
        self.assertIn(ISSUE_INCOMPLETE_COVERAGE, status.warnings)

    def test_inconsistent_mappings_report_error(self):
        req = _valid_request(profile=_valid_profile(has_inconsistent_mappings=True))
        status = TaxonomyDataContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_INCONSISTENT_MAPPINGS, status.errors)
        self.assertEqual(status.mapping_consistency_status, "inconsistent")

    def test_temporal_leakage_reports_error(self):
        req = _valid_request(profile=_valid_profile(has_future_effective_data=True))
        status = TaxonomyDataContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_TEMPORAL_LEAKAGE, status.errors)

    def test_snapshot_at_mismatch_reports_temporal_leakage(self):
        # trade_date and range modes carry a real date column, so the loader
        # is responsible for cross-checking manifest snapshot_at against the
        # max effective date and setting has_snapshot_at_mismatch on a
        # mismatch. The contract surfaces it as a temporal-leakage error.
        req = _valid_request(
            temporal_mode=TAXONOMY_MODE_TRADE_DATE,
            profile=_valid_profile(
                metadata={**_valid_profile().metadata, "temporal_mode": TAXONOMY_MODE_TRADE_DATE},
                columns_present=("instrument", "industry_code", "trade_date"),
                has_snapshot_at_mismatch=True,
            ),
        )
        status = TaxonomyDataContract.validate_and_build_status(req)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_TEMPORAL_LEAKAGE, status.errors)

    def test_static_mode_does_not_invent_snapshot_at_mismatch(self):
        # Static mode taxonomy artifacts have no date column; loaders must
        # leave has_snapshot_at_mismatch=False. The default valid profile
        # is static and must remain healthy.
        status = TaxonomyDataContract.validate_and_build_status(_valid_request())
        self.assertNotIn(ISSUE_TEMPORAL_LEAKAGE, status.errors)

    def test_status_is_informational_and_not_runtime_industry_semantics(self):
        req = _valid_request()
        status = TaxonomyDataContract.validate_and_build_status(req)
        self.assertFalse(status.industry_runtime_semantics_in_scope)
        self.assertIn("Informational taxonomy contract health", status.governance_note)


if __name__ == "__main__":
    unittest.main()
