"""Unit tests for TaxonomyArtifactPublisher.

Mirror of ``tests/logic/test_universe_artifact_publisher.py`` with
``industry_code`` as the second base column. Hermetic: no qlib.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.taxonomy_data_contract import (  # noqa: E402
    TAXONOMY_MODE_RANGE,
    TAXONOMY_MODE_STATIC,
    TAXONOMY_MODE_TRADE_DATE,
    TaxonomyContractInput,
    TaxonomyDataContract,
)
from src.data.taxonomy_artifact_publisher import (  # noqa: E402
    TaxonomyArtifactPublisher,
    TaxonomyArtifactPublisherError,
)


class TaxonomyPublisherStructuralTests(unittest.TestCase):
    def test_empty_taxonomy_name_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with self.assertRaisesRegex(TaxonomyArtifactPublisherError, "taxonomy_name"):
                TaxonomyArtifactPublisher.publish(
                    taxonomy_name="",
                    temporal_mode=TAXONOMY_MODE_STATIC,
                    rows=[("AAPL", "TECH")],
                    artifact_path=str(tmp / "t.csv"),
                    manifest_path=str(tmp / "t.manifest.json"),
                    snapshot_at="2026-02-27",
                )

    def test_unknown_temporal_mode_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with self.assertRaisesRegex(TaxonomyArtifactPublisherError, "temporal_mode"):
                TaxonomyArtifactPublisher.publish(
                    taxonomy_name="TEST",
                    temporal_mode="banana",
                    rows=[("AAPL", "TECH")],
                    artifact_path=str(tmp / "t.csv"),
                    manifest_path=str(tmp / "t.manifest.json"),
                    snapshot_at="2026-02-27",
                )

    def test_empty_rows_raises_and_leaves_no_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "t.csv"
            manifest = tmp / "t.manifest.json"
            with self.assertRaisesRegex(TaxonomyArtifactPublisherError, "rows is empty"):
                TaxonomyArtifactPublisher.publish(
                    taxonomy_name="TEST",
                    temporal_mode=TAXONOMY_MODE_STATIC,
                    rows=[],
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                    snapshot_at="2026-02-27",
                )
            self.assertFalse(artifact.exists())
            self.assertFalse(manifest.exists())


class TaxonomyPublisherArityTests(unittest.TestCase):
    def test_static_mode_rejects_3tuple_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "t.csv"
            manifest = tmp / "t.manifest.json"
            with self.assertRaisesRegex(TaxonomyArtifactPublisherError, "static"):
                TaxonomyArtifactPublisher.publish(
                    taxonomy_name="TEST",
                    temporal_mode=TAXONOMY_MODE_STATIC,
                    rows=[("AAPL", "TECH", "2026-02-27")],
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                    snapshot_at="2026-02-27",
                )
            self.assertFalse(artifact.exists())
            self.assertFalse(manifest.exists())

    def test_trade_date_mode_rejects_2tuple_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "t.csv"
            manifest = tmp / "t.manifest.json"
            with self.assertRaisesRegex(TaxonomyArtifactPublisherError, "trade_date"):
                TaxonomyArtifactPublisher.publish(
                    taxonomy_name="TEST",
                    temporal_mode=TAXONOMY_MODE_TRADE_DATE,
                    rows=[("AAPL", "TECH")],
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                )
            self.assertFalse(artifact.exists())

    def test_range_mode_rejects_3tuple_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "t.csv"
            manifest = tmp / "t.manifest.json"
            with self.assertRaisesRegex(TaxonomyArtifactPublisherError, "range"):
                TaxonomyArtifactPublisher.publish(
                    taxonomy_name="TEST",
                    temporal_mode=TAXONOMY_MODE_RANGE,
                    rows=[("AAPL", "TECH", "2026-01-01")],
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                    snapshot_at="2026-02-27",
                )
            self.assertFalse(artifact.exists())


class TaxonomyPublisherIsoValidationTests(unittest.TestCase):
    def test_bad_trade_date_in_row_is_rejected_before_io(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "t.csv"
            manifest = tmp / "t.manifest.json"
            with self.assertRaisesRegex(TaxonomyArtifactPublisherError, "2026/02/27"):
                TaxonomyArtifactPublisher.publish(
                    taxonomy_name="TEST",
                    temporal_mode=TAXONOMY_MODE_TRADE_DATE,
                    rows=[("AAPL", "TECH", "2026/02/27")],
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                )
            self.assertFalse(artifact.exists())
            self.assertFalse(manifest.exists())

    def test_bad_explicit_snapshot_at_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "t.csv"
            manifest = tmp / "t.manifest.json"
            with self.assertRaisesRegex(TaxonomyArtifactPublisherError, "banana"):
                TaxonomyArtifactPublisher.publish(
                    taxonomy_name="TEST",
                    temporal_mode=TAXONOMY_MODE_STATIC,
                    rows=[("AAPL", "TECH")],
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                    snapshot_at="banana",
                )
            self.assertFalse(artifact.exists())

    def test_bad_effective_start_in_range_mode_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "t.csv"
            manifest = tmp / "t.manifest.json"
            with self.assertRaisesRegex(TaxonomyArtifactPublisherError, "effective_start"):
                TaxonomyArtifactPublisher.publish(
                    taxonomy_name="TEST",
                    temporal_mode=TAXONOMY_MODE_RANGE,
                    rows=[("AAPL", "TECH", "bad", "2026-02-27")],
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                    snapshot_at="2026-02-27",
                )
            self.assertFalse(artifact.exists())

    def test_range_mode_effective_start_after_end_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "t.csv"
            manifest = tmp / "t.manifest.json"
            with self.assertRaisesRegex(TaxonomyArtifactPublisherError, r"<= effective_end"):
                TaxonomyArtifactPublisher.publish(
                    taxonomy_name="TEST",
                    temporal_mode=TAXONOMY_MODE_RANGE,
                    rows=[("AAPL", "TECH", "2026-03-01", "2026-02-01")],
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                    snapshot_at="2026-03-01",
                )
            self.assertFalse(artifact.exists())


class TaxonomyPublisherSnapshotAtRulesTests(unittest.TestCase):
    def test_static_mode_requires_explicit_snapshot_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with self.assertRaisesRegex(TaxonomyArtifactPublisherError, "snapshot_at"):
                TaxonomyArtifactPublisher.publish(
                    taxonomy_name="TEST",
                    temporal_mode=TAXONOMY_MODE_STATIC,
                    rows=[("AAPL", "TECH")],
                    artifact_path=str(tmp / "t.csv"),
                    manifest_path=str(tmp / "t.manifest.json"),
                )

    def test_range_mode_requires_explicit_snapshot_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with self.assertRaisesRegex(TaxonomyArtifactPublisherError, "snapshot_at"):
                TaxonomyArtifactPublisher.publish(
                    taxonomy_name="TEST",
                    temporal_mode=TAXONOMY_MODE_RANGE,
                    rows=[("AAPL", "TECH", "2026-01-01", "2026-02-27")],
                    artifact_path=str(tmp / "t.csv"),
                    manifest_path=str(tmp / "t.manifest.json"),
                )

    def test_trade_date_mode_default_snapshot_at_uses_max_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            res = TaxonomyArtifactPublisher.publish(
                taxonomy_name="TEST",
                temporal_mode=TAXONOMY_MODE_TRADE_DATE,
                rows=[
                    ("AAPL", "TECH", "2026-02-02"),
                    ("AAPL", "TECH", "2026-02-27"),
                ],
                artifact_path=str(tmp / "t.csv"),
                manifest_path=str(tmp / "t.manifest.json"),
            )
            self.assertEqual(res.profile.metadata.get("snapshot_at"), "2026-02-27")

    def test_trade_date_mode_explicit_snapshot_at_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            res = TaxonomyArtifactPublisher.publish(
                taxonomy_name="TEST",
                temporal_mode=TAXONOMY_MODE_TRADE_DATE,
                rows=[("AAPL", "TECH", "2026-02-27")],
                artifact_path=str(tmp / "t.csv"),
                manifest_path=str(tmp / "t.manifest.json"),
                snapshot_at="2026-02-27",
            )
            self.assertEqual(res.profile.metadata.get("snapshot_at"), "2026-02-27")

    def test_trade_date_mode_explicit_snapshot_at_mismatch_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "t.csv"
            manifest = tmp / "t.manifest.json"
            with self.assertRaises(TaxonomyArtifactPublisherError) as ctx:
                TaxonomyArtifactPublisher.publish(
                    taxonomy_name="TEST",
                    temporal_mode=TAXONOMY_MODE_TRADE_DATE,
                    rows=[("AAPL", "TECH", "2026-02-27")],
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                    snapshot_at="2026-02-25",
                )
            msg = str(ctx.exception)
            self.assertIn("2026-02-25", msg)
            self.assertIn("2026-02-27", msg)
            self.assertFalse(artifact.exists())
            self.assertFalse(manifest.exists())


class TaxonomyPublisherStaticUniquenessTests(unittest.TestCase):
    def test_static_mode_rejects_duplicate_instruments_before_io(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "t.csv"
            manifest = tmp / "t.manifest.json"
            with self.assertRaisesRegex(
                TaxonomyArtifactPublisherError,
                "Duplicate instrument 'AAPL'",
            ):
                TaxonomyArtifactPublisher.publish(
                    taxonomy_name="TEST",
                    temporal_mode=TAXONOMY_MODE_STATIC,
                    rows=[("AAPL", "TECH"), ("AAPL", "FIN")],
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                    snapshot_at="2026-02-27",
                )
            self.assertFalse(artifact.exists())
            self.assertFalse(manifest.exists())

    def test_trade_date_mode_allows_repeated_instrument_across_dates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            res = TaxonomyArtifactPublisher.publish(
                taxonomy_name="TEST",
                temporal_mode=TAXONOMY_MODE_TRADE_DATE,
                rows=[
                    ("AAPL", "TECH", "2026-02-26"),
                    ("AAPL", "FIN", "2026-02-27"),
                ],
                artifact_path=str(tmp / "t.csv"),
                manifest_path=str(tmp / "t.manifest.json"),
            )
            self.assertEqual(res.rows_written, 2)


class TaxonomyPublisherRoundTripTests(unittest.TestCase):
    def test_round_trip_static(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            res = TaxonomyArtifactPublisher.publish(
                taxonomy_name="TEST",
                temporal_mode=TAXONOMY_MODE_STATIC,
                rows=[("AAPL", "TECH"), ("GOOG", "TECH")],
                artifact_path=str(tmp / "t.csv"),
                manifest_path=str(tmp / "t.manifest.json"),
                snapshot_at="2026-02-27",
            )
            self.assertEqual(res.rows_written, 2)
            status = TaxonomyDataContract.validate_and_build_status(
                TaxonomyContractInput(
                    taxonomy_name="TEST",
                    temporal_mode=TAXONOMY_MODE_STATIC,
                    profile=res.profile,
                    reference_date="2026-02-27",
                )
            )
            self.assertEqual(status.contract_health, "ok", msg=f"{status.errors} {status.warnings}")

    def test_round_trip_trade_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            res = TaxonomyArtifactPublisher.publish(
                taxonomy_name="TEST",
                temporal_mode=TAXONOMY_MODE_TRADE_DATE,
                rows=[
                    ("AAPL", "TECH", "2026-02-02"),
                    ("GOOG", "TECH", "2026-02-27"),
                ],
                artifact_path=str(tmp / "t.csv"),
                manifest_path=str(tmp / "t.manifest.json"),
            )
            status = TaxonomyDataContract.validate_and_build_status(
                TaxonomyContractInput(
                    taxonomy_name="TEST",
                    temporal_mode=TAXONOMY_MODE_TRADE_DATE,
                    profile=res.profile,
                    reference_date="2026-02-27",
                )
            )
            self.assertEqual(status.contract_health, "ok", msg=f"{status.errors} {status.warnings}")

    def test_round_trip_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            res = TaxonomyArtifactPublisher.publish(
                taxonomy_name="TEST",
                temporal_mode=TAXONOMY_MODE_RANGE,
                rows=[
                    ("AAPL", "TECH", "2026-01-01", "2026-02-15"),
                    ("GOOG", "TECH", "2026-01-01", "2026-02-27"),
                ],
                artifact_path=str(tmp / "t.csv"),
                manifest_path=str(tmp / "t.manifest.json"),
                snapshot_at="2026-02-27",
            )
            status = TaxonomyDataContract.validate_and_build_status(
                TaxonomyContractInput(
                    taxonomy_name="TEST",
                    temporal_mode=TAXONOMY_MODE_RANGE,
                    profile=res.profile,
                    reference_date="2026-02-27",
                )
            )
            self.assertEqual(status.contract_health, "ok", msg=f"{status.errors} {status.warnings}")

    def test_publisher_delegates_profile_construction_to_loader(self) -> None:
        import src.data.taxonomy_artifact_publisher as pub_mod
        from src.data.taxonomy_artifact_loader import TaxonomyArtifactLoader

        captured: dict = {}
        original_load = TaxonomyArtifactLoader.load

        def _tracking_load(*args, **kwargs):
            captured["called"] = True
            captured["kwargs"] = kwargs
            return original_load(*args, **kwargs)

        pub_mod.TaxonomyArtifactLoader.load = staticmethod(_tracking_load)  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                TaxonomyArtifactPublisher.publish(
                    taxonomy_name="TEST",
                    temporal_mode=TAXONOMY_MODE_STATIC,
                    rows=[("AAPL", "TECH")],
                    artifact_path=str(tmp / "t.csv"),
                    manifest_path=str(tmp / "t.manifest.json"),
                    snapshot_at="2026-02-27",
                )
            self.assertTrue(captured.get("called"))
            self.assertEqual(captured["kwargs"].get("temporal_mode"), TAXONOMY_MODE_STATIC)
        finally:
            pub_mod.TaxonomyArtifactLoader.load = original_load  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
