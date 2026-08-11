"""Tests for ``src.data.bundle_manifest``.

Dimensional matrix from
``openspec/changes/add-config-robustness``:

- load_manifest: missing file → None
- load_manifest: well-formed JSON → BundleManifest
- load_manifest: malformed JSON → BundleManifestError
- load_manifest: missing required field → BundleManifestError
- validate: test_end < tail_date → passes silently
- validate: test_end == tail_date → passes (inclusive boundary)
- validate: test_end > tail_date, soft=False → raises BundleStaleError
  with both dates in message
- validate: test_end > tail_date, soft=True → logs WARNING, no raise
- validate: no manifest → INFO log, no raise
- validate: QLIB_SKIP_BUNDLE_VALIDATION=1 → INFO log, bypass
- validate: malformed test_end string → BundleManifestError
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.bundle_manifest import (  # noqa: E402
    SKIP_ENV_VAR,
    BundleManifest,
    BundleManifestError,
    BundleStaleError,
    compute_bundle_content_hash,
    compute_bundle_data_sha256,
    load_manifest,
    validate_test_end_against_bundle,
)


def _write_manifest(provider_dir: Path, body: dict | str) -> Path:
    """Write a bundle_manifest.json under *provider_dir*."""
    p = provider_dir / "bundle_manifest.json"
    if isinstance(body, dict):
        p.write_text(json.dumps(body), encoding="utf-8")
    else:
        p.write_text(body, encoding="utf-8")
    return p


_VALID_MANIFEST_BODY: dict = {
    "provider_uri": "D:/qlib_data/my_cn_data",
    "tail_date": "2026-03-06",
    "instrument_count": 4128,
    "built_at": "2026-03-08T12:34:56Z",
}


class LoadManifestTests(unittest.TestCase):
    def test_load_manifest_missing_file_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_manifest(Path(tmp)))

    def test_load_manifest_well_formed_returns_dataclass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_manifest(Path(tmp), _VALID_MANIFEST_BODY)
            manifest = load_manifest(Path(tmp))
        self.assertIsInstance(manifest, BundleManifest)
        assert manifest is not None  # for type narrowing
        self.assertEqual(manifest.provider_uri, "D:/qlib_data/my_cn_data")
        self.assertEqual(manifest.tail_date, date(2026, 3, 6))
        self.assertEqual(manifest.instrument_count, 4128)
        self.assertEqual(manifest.built_at, "2026-03-08T12:34:56Z")

    def test_load_manifest_malformed_json_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_manifest(Path(tmp), "{not valid json")
            with self.assertRaises(BundleManifestError) as ctx:
                load_manifest(Path(tmp))
        self.assertIn("bundle_manifest.json", str(ctx.exception))

    def test_load_manifest_missing_required_field_raises(self) -> None:
        body = dict(_VALID_MANIFEST_BODY)
        body.pop("tail_date")
        with tempfile.TemporaryDirectory() as tmp:
            _write_manifest(Path(tmp), body)
            with self.assertRaises(BundleManifestError) as ctx:
                load_manifest(Path(tmp))
        self.assertIn("tail_date", str(ctx.exception))

    def test_load_manifest_non_dict_root_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_manifest(Path(tmp), "[1, 2, 3]")
            with self.assertRaises(BundleManifestError):
                load_manifest(Path(tmp))

    def test_load_manifest_non_iso_tail_date_raises(self) -> None:
        body = dict(_VALID_MANIFEST_BODY)
        body["tail_date"] = "March 6, 2026"
        with tempfile.TemporaryDirectory() as tmp:
            _write_manifest(Path(tmp), body)
            with self.assertRaises(BundleManifestError) as ctx:
                load_manifest(Path(tmp))
        self.assertIn("tail_date", str(ctx.exception))

    def test_load_manifest_non_int_instrument_count_raises(self) -> None:
        body = dict(_VALID_MANIFEST_BODY)
        body["instrument_count"] = "many"
        with tempfile.TemporaryDirectory() as tmp:
            _write_manifest(Path(tmp), body)
            with self.assertRaises(BundleManifestError) as ctx:
                load_manifest(Path(tmp))
        self.assertIn("instrument_count", str(ctx.exception))

    # ----------------------------------------------------------------
    # Codex PR #149 P2 regression: ``provider_uri`` / ``built_at``
    # were previously coerced with ``str(...)``, so ``null`` /
    # objects passed validation as ``"None"`` / ``"{'a': 1}"``.
    # ----------------------------------------------------------------

    def test_load_manifest_null_provider_uri_raises(self) -> None:
        body = dict(_VALID_MANIFEST_BODY)
        body["provider_uri"] = None
        with tempfile.TemporaryDirectory() as tmp:
            _write_manifest(Path(tmp), body)
            with self.assertRaises(BundleManifestError) as ctx:
                load_manifest(Path(tmp))
        self.assertIn("provider_uri", str(ctx.exception))
        self.assertIn("NoneType", str(ctx.exception))

    def test_load_manifest_dict_provider_uri_raises(self) -> None:
        body = dict(_VALID_MANIFEST_BODY)
        body["provider_uri"] = {"path": "/qlib"}
        with tempfile.TemporaryDirectory() as tmp:
            _write_manifest(Path(tmp), body)
            with self.assertRaises(BundleManifestError) as ctx:
                load_manifest(Path(tmp))
        self.assertIn("provider_uri", str(ctx.exception))
        self.assertIn("dict", str(ctx.exception))

    def test_load_manifest_int_provider_uri_raises(self) -> None:
        """A YAML/JSON int (``42``) must NOT be coerced to ``"42"``."""
        body = dict(_VALID_MANIFEST_BODY)
        body["provider_uri"] = 42
        with tempfile.TemporaryDirectory() as tmp:
            _write_manifest(Path(tmp), body)
            with self.assertRaises(BundleManifestError) as ctx:
                load_manifest(Path(tmp))
        self.assertIn("provider_uri", str(ctx.exception))
        self.assertIn("int", str(ctx.exception))

    def test_load_manifest_null_built_at_raises(self) -> None:
        body = dict(_VALID_MANIFEST_BODY)
        body["built_at"] = None
        with tempfile.TemporaryDirectory() as tmp:
            _write_manifest(Path(tmp), body)
            with self.assertRaises(BundleManifestError) as ctx:
                load_manifest(Path(tmp))
        self.assertIn("built_at", str(ctx.exception))
        self.assertIn("NoneType", str(ctx.exception))

    def test_load_manifest_list_built_at_raises(self) -> None:
        body = dict(_VALID_MANIFEST_BODY)
        body["built_at"] = ["2026-03-06"]
        with tempfile.TemporaryDirectory() as tmp:
            _write_manifest(Path(tmp), body)
            with self.assertRaises(BundleManifestError) as ctx:
                load_manifest(Path(tmp))
        self.assertIn("built_at", str(ctx.exception))
        self.assertIn("list", str(ctx.exception))


class ValidateTestEndTests(unittest.TestCase):
    def setUp(self) -> None:
        # Snapshot SKIP env var; tests mutate it freely
        self._saved_skip = os.environ.pop(SKIP_ENV_VAR, None)

    def tearDown(self) -> None:
        os.environ.pop(SKIP_ENV_VAR, None)
        if self._saved_skip is not None:
            os.environ[SKIP_ENV_VAR] = self._saved_skip

    def test_test_end_before_tail_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_manifest(Path(tmp), _VALID_MANIFEST_BODY)
            # Should NOT raise
            validate_test_end_against_bundle(tmp, "2026-02-28")

    def test_test_end_equal_to_tail_passes(self) -> None:
        """Boundary: test_end == tail_date is inclusive and OK."""
        with tempfile.TemporaryDirectory() as tmp:
            _write_manifest(Path(tmp), _VALID_MANIFEST_BODY)
            # tail_date is 2026-03-06; passing the exact same date passes
            validate_test_end_against_bundle(tmp, "2026-03-06")

    def test_test_end_after_tail_hard_raises_with_both_dates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_manifest(Path(tmp), _VALID_MANIFEST_BODY)
            with self.assertRaises(BundleStaleError) as ctx:
                validate_test_end_against_bundle(tmp, "2026-04-30")
        msg = str(ctx.exception)
        self.assertIn("2026-04-30", msg)  # requested
        self.assertIn("2026-03-06", msg)  # bundle tail

    def test_test_end_after_tail_soft_logs_warning_no_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_manifest(Path(tmp), _VALID_MANIFEST_BODY)
            with self.assertLogs(
                "src.data.bundle_manifest", level="WARNING"
            ) as captured:
                validate_test_end_against_bundle(
                    tmp, "2026-04-30", soft=True
                )
        warnings = [r for r in captured.records if r.levelno == logging.WARNING]
        self.assertEqual(len(warnings), 1)
        self.assertIn("2026-04-30", warnings[0].getMessage())
        self.assertIn("2026-03-06", warnings[0].getMessage())

    def test_no_identity_logs_info_and_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # No bundle identity source written (neither _fetch_integrity.json
            # identity nor bundle_manifest.json) → INFO + pass (PR-G+I).
            with self.assertLogs(
                "src.data.bundle_manifest", level="INFO"
            ) as captured:
                validate_test_end_against_bundle(tmp, "2026-04-30")
        infos = [
            r.getMessage()
            for r in captured.records
            if r.levelno == logging.INFO
        ]
        self.assertTrue(
            any("skipping bundle freshness validation" in m for m in infos),
            f"expected an INFO log about skipping the freshness check; got {infos}",
        )

    def test_skip_env_var_bypasses_check(self) -> None:
        os.environ[SKIP_ENV_VAR] = "1"
        with tempfile.TemporaryDirectory() as tmp:
            # Manifest IS present and would otherwise hard-fail
            _write_manifest(Path(tmp), _VALID_MANIFEST_BODY)
            with self.assertLogs(
                "src.data.bundle_manifest", level="INFO"
            ) as captured:
                validate_test_end_against_bundle(tmp, "2026-04-30")
        infos = [
            r.getMessage()
            for r in captured.records
            if r.levelno == logging.INFO
        ]
        self.assertTrue(
            any("Bundle validation skipped" in m for m in infos),
            f"expected INFO log about skip; got {infos}",
        )

    def test_skip_env_var_true_value_accepted(self) -> None:
        os.environ[SKIP_ENV_VAR] = "true"
        with tempfile.TemporaryDirectory() as tmp:
            _write_manifest(Path(tmp), _VALID_MANIFEST_BODY)
            # Should not raise — env var bypass
            validate_test_end_against_bundle(tmp, "2026-04-30")

    def test_validate_accepts_date_object_for_test_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_manifest(Path(tmp), _VALID_MANIFEST_BODY)
            # Same as the string case, but pass a date object
            validate_test_end_against_bundle(tmp, date(2026, 2, 28))
            with self.assertRaises(BundleStaleError):
                validate_test_end_against_bundle(tmp, date(2026, 4, 30))

    def test_malformed_test_end_string_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_manifest(Path(tmp), _VALID_MANIFEST_BODY)
            with self.assertRaises(BundleManifestError):
                validate_test_end_against_bundle(tmp, "not-a-date")

    def test_malformed_manifest_surfaces_as_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_manifest(Path(tmp), "{not valid")
            with self.assertRaises(BundleManifestError):
                validate_test_end_against_bundle(tmp, "2026-02-28")


def _seed_bundle_data(root: Path) -> None:
    for rel, content in (
        ("calendars/day.txt", b"2019-01-02\n2019-01-03\n"),
        ("instruments/all.txt", b"SH600000\t2019-01-02\t2019-01-03\n"),
        ("features/sh600000/close.day.bin", b"\x01\x02\x03"),
        ("features/sh600000/open.day.bin", b"\x04\x05\x06"),
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


class ComputeBundleDataSha256Tests(unittest.TestCase):
    """The FULL-data digest: every panel-producing byte moves it."""

    def test_deterministic_across_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_bundle_data(Path(tmp))
            self.assertEqual(
                compute_bundle_data_sha256(tmp), compute_bundle_data_sha256(tmp))
            self.assertTrue(compute_bundle_data_sha256(tmp).startswith("sha256:"))

    def test_feature_bin_edit_moves_digest_calendar_hash_does_not(self):
        # The reason this digest exists: an in-place price correction is
        # invisible to the calendar-only bundle identity.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_bundle_data(root)
            before_data = compute_bundle_data_sha256(tmp)
            before_cal = compute_bundle_content_hash(tmp)
            (root / "features/sh600000/close.day.bin").write_bytes(b"\xff\x02\x03")
            self.assertNotEqual(compute_bundle_data_sha256(tmp), before_data)
            self.assertEqual(compute_bundle_content_hash(tmp), before_cal)

    def test_membership_edit_moves_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_bundle_data(root)
            before = compute_bundle_data_sha256(tmp)
            (root / "instruments/all.txt").write_bytes(
                b"SH600000\t2019-01-02\t2019-01-03\n"
                b"SH600001\t2019-01-02\t2019-01-03\n")
            self.assertNotEqual(compute_bundle_data_sha256(tmp), before)

    def test_added_feature_file_moves_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_bundle_data(root)
            before = compute_bundle_data_sha256(tmp)
            (root / "features/sh600001").mkdir()
            (root / "features/sh600001/close.day.bin").write_bytes(b"\x09")
            self.assertNotEqual(compute_bundle_data_sha256(tmp), before)

    def test_missing_data_directory_raises(self):
        import shutil

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_bundle_data(root)
            shutil.rmtree(root / "features")
            with self.assertRaises(BundleManifestError):
                compute_bundle_data_sha256(tmp)

    def test_symlinked_feature_directory_is_refused(self):
        # External finding on #415 r6: os.walk(followlinks=False) lists a
        # symlinked directory but never descends, so its target bins
        # would be read by qlib yet invisible to the digest. Refused
        # explicitly rather than followed (cycle risk, out-of-tree bytes).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_bundle_data(root)
            target = root / "elsewhere"
            target.mkdir()
            (target / "close.day.bin").write_bytes(b"\x0a")
            try:
                os.symlink(target, root / "features" / "sh600002",
                           target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation not permitted on this box")
            with self.assertRaises(BundleManifestError) as ctx:
                compute_bundle_data_sha256(tmp)
            self.assertIn("symlinked directory", str(ctx.exception))

    def test_symlinked_top_level_data_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_bundle_data(root)
            import shutil

            materialized = root / "_features_real"
            shutil.move(str(root / "features"), str(materialized))
            try:
                os.symlink(materialized, root / "features",
                           target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation not permitted on this box")
            with self.assertRaises(BundleManifestError) as ctx:
                compute_bundle_data_sha256(tmp)
            self.assertIn("symlinked", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
