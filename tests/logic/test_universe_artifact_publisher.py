"""Unit tests for UniverseArtifactPublisher.

Hermetic: no qlib. Exercises the full publish → loader round trip
using the temporary directory and feeds the returned profile into
``UniverseDataContract.validate_and_build_status``.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.universe_data_contract import (  # noqa: E402
    UNIVERSE_MODE_RANGE,
    UNIVERSE_MODE_STATIC,
    UNIVERSE_MODE_TRADE_DATE,
    UniverseContractInput,
    UniverseDataContract,
)
from src.data.universe_artifact_publisher import (  # noqa: E402
    UniverseArtifactPublisher,
    UniverseArtifactPublisherError,
)


class UniversePublisherStructuralTests(unittest.TestCase):
    def test_empty_universe_name_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with self.assertRaisesRegex(UniverseArtifactPublisherError, "universe_name"):
                UniverseArtifactPublisher.publish(
                    universe_name="",
                    temporal_mode=UNIVERSE_MODE_STATIC,
                    rows=[("AAPL", True)],
                    artifact_path=str(tmp / "u.csv"),
                    manifest_path=str(tmp / "u.manifest.json"),
                    snapshot_at="2026-02-27",
                )

    def test_unknown_temporal_mode_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with self.assertRaisesRegex(UniverseArtifactPublisherError, "temporal_mode"):
                UniverseArtifactPublisher.publish(
                    universe_name="TEST",
                    temporal_mode="banana",
                    rows=[("AAPL", True)],
                    artifact_path=str(tmp / "u.csv"),
                    manifest_path=str(tmp / "u.manifest.json"),
                    snapshot_at="2026-02-27",
                )

    def test_empty_rows_raises_and_leaves_no_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "u.csv"
            manifest = tmp / "u.manifest.json"
            with self.assertRaisesRegex(UniverseArtifactPublisherError, "rows is empty"):
                UniverseArtifactPublisher.publish(
                    universe_name="TEST",
                    temporal_mode=UNIVERSE_MODE_STATIC,
                    rows=[],
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                    snapshot_at="2026-02-27",
                )
            self.assertFalse(artifact.exists())
            self.assertFalse(manifest.exists())


class UniversePublisherArityTests(unittest.TestCase):
    def test_static_mode_rejects_3tuple_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "u.csv"
            manifest = tmp / "u.manifest.json"
            with self.assertRaisesRegex(UniverseArtifactPublisherError, "static"):
                UniverseArtifactPublisher.publish(
                    universe_name="TEST",
                    temporal_mode=UNIVERSE_MODE_STATIC,
                    rows=[("AAPL", True, "2026-02-27")],
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                    snapshot_at="2026-02-27",
                )
            self.assertFalse(artifact.exists())
            self.assertFalse(manifest.exists())

    def test_trade_date_mode_rejects_2tuple_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "u.csv"
            manifest = tmp / "u.manifest.json"
            with self.assertRaisesRegex(UniverseArtifactPublisherError, "trade_date"):
                UniverseArtifactPublisher.publish(
                    universe_name="TEST",
                    temporal_mode=UNIVERSE_MODE_TRADE_DATE,
                    rows=[("AAPL", True)],
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                )
            self.assertFalse(artifact.exists())

    def test_range_mode_rejects_3tuple_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "u.csv"
            manifest = tmp / "u.manifest.json"
            with self.assertRaisesRegex(UniverseArtifactPublisherError, "range"):
                UniverseArtifactPublisher.publish(
                    universe_name="TEST",
                    temporal_mode=UNIVERSE_MODE_RANGE,
                    rows=[("AAPL", True, "2026-01-01")],
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                    snapshot_at="2026-02-27",
                )
            self.assertFalse(artifact.exists())


class UniversePublisherIsoValidationTests(unittest.TestCase):
    def test_bad_trade_date_in_row_is_rejected_before_io(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "u.csv"
            manifest = tmp / "u.manifest.json"
            with self.assertRaisesRegex(UniverseArtifactPublisherError, "2026/02/27"):
                UniverseArtifactPublisher.publish(
                    universe_name="TEST",
                    temporal_mode=UNIVERSE_MODE_TRADE_DATE,
                    rows=[("AAPL", True, "2026/02/27")],
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                )
            self.assertFalse(artifact.exists())
            self.assertFalse(manifest.exists())

    def test_bad_explicit_snapshot_at_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "u.csv"
            manifest = tmp / "u.manifest.json"
            with self.assertRaisesRegex(UniverseArtifactPublisherError, "banana"):
                UniverseArtifactPublisher.publish(
                    universe_name="TEST",
                    temporal_mode=UNIVERSE_MODE_STATIC,
                    rows=[("AAPL", True)],
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                    snapshot_at="banana",
                )
            self.assertFalse(artifact.exists())

    def test_bad_effective_start_in_range_mode_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "u.csv"
            manifest = tmp / "u.manifest.json"
            with self.assertRaisesRegex(UniverseArtifactPublisherError, "effective_start"):
                UniverseArtifactPublisher.publish(
                    universe_name="TEST",
                    temporal_mode=UNIVERSE_MODE_RANGE,
                    rows=[("AAPL", True, "bad", "2026-02-27")],
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                    snapshot_at="2026-02-27",
                )
            self.assertFalse(artifact.exists())

    def test_range_mode_effective_start_after_end_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "u.csv"
            manifest = tmp / "u.manifest.json"
            with self.assertRaisesRegex(UniverseArtifactPublisherError, r"<= effective_end"):
                UniverseArtifactPublisher.publish(
                    universe_name="TEST",
                    temporal_mode=UNIVERSE_MODE_RANGE,
                    rows=[("AAPL", True, "2026-03-01", "2026-02-01")],
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                    snapshot_at="2026-03-01",
                )
            self.assertFalse(artifact.exists())


class UniversePublisherSnapshotAtRulesTests(unittest.TestCase):
    def test_static_mode_requires_explicit_snapshot_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with self.assertRaisesRegex(UniverseArtifactPublisherError, "snapshot_at"):
                UniverseArtifactPublisher.publish(
                    universe_name="TEST",
                    temporal_mode=UNIVERSE_MODE_STATIC,
                    rows=[("AAPL", True)],
                    artifact_path=str(tmp / "u.csv"),
                    manifest_path=str(tmp / "u.manifest.json"),
                )

    def test_range_mode_requires_explicit_snapshot_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with self.assertRaisesRegex(UniverseArtifactPublisherError, "snapshot_at"):
                UniverseArtifactPublisher.publish(
                    universe_name="TEST",
                    temporal_mode=UNIVERSE_MODE_RANGE,
                    rows=[("AAPL", True, "2026-01-01", "2026-02-27")],
                    artifact_path=str(tmp / "u.csv"),
                    manifest_path=str(tmp / "u.manifest.json"),
                )

    def test_trade_date_mode_default_snapshot_at_uses_max_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            res = UniverseArtifactPublisher.publish(
                universe_name="TEST",
                temporal_mode=UNIVERSE_MODE_TRADE_DATE,
                rows=[
                    ("AAPL", True, "2026-02-02"),
                    ("AAPL", True, "2026-02-27"),
                ],
                artifact_path=str(tmp / "u.csv"),
                manifest_path=str(tmp / "u.manifest.json"),
            )
            self.assertEqual(res.profile.metadata.get("snapshot_at"), "2026-02-27")

    def test_trade_date_mode_explicit_snapshot_at_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            res = UniverseArtifactPublisher.publish(
                universe_name="TEST",
                temporal_mode=UNIVERSE_MODE_TRADE_DATE,
                rows=[
                    ("AAPL", True, "2026-02-27"),
                ],
                artifact_path=str(tmp / "u.csv"),
                manifest_path=str(tmp / "u.manifest.json"),
                snapshot_at="2026-02-27",
            )
            self.assertEqual(res.profile.metadata.get("snapshot_at"), "2026-02-27")

    def test_trade_date_mode_explicit_snapshot_at_mismatch_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact = tmp / "u.csv"
            manifest = tmp / "u.manifest.json"
            with self.assertRaises(UniverseArtifactPublisherError) as ctx:
                UniverseArtifactPublisher.publish(
                    universe_name="TEST",
                    temporal_mode=UNIVERSE_MODE_TRADE_DATE,
                    rows=[("AAPL", True, "2026-02-27")],
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                    snapshot_at="2026-02-25",
                )
            msg = str(ctx.exception)
            self.assertIn("2026-02-25", msg)
            self.assertIn("2026-02-27", msg)
            self.assertFalse(artifact.exists())
            self.assertFalse(manifest.exists())


class UniversePublisherRoundTripTests(unittest.TestCase):
    def test_round_trip_static(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            res = UniverseArtifactPublisher.publish(
                universe_name="TEST",
                temporal_mode=UNIVERSE_MODE_STATIC,
                rows=[("AAPL", True), ("GOOG", False)],
                artifact_path=str(tmp / "u.csv"),
                manifest_path=str(tmp / "u.manifest.json"),
                snapshot_at="2026-02-27",
            )
            self.assertEqual(res.rows_written, 2)
            status = UniverseDataContract.validate_and_build_status(
                UniverseContractInput(
                    universe_name="TEST",
                    temporal_mode=UNIVERSE_MODE_STATIC,
                    profile=res.profile,
                    reference_date="2026-02-27",
                )
            )
            self.assertEqual(status.contract_health, "ok", msg=f"{status.errors} {status.warnings}")

    def test_round_trip_trade_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            res = UniverseArtifactPublisher.publish(
                universe_name="TEST",
                temporal_mode=UNIVERSE_MODE_TRADE_DATE,
                rows=[
                    ("AAPL", True, "2026-02-02"),
                    ("GOOG", False, "2026-02-27"),
                ],
                artifact_path=str(tmp / "u.csv"),
                manifest_path=str(tmp / "u.manifest.json"),
            )
            status = UniverseDataContract.validate_and_build_status(
                UniverseContractInput(
                    universe_name="TEST",
                    temporal_mode=UNIVERSE_MODE_TRADE_DATE,
                    profile=res.profile,
                    reference_date="2026-02-27",
                )
            )
            self.assertEqual(status.contract_health, "ok", msg=f"{status.errors} {status.warnings}")

    def test_round_trip_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            res = UniverseArtifactPublisher.publish(
                universe_name="TEST",
                temporal_mode=UNIVERSE_MODE_RANGE,
                rows=[
                    ("AAPL", True, "2026-01-01", "2026-02-15"),
                    ("GOOG", True, "2026-01-01", "2026-02-27"),
                ],
                artifact_path=str(tmp / "u.csv"),
                manifest_path=str(tmp / "u.manifest.json"),
                snapshot_at="2026-02-27",
            )
            status = UniverseDataContract.validate_and_build_status(
                UniverseContractInput(
                    universe_name="TEST",
                    temporal_mode=UNIVERSE_MODE_RANGE,
                    profile=res.profile,
                    reference_date="2026-02-27",
                )
            )
            self.assertEqual(status.contract_health, "ok", msg=f"{status.errors} {status.warnings}")

    def test_publisher_delegates_profile_construction_to_loader(self) -> None:
        # Audit: publisher must call the loader; we detect this by
        # patching the loader and asserting invocation. Any future
        # refactor that bypasses the loader breaks this test.
        import src.data.universe_artifact_publisher as pub_mod
        from src.data.universe_artifact_loader import UniverseArtifactLoader

        captured: dict = {}
        original_load = UniverseArtifactLoader.load

        def _tracking_load(*args, **kwargs):
            captured["called"] = True
            captured["kwargs"] = kwargs
            return original_load(*args, **kwargs)

        pub_mod.UniverseArtifactLoader.load = staticmethod(_tracking_load)  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                UniverseArtifactPublisher.publish(
                    universe_name="TEST",
                    temporal_mode=UNIVERSE_MODE_STATIC,
                    rows=[("AAPL", True)],
                    artifact_path=str(tmp / "u.csv"),
                    manifest_path=str(tmp / "u.manifest.json"),
                    snapshot_at="2026-02-27",
                )
            self.assertTrue(captured.get("called"))
            self.assertEqual(captured["kwargs"].get("temporal_mode"), UNIVERSE_MODE_STATIC)
        finally:
            pub_mod.UniverseArtifactLoader.load = original_load  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
