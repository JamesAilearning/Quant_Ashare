"""Unit tests for TaxonomyArtifactLoader.

Mirror of ``tests/logic/test_universe_artifact_loader.py`` with
``industry_code`` as the second base column. Hermetic: no qlib.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.taxonomy_data_contract import (  # noqa: E402
    ISSUE_MISSING_ARTIFACT,
    ISSUE_MISSING_MANIFEST,
    ISSUE_SCHEMA_MISMATCH,
    ISSUE_TEMPORAL_LEAKAGE,
    TAXONOMY_MODE_RANGE,
    TAXONOMY_MODE_STATIC,
    TAXONOMY_MODE_TRADE_DATE,
    TaxonomyContractInput,
    TaxonomyDataContract,
)
from src.data.taxonomy_artifact_loader import (  # noqa: E402
    TaxonomyArtifactLoader,
    TaxonomyArtifactLoaderError,
)
from src.data.trading_calendar import StaticTradingCalendar  # noqa: E402


def _write_csv(path: Path, header: tuple[str, ...], rows: list[tuple]) -> None:
    import csv
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(list(header))
        for row in rows:
            writer.writerow([str(cell) for cell in row])


def _write_manifest(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)


def _manifest(temporal_mode: str, snapshot_at: str) -> dict:
    return {
        "taxonomy_name": "TEST",
        "source_name": "explicit-rows",
        "source_uri": "explicit-rows://TEST",
        "snapshot_at": snapshot_at,
        "schema_version": "v1",
        "temporal_mode": temporal_mode,
    }


class TaxonomyLoaderStructuralTests(unittest.TestCase):
    def test_empty_artifact_path_raises(self) -> None:
        with self.assertRaisesRegex(TaxonomyArtifactLoaderError, "artifact_path"):
            TaxonomyArtifactLoader.load(
                artifact_path="",
                manifest_path="/tmp/m.json",
                temporal_mode=TAXONOMY_MODE_STATIC,
            )

    def test_unreadable_artifact_raises_loader_error_with_path(self) -> None:
        artifact = Path("unreadable-taxonomy.csv")
        with patch("pathlib.Path.open", side_effect=OSError("permission denied")):
            with self.assertRaisesRegex(
                TaxonomyArtifactLoaderError, "unreadable-taxonomy.csv",
            ):
                TaxonomyArtifactLoader._read_csv(
                    artifact,
                    TAXONOMY_MODE_STATIC,
                    None,
                )

    def test_empty_manifest_path_raises(self) -> None:
        with self.assertRaisesRegex(TaxonomyArtifactLoaderError, "manifest_path"):
            TaxonomyArtifactLoader.load(
                artifact_path="/tmp/a.csv",
                manifest_path="",
                temporal_mode=TAXONOMY_MODE_STATIC,
            )

    def test_unknown_temporal_mode_raises(self) -> None:
        with self.assertRaisesRegex(TaxonomyArtifactLoaderError, "temporal_mode"):
            TaxonomyArtifactLoader.load(
                artifact_path="/tmp/a.csv",
                manifest_path="/tmp/m.json",
                temporal_mode="banana",
            )


class TaxonomyLoaderStaticModeTests(unittest.TestCase):
    def test_healthy_static_mode_yields_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "t.csv"
            manifest = tmp / "t.manifest.json"
            _write_csv(
                artifact,
                ("instrument", "industry_code"),
                [("AAPL", "TECH"), ("GOOG", "TECH")],
            )
            _write_manifest(
                manifest,
                _manifest(TAXONOMY_MODE_STATIC, "2026-02-27"),
            )

            profile = TaxonomyArtifactLoader.load(
                artifact_path=str(artifact),
                manifest_path=str(manifest),
                temporal_mode=TAXONOMY_MODE_STATIC,
                reference_date="2026-02-27",
            )

            self.assertTrue(profile.artifact_present)
            self.assertTrue(profile.manifest_present)
            self.assertEqual(profile.rows, 2)
            self.assertIn("instrument", profile.columns_present)
            self.assertIn("industry_code", profile.columns_present)
            self.assertIsNone(profile.snapshot_start)
            self.assertIsNone(profile.snapshot_end)
            self.assertFalse(profile.has_snapshot_at_mismatch)
            self.assertFalse(profile.has_future_effective_data)

            status = TaxonomyDataContract.validate_and_build_status(
                TaxonomyContractInput(
                    taxonomy_name="TEST",
                    temporal_mode=TAXONOMY_MODE_STATIC,
                    profile=profile,
                    reference_date="2026-02-27",
                )
            )
            self.assertEqual(
                status.contract_health,
                "ok",
                msg=f"errors={status.errors} warnings={status.warnings}",
            )

    def test_static_mode_never_triggers_snapshot_at_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "t.csv"
            manifest = tmp / "t.manifest.json"
            _write_csv(
                artifact,
                ("instrument", "industry_code"),
                [("AAPL", "TECH")],
            )
            _write_manifest(manifest, _manifest(TAXONOMY_MODE_STATIC, "1999-01-01"))

            profile = TaxonomyArtifactLoader.load(
                artifact_path=str(artifact),
                manifest_path=str(manifest),
                temporal_mode=TAXONOMY_MODE_STATIC,
            )
            self.assertFalse(profile.has_snapshot_at_mismatch)


class TaxonomyLoaderTradeDateModeTests(unittest.TestCase):
    def _build_tmp(self, rows: list[tuple], snapshot_at: str) -> tuple[Path, Path, Path]:
        tmp = Path(tempfile.mkdtemp())
        artifact = tmp / "t.csv"
        manifest = tmp / "t.manifest.json"
        _write_csv(
            artifact,
            ("instrument", "industry_code", "trade_date"),
            rows,
        )
        _write_manifest(manifest, _manifest(TAXONOMY_MODE_TRADE_DATE, snapshot_at))
        return tmp, artifact, manifest

    def test_healthy_trade_date_mode(self) -> None:
        _, artifact, manifest = self._build_tmp(
            rows=[
                ("AAPL", "TECH", "2026-02-02"),
                ("AAPL", "TECH", "2026-02-27"),
                ("GOOG", "TECH", "2026-02-27"),
            ],
            snapshot_at="2026-02-27",
        )
        profile = TaxonomyArtifactLoader.load(
            artifact_path=str(artifact),
            manifest_path=str(manifest),
            temporal_mode=TAXONOMY_MODE_TRADE_DATE,
            reference_date="2026-02-27",
        )
        self.assertEqual(profile.snapshot_start, "2026-02-02")
        self.assertEqual(profile.snapshot_end, "2026-02-27")
        self.assertFalse(profile.has_snapshot_at_mismatch)
        self.assertFalse(profile.has_future_effective_data)

        status = TaxonomyDataContract.validate_and_build_status(
            TaxonomyContractInput(
                taxonomy_name="TEST",
                temporal_mode=TAXONOMY_MODE_TRADE_DATE,
                profile=profile,
                reference_date="2026-02-27",
            )
        )
        self.assertEqual(
            status.contract_health,
            "ok",
            msg=f"errors={status.errors} warnings={status.warnings}",
        )

    def test_snapshot_at_mismatch_is_flagged(self) -> None:
        _, artifact, manifest = self._build_tmp(
            rows=[("AAPL", "TECH", "2026-02-20")],
            snapshot_at="2026-02-27",
        )
        profile = TaxonomyArtifactLoader.load(
            artifact_path=str(artifact),
            manifest_path=str(manifest),
            temporal_mode=TAXONOMY_MODE_TRADE_DATE,
        )
        self.assertTrue(profile.has_snapshot_at_mismatch)

    def test_future_trade_date_is_flagged(self) -> None:
        _, artifact, manifest = self._build_tmp(
            rows=[
                ("AAPL", "TECH", "2026-02-20"),
                ("AAPL", "TECH", "2026-03-10"),
            ],
            snapshot_at="2026-03-10",
        )
        profile = TaxonomyArtifactLoader.load(
            artifact_path=str(artifact),
            manifest_path=str(manifest),
            temporal_mode=TAXONOMY_MODE_TRADE_DATE,
            reference_date="2026-02-27",
        )
        self.assertTrue(profile.has_future_effective_data)

        status = TaxonomyDataContract.validate_and_build_status(
            TaxonomyContractInput(
                taxonomy_name="TEST",
                temporal_mode=TAXONOMY_MODE_TRADE_DATE,
                profile=profile,
                reference_date="2026-02-27",
            )
        )
        self.assertIn(ISSUE_TEMPORAL_LEAKAGE, status.errors)

    def test_calendar_injection_yields_coverage_ratio(self) -> None:
        _, artifact, manifest = self._build_tmp(
            rows=[
                ("AAPL", "TECH", "2026-02-25"),
                ("AAPL", "TECH", "2026-02-26"),
                ("AAPL", "TECH", "2026-02-27"),
            ],
            snapshot_at="2026-02-27",
        )
        calendar = StaticTradingCalendar(
            [date(2026, 2, 25), date(2026, 2, 26), date(2026, 2, 27)]
        )
        profile = TaxonomyArtifactLoader.load(
            artifact_path=str(artifact),
            manifest_path=str(manifest),
            temporal_mode=TAXONOMY_MODE_TRADE_DATE,
            calendar=calendar,
        )
        self.assertEqual(profile.coverage_ratio, 1.0)

    def test_coverage_ratio_is_none_without_calendar(self) -> None:
        _, artifact, manifest = self._build_tmp(
            rows=[("AAPL", "TECH", "2026-02-27")],
            snapshot_at="2026-02-27",
        )
        profile = TaxonomyArtifactLoader.load(
            artifact_path=str(artifact),
            manifest_path=str(manifest),
            temporal_mode=TAXONOMY_MODE_TRADE_DATE,
        )
        self.assertIsNone(profile.coverage_ratio)


class TaxonomyLoaderRangeModeTests(unittest.TestCase):
    def test_healthy_range_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "t.csv"
            manifest = tmp / "t.manifest.json"
            _write_csv(
                artifact,
                ("instrument", "industry_code", "effective_start", "effective_end"),
                [
                    ("AAPL", "TECH", "2026-01-01", "2026-02-15"),
                    ("GOOG", "TECH", "2026-01-01", "2026-02-27"),
                ],
            )
            _write_manifest(manifest, _manifest(TAXONOMY_MODE_RANGE, "2026-02-27"))

            profile = TaxonomyArtifactLoader.load(
                artifact_path=str(artifact),
                manifest_path=str(manifest),
                temporal_mode=TAXONOMY_MODE_RANGE,
                reference_date="2026-02-27",
            )
            self.assertEqual(profile.snapshot_start, "2026-01-01")
            self.assertEqual(profile.snapshot_end, "2026-02-27")
            self.assertFalse(profile.has_future_effective_data)

            status = TaxonomyDataContract.validate_and_build_status(
                TaxonomyContractInput(
                    taxonomy_name="TEST",
                    temporal_mode=TAXONOMY_MODE_RANGE,
                    profile=profile,
                    reference_date="2026-02-27",
                )
            )
            self.assertEqual(
                status.contract_health,
                "ok",
                msg=f"errors={status.errors} warnings={status.warnings}",
            )

    def test_range_mode_future_effective_end_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "t.csv"
            manifest = tmp / "t.manifest.json"
            _write_csv(
                artifact,
                ("instrument", "industry_code", "effective_start", "effective_end"),
                [("AAPL", "TECH", "2026-01-01", "2030-01-01")],
            )
            _write_manifest(manifest, _manifest(TAXONOMY_MODE_RANGE, "2030-01-01"))

            profile = TaxonomyArtifactLoader.load(
                artifact_path=str(artifact),
                manifest_path=str(manifest),
                temporal_mode=TAXONOMY_MODE_RANGE,
                reference_date="2026-02-27",
            )
            self.assertTrue(profile.has_future_effective_data)


class TaxonomyLoaderMissingFilesTests(unittest.TestCase):
    def test_missing_artifact_yields_missing_artifact_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            manifest = tmp / "t.manifest.json"
            _write_manifest(manifest, _manifest(TAXONOMY_MODE_STATIC, "2026-02-27"))

            profile = TaxonomyArtifactLoader.load(
                artifact_path=str(tmp / "missing.csv"),
                manifest_path=str(manifest),
                temporal_mode=TAXONOMY_MODE_STATIC,
            )
            self.assertFalse(profile.artifact_present)

            status = TaxonomyDataContract.validate_and_build_status(
                TaxonomyContractInput(
                    taxonomy_name="TEST",
                    temporal_mode=TAXONOMY_MODE_STATIC,
                    profile=profile,
                )
            )
            self.assertEqual(status.contract_health, "error")
            self.assertIn(ISSUE_MISSING_ARTIFACT, status.errors)

    def test_missing_manifest_yields_missing_manifest_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "t.csv"
            _write_csv(
                artifact,
                ("instrument", "industry_code"),
                [("AAPL", "TECH")],
            )

            profile = TaxonomyArtifactLoader.load(
                artifact_path=str(artifact),
                manifest_path=str(tmp / "missing.json"),
                temporal_mode=TAXONOMY_MODE_STATIC,
            )
            self.assertFalse(profile.manifest_present)

            status = TaxonomyDataContract.validate_and_build_status(
                TaxonomyContractInput(
                    taxonomy_name="TEST",
                    temporal_mode=TAXONOMY_MODE_STATIC,
                    profile=profile,
                )
            )
            self.assertEqual(status.contract_health, "error")
            self.assertIn(ISSUE_MISSING_MANIFEST, status.errors)
            self.assertIn(ISSUE_SCHEMA_MISMATCH, status.errors)


if __name__ == "__main__":
    unittest.main()
