"""Tests for the PR8 follow-up: ``save_manifest`` helper + ingest
script wiring.

PR8 (#149) shipped the read side (``load_manifest`` +
``validate_test_end_against_bundle``). No script emitted the manifest
yet, so every walk-forward run logged "no manifest = no validation"
on the INFO line.

This verifies the helper's contract + that the Tushare ingest script
calls it after publish completes.
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.bundle_manifest import (  # noqa: E402
    MANIFEST_FILENAME,
    BundleManifestError,
    load_manifest,
    save_manifest,
)

# ---------------------------------------------------------------------------
# save_manifest contract
# ---------------------------------------------------------------------------


class SaveManifestTests(unittest.TestCase):
    def test_roundtrip_via_load_manifest(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            saved = save_manifest(
                td,
                tail_date="2026-03-06",
                instrument_count=500,
                built_at="2026-03-08T12:00:00+00:00",
            )
            self.assertTrue(saved.exists())
            self.assertEqual(saved.name, MANIFEST_FILENAME)

            loaded = load_manifest(td)
            self.assertIsNotNone(loaded)
            assert loaded is not None  # narrowing for type checker
            self.assertEqual(loaded.tail_date, date(2026, 3, 6))
            self.assertEqual(loaded.instrument_count, 500)
            self.assertEqual(loaded.built_at, "2026-03-08T12:00:00+00:00")

    def test_built_at_defaults_to_utc_now(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            before = datetime.now(tz=timezone.utc)
            save_manifest(td, tail_date="2026-03-06", instrument_count=10)
            after = datetime.now(tz=timezone.utc)
            loaded = load_manifest(td)
            assert loaded is not None
            built = datetime.fromisoformat(loaded.built_at)
            self.assertGreaterEqual(built, before.replace(microsecond=0))
            self.assertLessEqual(built, after)

    def test_accepts_date_object_for_tail(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            save_manifest(td, tail_date=date(2026, 6, 1), instrument_count=99)
            loaded = load_manifest(td)
            assert loaded is not None
            self.assertEqual(loaded.tail_date, date(2026, 6, 1))

    def test_creates_parent_directory(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            nested = Path(td) / "new" / "dir"
            self.assertFalse(nested.exists())
            save_manifest(nested, tail_date="2026-01-01", instrument_count=1)
            self.assertTrue(
                (nested / MANIFEST_FILENAME).exists(),
                "save_manifest should mkdir the parent directory",
            )

    def test_rejects_malformed_tail_date_string(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(BundleManifestError, "tail_date"):
                save_manifest(td, tail_date="03/06/2026", instrument_count=1)

    def test_rejects_non_int_instrument_count(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(BundleManifestError, "instrument_count"):
                save_manifest(
                    td,
                    tail_date="2026-03-06",
                    instrument_count="500",  # type: ignore[arg-type]
                )

    def test_rejects_bool_instrument_count(self):
        """bool is an int subclass in Python — make sure the check
        explicitly rejects it (a True/False ingest config bug shouldn't
        silently produce instrument_count=1/0)."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(BundleManifestError, "instrument_count"):
                save_manifest(
                    td,
                    tail_date="2026-03-06",
                    instrument_count=True,  # type: ignore[arg-type]
                )

    def test_atomic_write_no_tmp_leftover(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            save_manifest(td, tail_date="2026-03-06", instrument_count=1)
            tmps = list(Path(td).glob("*.tmp"))
            self.assertEqual(tmps, [])

    def test_overwrites_existing_manifest(self):
        """A re-ingest with the same provider_uri must overwrite the
        old manifest, not append or fail."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            save_manifest(td, tail_date="2026-01-01", instrument_count=10)
            save_manifest(td, tail_date="2026-06-30", instrument_count=20)
            loaded = load_manifest(td)
            assert loaded is not None
            self.assertEqual(loaded.tail_date, date(2026, 6, 30))
            self.assertEqual(loaded.instrument_count, 20)


# ---------------------------------------------------------------------------
# Ingest script integration
# ---------------------------------------------------------------------------


class IngestScriptEmitTests(unittest.TestCase):
    """Verify the script calls save_manifest after the publisher
    succeeds. We don't run real Tushare — we mock the publisher and
    assert the side effect."""

    def _make_fake_publish_result(self, output_dir: Path, *,
                                  coverage_end_date: str | None = "2026-03-06",
                                  instrument_count: int = 4128):
        # Mimic TushareQlibProviderPublishResult and the nested
        # ValidationProfile / ManifestSummary fields the script reads.
        validation_profile = SimpleNamespace(
            health="ok",
            instrument_count=instrument_count,
            row_count=1_000_000,
            coverage_start_date="2010-01-01",
            coverage_end_date=coverage_end_date,
        )
        manifest_summary = SimpleNamespace(data_adjust_mode="pre")
        return SimpleNamespace(
            output_dir=str(output_dir),
            manifest_path=str(output_dir / "publish_manifest.json"),
            validation_path=str(output_dir / "validation.json"),
            comparison_path=None,
            validation_profile=validation_profile,
            manifest=manifest_summary,
        )

    def test_main_emits_bundle_manifest_after_publish(self):
        import tempfile

        import scripts.ingest_tushare_qlib_provider as ingest_mod

        with tempfile.TemporaryDirectory() as td:
            output_dir = Path(td) / "qlib_bundle"
            output_dir.mkdir()
            fake_result = self._make_fake_publish_result(output_dir)

            # Stub _load_config to skip Tushare config parsing, and stub
            # the publisher to return our fake result.
            with patch.object(
                ingest_mod, "_load_config",
                return_value=SimpleNamespace(),
            ), patch.object(
                ingest_mod.TushareQlibProviderPublisher, "publish",
                return_value=fake_result,
            ), patch.object(sys, "argv", ["ingest", "ignored.yaml"]):
                ingest_mod.main()

            manifest_path = output_dir / MANIFEST_FILENAME
            self.assertTrue(
                manifest_path.is_file(),
                f"ingest script should have written {manifest_path}",
            )
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["tail_date"], "2026-03-06")
            self.assertEqual(payload["instrument_count"], 4128)

    def test_main_skips_emit_when_coverage_end_date_missing(self):
        """When the publisher can't determine coverage_end_date, the
        script must NOT crash — it logs a WARNING and skips the
        manifest emit, leaving the bundle usable but on the legacy
        no-manifest path."""
        import tempfile

        import scripts.ingest_tushare_qlib_provider as ingest_mod

        with tempfile.TemporaryDirectory() as td:
            output_dir = Path(td) / "qlib_bundle"
            output_dir.mkdir()
            fake_result = self._make_fake_publish_result(
                output_dir, coverage_end_date=None,
            )

            with patch.object(
                ingest_mod, "_load_config",
                return_value=SimpleNamespace(),
            ), patch.object(
                ingest_mod.TushareQlibProviderPublisher, "publish",
                return_value=fake_result,
            ), patch.object(sys, "argv", ["ingest", "ignored.yaml"]):
                # Must NOT raise.
                ingest_mod.main()

            manifest_path = output_dir / MANIFEST_FILENAME
            self.assertFalse(
                manifest_path.exists(),
                "manifest should be skipped when coverage_end_date is None",
            )

    def test_main_does_not_emit_on_publish_failure(self):
        """When the publisher raises, sys.exit(1) is called before the
        manifest-emit block runs."""
        import tempfile

        import scripts.ingest_tushare_qlib_provider as ingest_mod
        from src.data.tushare.provider_bundle import (
            TushareQlibProviderBundleError,
        )

        with tempfile.TemporaryDirectory() as td:
            output_dir = Path(td) / "qlib_bundle"
            output_dir.mkdir()

            with patch.object(
                ingest_mod, "_load_config",
                return_value=SimpleNamespace(),
            ), patch.object(
                ingest_mod.TushareQlibProviderPublisher, "publish",
                side_effect=TushareQlibProviderBundleError("publish bombed"),
            ), patch.object(sys, "argv", ["ingest", "ignored.yaml"]):
                with self.assertRaises(SystemExit) as cm:
                    ingest_mod.main()
                self.assertEqual(cm.exception.code, 1)

            manifest_path = output_dir / MANIFEST_FILENAME
            self.assertFalse(
                manifest_path.exists(),
                "no manifest should be written on publish failure",
            )


if __name__ == "__main__":
    unittest.main()
