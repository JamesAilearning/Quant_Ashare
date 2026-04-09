"""End-to-end test: publisher -> loader -> benchmark contract, real qlib.

This test initializes canonical qlib runtime against the local data
bundle and performs the full round trip. It skips gracefully if the
local data bundle is not present so other machines / CI stay green.

Note: the user's local data bundle does not contain the SH000300 index
instrument directly, so the test uses SH600000 as a publishable stable
stock code. The benchmark data contract's ``benchmark_code`` field is
a free-form caller-supplied label; this choice does not violate
contract semantics.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.benchmark_data_contract import (  # noqa: E402
    BenchmarkContractInput,
    BenchmarkDataContract,
)
from src.core.qlib_runtime import (  # noqa: E402
    QlibRuntimeConfig,
    _reset_canonical_qlib_runtime_for_tests,
    init_qlib_canonical,
    is_canonical_qlib_initialized,
)
from src.data.benchmark_artifact_publisher import (  # noqa: E402
    BenchmarkArtifactPublisher,
    BenchmarkArtifactPublisherError,
)


LOCAL_QLIB_DATA = Path(r"D:/qlib_data/my_cn_data")
TEST_BENCHMARK_CODE = "SH600000"
# Use the widest trading-day window available in the local bundle so the
# loader's calendar-free coverage approximation is well sampled.
TEST_START = "2026-01-05"
TEST_END = "2026-02-27"


def _qlib_importable() -> bool:
    try:
        import qlib  # noqa: F401
        return True
    except ImportError:
        return False


def _local_bundle_available() -> bool:
    return LOCAL_QLIB_DATA.is_dir() and (LOCAL_QLIB_DATA / "calendars").is_dir()


@unittest.skipUnless(_qlib_importable(), "qlib not installed in this environment")
@unittest.skipUnless(
    _local_bundle_available(),
    f"local qlib data bundle not present at {LOCAL_QLIB_DATA}",
)
class BenchmarkPublisherE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _reset_canonical_qlib_runtime_for_tests()
        init_qlib_canonical(
            QlibRuntimeConfig(
                provider_uri=str(LOCAL_QLIB_DATA),
                region="cn",
            )
        )

    @classmethod
    def tearDownClass(cls) -> None:
        _reset_canonical_qlib_runtime_for_tests()

    def test_round_trip_publisher_to_contract_is_ok(self) -> None:
        self.assertTrue(is_canonical_qlib_initialized())
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            artifact_path = tmp_dir / f"{TEST_BENCHMARK_CODE}.csv"
            manifest_path = tmp_dir / f"{TEST_BENCHMARK_CODE}.csv.manifest.json"

            result = BenchmarkArtifactPublisher.publish(
                benchmark_code=TEST_BENCHMARK_CODE,
                start_time=TEST_START,
                end_time=TEST_END,
                artifact_path=str(artifact_path),
                manifest_path=str(manifest_path),
                reference_date=TEST_END,
            )

            self.assertGreater(result.rows_written, 0)
            self.assertTrue(artifact_path.is_file())
            self.assertTrue(manifest_path.is_file())

            # The manifest must carry all required provenance fields.
            manifest_text = manifest_path.read_text(encoding="utf-8")
            for field in (
                "benchmark_code",
                "source_name",
                "source_uri",
                "snapshot_at",
                "schema_version",
            ):
                self.assertIn(field, manifest_text)

            profile = result.profile
            self.assertTrue(profile.artifact_present)
            self.assertTrue(profile.manifest_present)
            self.assertEqual(profile.metadata.get("benchmark_code"), TEST_BENCHMARK_CODE)
            self.assertIn("date", profile.columns_present)
            self.assertIn("close", profile.columns_present)

            status = BenchmarkDataContract.validate_and_build_status(
                BenchmarkContractInput(
                    benchmark_code=TEST_BENCHMARK_CODE,
                    profile=profile,
                    reference_date=TEST_END,
                )
            )
            self.assertEqual(
                status.contract_health,
                "ok",
                msg=f"errors={status.errors} warnings={status.warnings}",
            )

    def test_snapshot_at_is_derived_from_actual_data(self) -> None:
        # end_time falls on a Saturday; the qlib provider returns rows
        # ending on the preceding Friday. The publisher must write the
        # actual max row date (Friday) into snapshot_at, not end_time.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            artifact_path = tmp_dir / f"{TEST_BENCHMARK_CODE}.csv"
            manifest_path = tmp_dir / f"{TEST_BENCHMARK_CODE}.csv.manifest.json"

            result = BenchmarkArtifactPublisher.publish(
                benchmark_code=TEST_BENCHMARK_CODE,
                start_time=TEST_START,
                end_time="2026-02-28",  # Saturday, intentionally non-trading
                artifact_path=str(artifact_path),
                manifest_path=str(manifest_path),
                reference_date="2026-02-28",
            )

            self.assertEqual(
                result.profile.metadata.get("snapshot_at"),
                "2026-02-27",
                msg="snapshot_at must reflect the actual max row date, not end_time",
            )

            status = BenchmarkDataContract.validate_and_build_status(
                BenchmarkContractInput(
                    benchmark_code=TEST_BENCHMARK_CODE,
                    profile=result.profile,
                    reference_date="2026-02-28",
                )
            )
            self.assertEqual(
                status.contract_health,
                "ok",
                msg=f"errors={status.errors} warnings={status.warnings}",
            )

    def test_explicit_snapshot_at_mismatch_raises(self) -> None:
        # Caller passes a snapshot_at that does not match the real max
        # row date. The publisher must reject at the boundary, not let
        # the loader's strict-equality check surface the error far from
        # the cause.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            artifact_path = tmp_dir / f"{TEST_BENCHMARK_CODE}.csv"
            manifest_path = tmp_dir / f"{TEST_BENCHMARK_CODE}.csv.manifest.json"

            with self.assertRaises(BenchmarkArtifactPublisherError) as ctx:
                BenchmarkArtifactPublisher.publish(
                    benchmark_code=TEST_BENCHMARK_CODE,
                    start_time=TEST_START,
                    end_time=TEST_END,
                    artifact_path=str(artifact_path),
                    manifest_path=str(manifest_path),
                    snapshot_at="2026-02-25",  # intentionally wrong
                )

            message = str(ctx.exception)
            self.assertIn("2026-02-25", message)
            self.assertIn("2026-02-27", message)
            # And nothing was written to disk before the rejection.
            self.assertFalse(artifact_path.exists())
            self.assertFalse(manifest_path.exists())

    def test_empty_query_window_raises(self) -> None:
        # A weekend-only window should yield zero rows from the daily provider.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            with self.assertRaises(BenchmarkArtifactPublisherError):
                BenchmarkArtifactPublisher.publish(
                    benchmark_code=TEST_BENCHMARK_CODE,
                    start_time="2026-02-07",  # Saturday
                    end_time="2026-02-08",    # Sunday
                    artifact_path=str(tmp_dir / "empty.csv"),
                    manifest_path=str(tmp_dir / "empty.manifest.json"),
                )


class BenchmarkPublisherInitGuardTests(unittest.TestCase):
    """These tests do NOT require the local data bundle."""

    def setUp(self) -> None:
        _reset_canonical_qlib_runtime_for_tests()

    def tearDown(self) -> None:
        _reset_canonical_qlib_runtime_for_tests()

    def test_publish_without_canonical_init_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            with self.assertRaises(BenchmarkArtifactPublisherError) as ctx:
                BenchmarkArtifactPublisher.publish(
                    benchmark_code=TEST_BENCHMARK_CODE,
                    start_time=TEST_START,
                    end_time=TEST_END,
                    artifact_path=str(tmp_dir / "x.csv"),
                    manifest_path=str(tmp_dir / "x.manifest.json"),
                )
            self.assertIn("Canonical qlib runtime is not initialized", str(ctx.exception))

    def test_structural_misuse_raises(self) -> None:
        _reset_canonical_qlib_runtime_for_tests()
        # Even with empty arg, the init guard fires first; that's acceptable
        # because it is the first line of defense. Validate that by forcing
        # init and then passing empty args.
        if not (_qlib_importable() and _local_bundle_available()):
            self.skipTest("requires local qlib bundle to exercise arg validation")
        init_qlib_canonical(
            QlibRuntimeConfig(provider_uri=str(LOCAL_QLIB_DATA), region="cn")
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                with self.assertRaises(BenchmarkArtifactPublisherError):
                    BenchmarkArtifactPublisher.publish(
                        benchmark_code="",
                        start_time=TEST_START,
                        end_time=TEST_END,
                        artifact_path=str(tmp_dir / "x.csv"),
                        manifest_path=str(tmp_dir / "x.manifest.json"),
                    )
        finally:
            _reset_canonical_qlib_runtime_for_tests()


class BenchmarkPublisherSnapshotAtDerivationTests(unittest.TestCase):
    """Unit tests for snapshot_at derivation that do NOT require qlib bundle.

    Patches the publisher's internal helpers so the test exercises the
    snapshot_at logic in isolation. This is the only place we mock
    publisher internals; the round-trip tests above remain hermetic.
    """

    def setUp(self) -> None:
        _reset_canonical_qlib_runtime_for_tests()
        # Inject a fake canonical config so the init guard passes.
        # Use module-level state directly because real init_qlib_canonical
        # would require a real qlib install.
        from src.core import qlib_runtime as _rt
        _rt._CANONICAL_CONFIG = QlibRuntimeConfig(provider_uri="fake://x", region="cn")
        _rt._CANONICAL_QLIB_INITIALIZED = True

    def tearDown(self) -> None:
        _reset_canonical_qlib_runtime_for_tests()

    def _patch_publisher(self, fake_rows):
        """Replace _flatten_close_frame and the qlib import with stubs."""
        import src.data.benchmark_artifact_publisher as pub_mod
        # Inject a fake qlib.data module via sys.modules so the
        # `from qlib.data import D` inside publish() resolves to our stub
        # without requiring a real qlib install.
        import types
        fake_qlib_data = types.ModuleType("qlib.data")
        fake_qlib = types.ModuleType("qlib")

        class _FakeD:
            @staticmethod
            def features(codes, fields, start_time, end_time):
                return "fake-frame"

            @staticmethod
            def calendar(freq="day"):
                # Provide a calendar covering a wide window so the
                # publisher's injected QlibTradingCalendar can fetch
                # something real-shaped during the round-trip load.
                from datetime import date as _d, timedelta as _td
                start = _d(2025, 1, 1)
                end = _d(2027, 12, 31)
                days: list = []
                cursor = start
                while cursor <= end:
                    if cursor.weekday() < 5:  # Mon-Fri only
                        days.append(cursor)
                    cursor = cursor + _td(days=1)
                return days

        fake_qlib_data.D = _FakeD
        sys.modules.setdefault("qlib", fake_qlib)
        sys.modules["qlib.data"] = fake_qlib_data

        original_flatten = pub_mod.BenchmarkArtifactPublisher._flatten_close_frame
        pub_mod.BenchmarkArtifactPublisher._flatten_close_frame = staticmethod(  # type: ignore[assignment]
            lambda frame: list(fake_rows)
        )
        return original_flatten

    def _restore_publisher(self, original_flatten) -> None:
        import src.data.benchmark_artifact_publisher as pub_mod
        pub_mod.BenchmarkArtifactPublisher._flatten_close_frame = original_flatten  # type: ignore[assignment]
        sys.modules.pop("qlib.data", None)
        # Leave fake "qlib" placeholder in sys.modules harmless; it has
        # no submodules and no other test imports it directly.

    def test_default_snapshot_at_uses_actual_max_row(self) -> None:
        rows = [("2026-02-26", 100.0), ("2026-02-27", 101.0)]
        original = self._patch_publisher(rows)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                result = BenchmarkArtifactPublisher.publish(
                    benchmark_code="FAKE",
                    start_time="2026-02-01",
                    end_time="2026-02-28",  # Saturday, beyond actual data
                    artifact_path=str(tmp_dir / "fake.csv"),
                    manifest_path=str(tmp_dir / "fake.manifest.json"),
                )
            self.assertEqual(result.profile.metadata.get("snapshot_at"), "2026-02-27")
        finally:
            self._restore_publisher(original)

    def test_explicit_snapshot_at_match_is_accepted(self) -> None:
        rows = [("2026-02-26", 100.0), ("2026-02-27", 101.0)]
        original = self._patch_publisher(rows)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                result = BenchmarkArtifactPublisher.publish(
                    benchmark_code="FAKE",
                    start_time="2026-02-01",
                    end_time="2026-02-27",
                    artifact_path=str(tmp_dir / "fake.csv"),
                    manifest_path=str(tmp_dir / "fake.manifest.json"),
                    snapshot_at="2026-02-27",
                )
            self.assertEqual(result.profile.metadata.get("snapshot_at"), "2026-02-27")
        finally:
            self._restore_publisher(original)

    def test_publish_passes_calendar_to_loader(self) -> None:
        # Publisher must inject a QlibTradingCalendar into its internal
        # loader call so the round-trip profile's coverage_ratio uses
        # the real qlib calendar, not the 0.63 fallback.
        rows = [("2026-02-26", 100.0), ("2026-02-27", 101.0)]
        original_flatten = self._patch_publisher(rows)
        import src.data.benchmark_artifact_publisher as pub_mod
        from src.data.trading_calendar import QlibTradingCalendar as _Q

        captured: dict = {}
        original_load = pub_mod.BenchmarkArtifactLoader.load

        def fake_load(**kwargs):
            captured.update(kwargs)
            return original_load(**kwargs)

        pub_mod.BenchmarkArtifactLoader.load = staticmethod(fake_load)  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                pub_mod.BenchmarkArtifactPublisher.publish(
                    benchmark_code="FAKE",
                    start_time="2026-02-01",
                    end_time="2026-02-27",
                    artifact_path=str(tmp_dir / "fake.csv"),
                    manifest_path=str(tmp_dir / "fake.manifest.json"),
                )
            self.assertIn("calendar", captured)
            self.assertIsNotNone(captured["calendar"])
            self.assertIsInstance(captured["calendar"], _Q)
        finally:
            pub_mod.BenchmarkArtifactLoader.load = original_load  # type: ignore[assignment]
            self._restore_publisher(original_flatten)

    def test_explicit_snapshot_at_mismatch_raises_at_boundary(self) -> None:
        rows = [("2026-02-26", 100.0), ("2026-02-27", 101.0)]
        original = self._patch_publisher(rows)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                artifact_path = tmp_dir / "fake.csv"
                manifest_path = tmp_dir / "fake.manifest.json"
                with self.assertRaises(BenchmarkArtifactPublisherError) as ctx:
                    BenchmarkArtifactPublisher.publish(
                        benchmark_code="FAKE",
                        start_time="2026-02-01",
                        end_time="2026-02-27",
                        artifact_path=str(artifact_path),
                        manifest_path=str(manifest_path),
                        snapshot_at="2026-02-25",
                    )
                message = str(ctx.exception)
                self.assertIn("2026-02-25", message)
                self.assertIn("2026-02-27", message)
                # No file was written before the rejection.
                self.assertFalse(artifact_path.exists())
                self.assertFalse(manifest_path.exists())
        finally:
            self._restore_publisher(original)


class BenchmarkPublisherFlattenFrameTests(unittest.TestCase):
    """Direct tests for _flatten_close_frame's narrow exception handling.

    These do not require qlib or the canonical runtime — they invoke
    the staticmethod directly with hand-crafted duck-typed inputs.
    """

    def test_none_returns_empty(self) -> None:
        self.assertEqual(BenchmarkArtifactPublisher._flatten_close_frame(None), [])

    def test_object_without_reset_index_returns_empty(self) -> None:
        class _Bare:
            empty = False

        self.assertEqual(
            BenchmarkArtifactPublisher._flatten_close_frame(_Bare()), []
        )

    def test_empty_flag_short_circuits(self) -> None:
        class _Empty:
            empty = True

        self.assertEqual(
            BenchmarkArtifactPublisher._flatten_close_frame(_Empty()), []
        )

    def test_minimal_duck_typed_frame_is_parsed(self) -> None:
        from datetime import date as _d

        class _Record(dict):
            def __getitem__(self, key):
                return super().__getitem__(key)

        class _FakeFrame:
            empty = False
            columns = ["datetime", "$close"]

            def reset_index(self):
                return self

            def iterrows(self):
                yield 0, _Record({"datetime": _d(2026, 2, 27), "$close": 101.5})
                yield 1, _Record({"datetime": _d(2026, 2, 26), "$close": 100.0})

        rows = BenchmarkArtifactPublisher._flatten_close_frame(_FakeFrame())
        # Sorted ascending by ISO date.
        self.assertEqual(rows, [("2026-02-26", 100.0), ("2026-02-27", 101.5)])

    def test_unexpected_exception_is_not_swallowed(self) -> None:
        # If reset_index raises something outside the narrow allowlist,
        # the exception MUST propagate so the bug is visible at the
        # actual call site instead of being misreported as "no rows".
        class _BoomFrame:
            empty = False

            def reset_index(self):
                raise RuntimeError("synthetic bug")

        with self.assertRaises(RuntimeError):
            BenchmarkArtifactPublisher._flatten_close_frame(_BoomFrame())


if __name__ == "__main__":
    unittest.main()
