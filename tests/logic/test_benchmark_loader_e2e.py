"""End-to-end tests: benchmark artifact loader -> benchmark data contract.

These tests exercise real file IO through the loader and then feed the
resulting profile into ``BenchmarkDataContract.validate_and_build_status``
unchanged. They are hermetic: no qlib provider, no network.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.benchmark_data_contract import (  # noqa: E402
    ISSUE_INCOMPLETE_COVERAGE,
    ISSUE_MISSING_ARTIFACT,
    ISSUE_MISSING_MANIFEST,
    ISSUE_SCHEMA_MISMATCH,
    ISSUE_STALE_DATA,
    ISSUE_TEMPORAL_ISSUE,
    BenchmarkContractInput,
    BenchmarkDataContract,
)
from src.data.benchmark_artifact_loader import (  # noqa: E402
    BenchmarkArtifactLoader,
    BenchmarkArtifactLoaderError,
)

FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "benchmark"


class BenchmarkLoaderHappyPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.artifact_path = FIXTURES / "SH000300.csv"
        self.manifest_path = FIXTURES / "SH000300.csv.manifest.json"
        self.assertTrue(self.artifact_path.is_file(), "fixture csv missing")
        self.assertTrue(self.manifest_path.is_file(), "fixture manifest missing")

    def _build_status(self, reference_date: str):
        profile = BenchmarkArtifactLoader.load(
            artifact_path=str(self.artifact_path),
            manifest_path=str(self.manifest_path),
            reference_date=reference_date,
        )
        request = BenchmarkContractInput(
            benchmark_code="SH000300",
            profile=profile,
            reference_date=reference_date,
        )
        return BenchmarkDataContract.validate_and_build_status(request)

    def test_healthy_snapshot_yields_ok(self) -> None:
        status = self._build_status(reference_date="2026-02-27")
        self.assertEqual(status.contract_health, "ok", msg=f"errors={status.errors} warnings={status.warnings}")
        self.assertEqual(status.errors, ())
        self.assertEqual(status.warnings, ())
        self.assertTrue(status.artifact_present)
        self.assertTrue(status.manifest_present)
        self.assertEqual(status.snapshot_start, "2026-02-02")
        self.assertEqual(status.snapshot_end, "2026-02-27")
        self.assertEqual(status.rows, 20)
        self.assertIn("date", status.columns_present)
        self.assertIn("close", status.columns_present)

    def test_stale_snapshot_yields_warning(self) -> None:
        # Reference well past snapshot_end should trigger ISSUE_STALE_DATA.
        status = self._build_status(reference_date="2026-03-20")
        self.assertEqual(status.contract_health, "warning")
        self.assertIn(ISSUE_STALE_DATA, status.warnings)
        self.assertEqual(status.errors, ())


class BenchmarkLoaderFailurePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.valid_artifact = FIXTURES / "SH000300.csv"
        self.valid_manifest = FIXTURES / "SH000300.csv.manifest.json"

    def _validate(self, profile) -> None:
        return BenchmarkDataContract.validate_and_build_status(
            BenchmarkContractInput(
                benchmark_code="SH000300",
                profile=profile,
                reference_date="2026-02-27",
            )
        )

    def test_missing_artifact_yields_error(self) -> None:
        profile = BenchmarkArtifactLoader.load(
            artifact_path=str(FIXTURES / "does_not_exist.csv"),
            manifest_path=str(self.valid_manifest),
            reference_date="2026-02-27",
        )
        status = self._validate(profile)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_MISSING_ARTIFACT, status.errors)

    def test_missing_manifest_yields_error(self) -> None:
        profile = BenchmarkArtifactLoader.load(
            artifact_path=str(self.valid_artifact),
            manifest_path=str(FIXTURES / "does_not_exist.manifest.json"),
            reference_date="2026-02-27",
        )
        status = self._validate(profile)
        self.assertEqual(status.contract_health, "error")
        self.assertIn(ISSUE_MISSING_MANIFEST, status.errors)
        self.assertIn(ISSUE_SCHEMA_MISMATCH, status.errors)  # metadata gone -> schema mismatch

    def test_nan_close_yields_schema_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            bad_csv = tmp_dir / "bad.csv"
            bad_csv.write_text(
                "date,close\n2026-02-02,3800.12\n2026-02-03,NaN\n2026-02-04,3820.77\n",
                encoding="utf-8",
            )
            manifest = tmp_dir / "bad.csv.manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "benchmark_code": "SH000300",
                        "source_name": "fixture-local",
                        "source_uri": "file://bad.csv",
                        "snapshot_at": "2026-02-04",
                        "schema_version": "v1",
                    }
                ),
                encoding="utf-8",
            )
            profile = BenchmarkArtifactLoader.load(
                artifact_path=str(bad_csv),
                manifest_path=str(manifest),
                reference_date="2026-02-04",
            )
        # close column must be dropped from columns_present due to NaN,
        # so the contract surfaces schema_mismatch.
        self.assertNotIn("close", profile.columns_present)
        status = self._validate(profile)
        self.assertIn(ISSUE_SCHEMA_MISMATCH, status.errors)
        self.assertEqual(status.contract_health, "error")

    def test_future_dated_snapshot_yields_temporal_issue(self) -> None:
        # Use the healthy fixture but with a reference_date BEFORE snapshot_end.
        profile = BenchmarkArtifactLoader.load(
            artifact_path=str(self.valid_artifact),
            manifest_path=str(self.valid_manifest),
            reference_date="2026-02-10",
        )
        self.assertTrue(profile.has_future_data)
        status = BenchmarkDataContract.validate_and_build_status(
            BenchmarkContractInput(
                benchmark_code="SH000300",
                profile=profile,
                reference_date="2026-02-10",
            )
        )
        self.assertIn(ISSUE_TEMPORAL_ISSUE, status.errors)
        self.assertEqual(status.contract_health, "error")

    def test_structural_misuse_raises(self) -> None:
        with self.assertRaises(BenchmarkArtifactLoaderError):
            BenchmarkArtifactLoader.load(
                artifact_path="",
                manifest_path=str(self.valid_manifest),
            )
        with self.assertRaises(BenchmarkArtifactLoaderError):
            BenchmarkArtifactLoader.load(
                artifact_path=str(self.valid_artifact),
                manifest_path="",
            )


class BenchmarkLoaderCoverageSmokeTests(unittest.TestCase):
    """Smoke check: loader does not over-report coverage beyond 1.0."""

    def test_coverage_ratio_never_exceeds_one(self) -> None:
        profile = BenchmarkArtifactLoader.load(
            artifact_path=str(FIXTURES / "SH000300.csv"),
            manifest_path=str(FIXTURES / "SH000300.csv.manifest.json"),
            reference_date="2026-02-27",
        )
        self.assertIsNotNone(profile.coverage_ratio)
        assert profile.coverage_ratio is not None  # for type-checkers
        self.assertLessEqual(profile.coverage_ratio, 1.0)
        self.assertGreater(profile.coverage_ratio, 0.0)
        # Sanity: healthy fixture should satisfy the contract's default
        # min_coverage_ratio (0.95).
        self.assertGreaterEqual(profile.coverage_ratio, 0.95, msg="healthy fixture should not be flagged as incomplete")

        # Ensure ISSUE_INCOMPLETE_COVERAGE is not reachable from healthy fixture.
        status = BenchmarkDataContract.validate_and_build_status(
            BenchmarkContractInput(
                benchmark_code="SH000300",
                profile=profile,
                reference_date="2026-02-27",
            )
        )
        self.assertNotIn(ISSUE_INCOMPLETE_COVERAGE, status.warnings)


if __name__ == "__main__":
    unittest.main()
