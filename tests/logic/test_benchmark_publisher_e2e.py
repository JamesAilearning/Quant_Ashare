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


if __name__ == "__main__":
    unittest.main()
