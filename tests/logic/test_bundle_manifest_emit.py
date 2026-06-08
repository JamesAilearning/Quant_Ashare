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
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.bundle_manifest import (  # noqa: E402
    CONTENT_HASH_PREFIX,
    MANIFEST_FILENAME,
    SKIP_ENV_VAR,
    BundleContentHashMismatchError,
    BundleManifestError,
    compute_bundle_content_hash,
    load_manifest,
    save_manifest,
    verify_content_hash,
)


def _write_calendar(provider_dir: Path, *, dates: list[str] | None = None) -> Path:
    """Write a minimal ``calendars/day.txt`` under *provider_dir*.

    Many of the new tests need an actual calendar file so the content-
    hash check can run. The content is just a few ISO dates; the
    integrity check only cares about the bytes, not the semantic
    contents.
    """
    cal_dir = provider_dir / "calendars"
    cal_dir.mkdir(parents=True, exist_ok=True)
    cal_path = cal_dir / "day.txt"
    body = "\n".join(dates or ["2026-01-02", "2026-01-03", "2026-01-06"]) + "\n"
    cal_path.write_text(body, encoding="utf-8")
    return cal_path


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
# compute_bundle_content_hash
# ---------------------------------------------------------------------------


class ComputeBundleContentHashTests(unittest.TestCase):
    """Tests for the SHA-256 fingerprint helper. We deliberately
    write small calendar files by hand — the hash only cares about
    bytes, not semantic dates."""

    def test_returns_sha256_prefixed_64_hex(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            _write_calendar(Path(td))
            digest = compute_bundle_content_hash(td)
        self.assertTrue(digest.startswith(CONTENT_HASH_PREFIX))
        hex_part = digest[len(CONTENT_HASH_PREFIX):]
        self.assertEqual(len(hex_part), 64)
        # All lower-case hex.
        self.assertTrue(all(c in "0123456789abcdef" for c in hex_part))

    def test_is_deterministic_for_same_bytes(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            _write_calendar(Path(td))
            d1 = compute_bundle_content_hash(td)
            d2 = compute_bundle_content_hash(td)
        self.assertEqual(d1, d2)

    def test_changes_when_bytes_change(self):
        """A single-byte difference in the calendar must produce a
        different hash — otherwise the integrity check is useless."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            _write_calendar(Path(td), dates=["2026-01-02"])
            d_short = compute_bundle_content_hash(td)
        with tempfile.TemporaryDirectory() as td:
            _write_calendar(Path(td), dates=["2026-01-02", "2026-01-03"])
            d_long = compute_bundle_content_hash(td)
        self.assertNotEqual(d_short, d_long)

    def test_raises_when_calendar_missing(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            # No calendars/day.txt written.
            with self.assertRaisesRegex(
                BundleManifestError, "calendars/day.txt|calendar"
            ):
                compute_bundle_content_hash(td)

    def test_wraps_oserror_from_unreadable_calendar(self):
        """The ``is_file()`` guard catches the missing-file case, but
        a calendar that exists yet is unreadable — permission denied,
        EIO, a TOCTOU race that deletes it between the check and
        the read — would otherwise raise the raw ``OSError`` up to
        the caller. The docstring contract is "missing or unreadable
        => BundleManifestError" so the ingest script's
        ``except BundleManifestError`` actually catches both failure
        modes. Codex P2 on PR #175.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            _write_calendar(Path(td))
            # Force the open() call to raise PermissionError after the
            # is_file() check has already passed. Patching pathlib.Path.open
            # is the cleanest way to simulate the race / permission case
            # deterministically across OSes (a real chmod 000 file would
            # behave differently on Windows vs Linux).
            with patch.object(
                Path, "open",
                side_effect=PermissionError("simulated"),
            ):
                with self.assertRaises(BundleManifestError) as ctx:
                    compute_bundle_content_hash(td)
        msg = str(ctx.exception)
        self.assertIn("PermissionError", msg)
        # The original OSError must be preserved as __cause__ so callers
        # debugging downstream can still see the root cause.
        self.assertIsInstance(ctx.exception.__cause__, OSError)


# ---------------------------------------------------------------------------
# save_manifest content_hash round-trip
# ---------------------------------------------------------------------------


class SaveManifestContentHashTests(unittest.TestCase):

    def test_omits_content_hash_when_none(self):
        """Legacy callers that don't pass ``content_hash`` MUST produce
        a JSON without the key (not ``"content_hash": null``) so the
        emitted manifest stays byte-identical to pre-PR fixtures."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            save_manifest(td, tail_date="2026-03-06", instrument_count=10)
            payload = json.loads(
                (Path(td) / MANIFEST_FILENAME).read_text(encoding="utf-8"),
            )
        self.assertNotIn("content_hash", payload)

    def test_writes_content_hash_when_supplied(self):
        import tempfile

        good = CONTENT_HASH_PREFIX + ("a" * 64)
        with tempfile.TemporaryDirectory() as td:
            save_manifest(
                td,
                tail_date="2026-03-06",
                instrument_count=10,
                content_hash=good,
            )
            payload = json.loads(
                (Path(td) / MANIFEST_FILENAME).read_text(encoding="utf-8"),
            )
        self.assertEqual(payload["content_hash"], good)

    def test_round_trips_via_load_manifest(self):
        import tempfile

        good = CONTENT_HASH_PREFIX + ("b" * 64)
        with tempfile.TemporaryDirectory() as td:
            save_manifest(
                td, tail_date="2026-03-06", instrument_count=1,
                content_hash=good,
            )
            loaded = load_manifest(td)
            assert loaded is not None
            self.assertEqual(loaded.content_hash, good)

    def test_load_returns_none_for_legacy_manifest_without_field(self):
        """A manifest that pre-dates this PR (no ``content_hash`` key)
        MUST load fine with ``content_hash=None``; that's the legacy
        contract operators implicitly rely on."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            (Path(td) / MANIFEST_FILENAME).write_text(
                json.dumps({
                    "provider_uri": "D:/legacy",
                    "tail_date": "2026-03-06",
                    "instrument_count": 5,
                    "built_at": "2026-03-08T00:00:00Z",
                }),
                encoding="utf-8",
            )
            loaded = load_manifest(td)
        assert loaded is not None
        self.assertIsNone(loaded.content_hash)

    def test_rejects_non_string_content_hash(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(BundleManifestError, "content_hash"):
                save_manifest(
                    td, tail_date="2026-03-06", instrument_count=1,
                    content_hash=12345,  # type: ignore[arg-type]
                )

    def test_rejects_content_hash_without_algo_prefix(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(BundleManifestError, "content_hash"):
                save_manifest(
                    td, tail_date="2026-03-06", instrument_count=1,
                    content_hash="a" * 64,
                )

    def test_rejects_content_hash_wrong_hex_length(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(BundleManifestError, "content_hash"):
                save_manifest(
                    td, tail_date="2026-03-06", instrument_count=1,
                    content_hash=CONTENT_HASH_PREFIX + "abcdef",  # too short
                )

    def test_rejects_content_hash_non_hex_chars(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(BundleManifestError, "content_hash"):
                save_manifest(
                    td, tail_date="2026-03-06", instrument_count=1,
                    # 64 chars but with a non-hex 'z'.
                    content_hash=CONTENT_HASH_PREFIX + ("a" * 63) + "z",
                )

    def test_save_rejects_uppercase_hex_content_hash(self):
        """``compute_bundle_content_hash`` emits lowercase hex, so
        an uppercase manifest would shape-validate but then byte-
        mismatch at verify time. Reject at write to fail honestly.
        Codex P2 on PR #175.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(BundleManifestError, "content_hash"):
                save_manifest(
                    td, tail_date="2026-03-06", instrument_count=1,
                    # 64 valid hex chars, but the letters are uppercase.
                    content_hash=CONTENT_HASH_PREFIX + ("AB" * 32),
                )

    def test_load_rejects_uppercase_hex_content_hash(self):
        """Symmetric to save_manifest's rejection — a hand-crafted
        manifest with uppercase hex on disk must surface at load time,
        not later as a confusing mismatch. Codex P2 on PR #175.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            (Path(td) / MANIFEST_FILENAME).write_text(
                json.dumps({
                    "provider_uri": "D:/x",
                    "tail_date": "2026-03-06",
                    "instrument_count": 5,
                    "built_at": "2026-03-08T00:00:00Z",
                    "content_hash": CONTENT_HASH_PREFIX + ("AB" * 32),
                }),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(BundleManifestError, "content_hash"):
                load_manifest(td)

    def test_load_rejects_explicit_null_content_hash(self):
        """``"content_hash": null`` on disk must REJECT rather than
        silently disable the integrity check. A producer that emits
        explicit null almost certainly meant to set a hash but failed;
        treating it as "legacy / no hash" turns a corruption signal
        into a silent opt-out. Codex P2 on PR #175.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            (Path(td) / MANIFEST_FILENAME).write_text(
                json.dumps({
                    "provider_uri": "D:/x",
                    "tail_date": "2026-03-06",
                    "instrument_count": 5,
                    "built_at": "2026-03-08T00:00:00Z",
                    "content_hash": None,
                }),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                BundleManifestError, "content_hash.*null|null.*content_hash"
            ):
                load_manifest(td)

    def test_load_rejects_malformed_content_hash_on_disk(self):
        """A hand-edited / corrupted manifest with a malformed
        content_hash on disk must surface as BundleManifestError at
        load time, not later as a confusing mismatch."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            (Path(td) / MANIFEST_FILENAME).write_text(
                json.dumps({
                    "provider_uri": "D:/x",
                    "tail_date": "2026-03-06",
                    "instrument_count": 5,
                    "built_at": "2026-03-08T00:00:00Z",
                    "content_hash": "md5:abc",  # wrong algo prefix
                }),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(BundleManifestError, "content_hash"):
                load_manifest(td)


# ---------------------------------------------------------------------------
# verify_content_hash
# ---------------------------------------------------------------------------


class VerifyContentHashTests(unittest.TestCase):
    """``verify_content_hash`` is the runtime side of the integrity
    check — it loads the manifest, recomputes the calendar hash, and
    compares. The matrix of cases below covers:

    - matching hash → silent
    - mismatch (out-of-band edit) → BundleContentHashMismatchError
    - mismatch with soft=True → WARNING
    - manifest with no content_hash (legacy) → silent
    - missing manifest entirely → silent
    - manifest claims hash but calendar file is missing → BundleManifestError
    - SKIP env var → silent (matches validate_test_end behaviour)
    """

    def setUp(self) -> None:
        import os
        self._saved_skip = os.environ.pop(SKIP_ENV_VAR, None)

    def tearDown(self) -> None:
        import os
        os.environ.pop(SKIP_ENV_VAR, None)
        if self._saved_skip is not None:
            os.environ[SKIP_ENV_VAR] = self._saved_skip

    def test_silent_when_calendar_matches_manifest_hash(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            _write_calendar(Path(td))
            actual = compute_bundle_content_hash(td)
            save_manifest(
                td, tail_date="2026-03-06", instrument_count=1,
                content_hash=actual,
            )
            # Must NOT raise.
            verify_content_hash(td)

    def test_raises_on_mismatch(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            _write_calendar(Path(td))
            # Stamp a manifest with a hash that doesn't match the
            # calendar — simulates a calendar edited after manifest
            # write.
            stale = CONTENT_HASH_PREFIX + ("0" * 64)
            save_manifest(
                td, tail_date="2026-03-06", instrument_count=1,
                content_hash=stale,
            )
            with self.assertRaises(BundleContentHashMismatchError) as ctx:
                verify_content_hash(td)
        msg = str(ctx.exception)
        self.assertIn(stale, msg)
        self.assertIn(SKIP_ENV_VAR, msg)  # remediation hint

    def test_mismatch_soft_logs_warning(self):
        import logging
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            _write_calendar(Path(td))
            stale = CONTENT_HASH_PREFIX + ("0" * 64)
            save_manifest(
                td, tail_date="2026-03-06", instrument_count=1,
                content_hash=stale,
            )
            with self.assertLogs(
                "src.data.bundle_manifest", level="WARNING"
            ) as captured:
                verify_content_hash(td, soft=True)
        warns = [r for r in captured.records if r.levelno == logging.WARNING]
        self.assertEqual(len(warns), 1)
        self.assertIn("content_hash mismatch", warns[0].getMessage())

    def test_silent_when_manifest_has_no_content_hash(self):
        """Legacy bundle: manifest exists, but no content_hash field.
        Must be a no-op so existing operators are not forced to
        re-ingest."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            _write_calendar(Path(td))
            save_manifest(td, tail_date="2026-03-06", instrument_count=1)
            # No raise.
            verify_content_hash(td)

    def test_silent_when_manifest_missing(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            _write_calendar(Path(td))
            # No manifest written at all.
            verify_content_hash(td)

    def test_raises_when_manifest_claims_hash_but_calendar_missing(self):
        """Manifest declares an integrity surface (``content_hash``)
        that no longer exists on disk. Surface this as
        BundleManifestError (consistent with the "manifest schema is
        wrong" error class) rather than as a silent pass."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            # Hand-craft a manifest with content_hash but DO NOT
            # write calendars/day.txt. save_manifest would normally
            # not gate on the calendar existing — operators usually
            # write the manifest after the publisher emits the
            # calendar, but we test the standalone validator here.
            fake = CONTENT_HASH_PREFIX + ("a" * 64)
            save_manifest(
                td, tail_date="2026-03-06", instrument_count=1,
                content_hash=fake,
            )
            # calendars/day.txt does not exist.
            with self.assertRaisesRegex(
                BundleManifestError, "calendar"
            ):
                verify_content_hash(td)

    def test_skip_env_var_bypasses(self):
        import os
        import tempfile

        os.environ[SKIP_ENV_VAR] = "1"
        with tempfile.TemporaryDirectory() as td:
            _write_calendar(Path(td))
            stale = CONTENT_HASH_PREFIX + ("0" * 64)
            save_manifest(
                td, tail_date="2026-03-06", instrument_count=1,
                content_hash=stale,
            )
            # Mismatch, but env-var bypass → no raise.
            verify_content_hash(td)


# ---------------------------------------------------------------------------
# validate_test_end_against_bundle: hash check fires before date check
# ---------------------------------------------------------------------------


class ValidateBundleHashCheckOrderingTests(unittest.TestCase):
    """When both content_hash and tail_date are violated,
    BundleContentHashMismatchError must fire FIRST — see the docstring
    rationale in validate_test_end_against_bundle."""

    def setUp(self) -> None:
        import os
        self._saved_skip = os.environ.pop(SKIP_ENV_VAR, None)

    def tearDown(self) -> None:
        import os
        os.environ.pop(SKIP_ENV_VAR, None)
        if self._saved_skip is not None:
            os.environ[SKIP_ENV_VAR] = self._saved_skip

    def test_hash_mismatch_fires_before_stale_date(self):
        import tempfile

        from src.data.bundle_manifest import validate_test_end_against_bundle

        with tempfile.TemporaryDirectory() as td:
            _write_calendar(Path(td))
            # tail_date is 2026-01-01 — way past for the test_end below
            # — AND content_hash is wrong. We expect the hash error,
            # NOT the stale-date error.
            stale = CONTENT_HASH_PREFIX + ("0" * 64)
            save_manifest(
                td, tail_date="2026-01-01", instrument_count=1,
                content_hash=stale,
            )
            with self.assertRaises(BundleContentHashMismatchError):
                validate_test_end_against_bundle(td, "2099-12-31")

    def test_malformed_test_end_beats_hash_error(self):
        """When ``test_end`` is malformed AND the bundle has a hash
        mismatch (or missing calendar), the test_end-parse error MUST
        fire first. The malformed-date case is a caller-config bug
        (their YAML is wrong); they should see that actionable error,
        not a downstream environmental hash error that masks the
        real problem. Codex P2 on PR #175.
        """
        import tempfile

        from src.data.bundle_manifest import validate_test_end_against_bundle

        with tempfile.TemporaryDirectory() as td:
            _write_calendar(Path(td))
            # Manifest claims a bogus content_hash, so the hash check
            # WILL fail — but we expect the malformed-date error
            # FIRST, not the hash mismatch error.
            stale_hash = CONTENT_HASH_PREFIX + ("0" * 64)
            save_manifest(
                td, tail_date="2026-03-06", instrument_count=1,
                content_hash=stale_hash,
            )
            with self.assertRaisesRegex(
                BundleManifestError, "test_end"
            ) as ctx:
                validate_test_end_against_bundle(td, "not-a-date")
            # Sanity: the raised error is the config one, not the hash
            # mismatch one.
            self.assertNotIsInstance(
                ctx.exception, BundleContentHashMismatchError,
            )

    def test_hash_valid_then_stale_date_still_raises(self):
        """Sanity: when the hash matches, the date check still runs
        and a past test_end still triggers BundleStaleError."""
        import tempfile

        from src.data.bundle_manifest import (
            BundleStaleError,
            validate_test_end_against_bundle,
        )

        with tempfile.TemporaryDirectory() as td:
            _write_calendar(Path(td))
            good = compute_bundle_content_hash(td)
            save_manifest(
                td, tail_date="2026-01-01", instrument_count=1,
                content_hash=good,
            )
            with self.assertRaises(BundleStaleError):
                validate_test_end_against_bundle(td, "2099-12-31")
