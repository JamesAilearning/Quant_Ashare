"""Tests for the P3-4c bundle fetch-integrity stamp (read / write contract)."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.pit.bundle_integrity import (  # noqa: E402
    INTEGRITY_FILENAME,
    SCHEMA_VERSION,
    BundleIntegrityError,
    read_bundle_integrity,
    write_bundle_integrity,
)
from src.data.tushare.fetcher import FetchHole  # noqa: E402

FIXED_NOW = datetime(2026, 6, 9, 4, 30, 0, tzinfo=timezone.utc)


def _hole(endpoint="daily", unit="ts_code=600001.SH year=2020"):
    return FetchHole(
        endpoint=endpoint, unit=unit, reason_class="transient",
        attempts=5, last_error="TushareClientError: rate limit",
    )


class WriteReadTests(unittest.TestCase):

    def test_clean_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            write_bundle_integrity(bundle, built_from_holey_fetch=False, now=FIXED_NOW)
            got = read_bundle_integrity(bundle)
            assert got is not None
            self.assertEqual(got.schema_version, SCHEMA_VERSION)
            self.assertFalse(got.built_from_holey_fetch)
            self.assertEqual(got.holes, ())
            self.assertEqual(got.built_at, FIXED_NOW.isoformat())

    def test_holey_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            holes = (_hole("daily"), _hole("namechange", "file"))
            write_bundle_integrity(
                bundle, built_from_holey_fetch=True, holes=holes, now=FIXED_NOW,
            )
            got = read_bundle_integrity(bundle)
            assert got is not None
            self.assertTrue(got.built_from_holey_fetch)
            self.assertEqual(len(got.holes), 2)
            self.assertEqual({h.endpoint for h in got.holes}, {"daily", "namechange"})
            self.assertEqual(got.holes[0].attempts, 5)
            self.assertEqual(got.holes[0].last_error, "TushareClientError: rate limit")
            # valid JSON on disk with the version stamp
            raw = json.loads((bundle / INTEGRITY_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(raw["schema_version"], SCHEMA_VERSION)

    def test_default_timestamp_is_system_clock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            write_bundle_integrity(bundle, built_from_holey_fetch=False)  # now=None
            got = read_bundle_integrity(bundle)
            assert got is not None
            self.assertTrue(got.built_at)
            self.assertNotEqual(got.built_at, FIXED_NOW.isoformat())

    def test_atomic_no_tmp_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            write_bundle_integrity(bundle, built_from_holey_fetch=False, now=FIXED_NOW)
            self.assertEqual(list(bundle.glob("*.tmp")), [])


class ReadFailLoudTests(unittest.TestCase):

    def test_missing_stamp_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(read_bundle_integrity(Path(tmp)))

    def test_non_object_fails_loud(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / INTEGRITY_FILENAME).write_text(json.dumps([1, 2]), encoding="utf-8")
            with self.assertRaisesRegex(BundleIntegrityError, "not a JSON object"):
                read_bundle_integrity(Path(tmp))

    def test_non_utf8_fails_loud(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / INTEGRITY_FILENAME).write_bytes(b"\xff\xfe not utf-8 \x80")
            with self.assertRaisesRegex(BundleIntegrityError, "unreadable"):
                read_bundle_integrity(Path(tmp))

    def test_unknown_schema_fails_loud(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / INTEGRITY_FILENAME).write_text(
                json.dumps({"schema_version": 999, "built_from_holey_fetch": False,
                            "built_at": "x", "holes": []}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(BundleIntegrityError, "schema_version"):
                read_bundle_integrity(Path(tmp))

    def test_missing_field_fails_loud(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # valid version but missing built_from_holey_fetch
            (Path(tmp) / INTEGRITY_FILENAME).write_text(
                json.dumps({"schema_version": SCHEMA_VERSION, "built_at": "x", "holes": []}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(BundleIntegrityError, "missing required field"):
                read_bundle_integrity(Path(tmp))

    def test_wrong_field_type_fails_loud(self) -> None:
        # codex P2: a present-but-wrong-type field (int for the bool) must fail
        # loud, not be read as a falsy/clean stamp.
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / INTEGRITY_FILENAME).write_text(
                json.dumps({
                    "schema_version": SCHEMA_VERSION, "built_from_holey_fetch": 0,
                    "built_at": "x", "holes": [],
                }),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(BundleIntegrityError, "must be bool"):
                read_bundle_integrity(Path(tmp))

    def test_wrong_hole_field_type_fails_loud(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / INTEGRITY_FILENAME).write_text(
                json.dumps({
                    "schema_version": SCHEMA_VERSION, "built_from_holey_fetch": True,
                    "built_at": "x",
                    "holes": [{
                        "endpoint": "daily", "unit": "u", "reason_class": "t",
                        "attempts": "five", "last_error": "e",  # attempts not int
                    }],
                }),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(BundleIntegrityError, "must be int"):
                read_bundle_integrity(Path(tmp))

    def test_clean_stamp_listing_holes_fails_loud(self) -> None:
        # codex P2: a stamp claiming clean (built_from_holey_fetch=false) while
        # listing holes is internally inconsistent — fail loud, don't accept it as
        # clean (the recommend gate keys on the flag alone).
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / INTEGRITY_FILENAME).write_text(
                json.dumps({
                    "schema_version": SCHEMA_VERSION, "built_from_holey_fetch": False,
                    "built_at": "x",
                    "holes": [{
                        "endpoint": "daily", "unit": "u", "reason_class": "t",
                        "attempts": 1, "last_error": "e",
                    }],
                }),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(BundleIntegrityError, "inconsistent"):
                read_bundle_integrity(Path(tmp))


if __name__ == "__main__":
    unittest.main()
