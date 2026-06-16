"""PR-G+I: single bundle identity folded into _fetch_integrity.json.

Covers the writer/reader roundtrip + back-compat, and the four re-pointed
consumers (feature-cache tag, WF freshness check, resume fingerprint, and — by
the lockstep pin — that the cache key and resume fingerprint invalidate together
on a same-window re-ingest).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.core.walk_forward import WalkForwardConfig
from src.core.walk_forward._resume import compute_config_fingerprint
from src.data._feature_dataset_cache import _LEGACY_BUNDLE_TAG, read_bundle_tag
from src.data.bundle_manifest import (
    BundleStaleError,
    _resolve_bundle_freshness,
    compute_bundle_content_hash,
    validate_test_end_against_bundle,
)
from src.data.pit.bundle_integrity import (
    BundleIdentity,
    BundleIntegrityError,
    read_bundle_integrity,
    write_bundle_integrity,
)


def _make_config(**overrides) -> WalkForwardConfig:
    base = dict(
        instruments="csi300", feature_handler="Alpha158",
        overall_start="2022-01-01", overall_end="2025-12-31",
        train_months=24, valid_months=3, test_months=3, step_months=3,
        topk=50, output_dir="output/wf",
    )
    base.update(overrides)
    return WalkForwardConfig(**base)


def _write_bundle(bundle_dir: Path, dates: list[str], *, with_identity: bool = True) -> str:
    """Write a minimal bundle (calendars/day.txt) + its _fetch_integrity stamp.
    Returns the content_hash used in the identity (computed from the real bytes)."""
    cal = bundle_dir / "calendars"
    cal.mkdir(parents=True, exist_ok=True)
    (cal / "day.txt").write_text("\n".join(dates) + "\n", encoding="utf-8")
    content_hash = compute_bundle_content_hash(bundle_dir)
    identity = None
    if with_identity:
        identity = BundleIdentity(
            tail_date=dates[-1], content_hash=content_hash, instrument_count=len(dates),
            calendar_start=dates[0], calendar_end=dates[-1],
        )
    write_bundle_integrity(bundle_dir, built_from_holey_fetch=False, identity=identity)
    return content_hash


class WriterReaderRoundtripTests(unittest.TestCase):
    def test_roundtrip_with_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            ch = _write_bundle(d, ["2018-01-02", "2025-12-31"])
            integ = read_bundle_integrity(d)
            assert integ is not None and integ.identity is not None
            self.assertEqual(integ.identity.tail_date, "2025-12-31")
            self.assertEqual(integ.identity.content_hash, ch)
            self.assertEqual(integ.identity.instrument_count, 2)
            self.assertEqual(integ.identity.tag, f"2025-12-31@{ch}")
            # Gate back-compat: a clean stamp that ALSO carries identity still
            # reads built_from_holey_fetch=False (the daily_recommend gate input
            # is unaffected by the optional identity key).
            self.assertFalse(integ.built_from_holey_fetch)

    def test_legacy_v1_stamp_without_identity_reads_none(self) -> None:
        # A pre-PR-G+I stamp (no identity key) must NOT fail loud — schema stays
        # v1, identity is optional, so the daily_recommend gate keeps working.
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            write_bundle_integrity(d, built_from_holey_fetch=False)  # no identity
            integ = read_bundle_integrity(d)
            assert integ is not None
            self.assertIsNone(integ.identity)
            # and the on-disk stamp has no "identity" key at all (byte-stable)
            raw = json.loads((d / "_fetch_integrity.json").read_text(encoding="utf-8"))
            self.assertNotIn("identity", raw)

    def test_malformed_identity_fails_loud(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            write_bundle_integrity(d, built_from_holey_fetch=False)
            p = d / "_fetch_integrity.json"
            raw = json.loads(p.read_text(encoding="utf-8"))
            raw["identity"] = {"tail_date": "2025-12-31"}  # missing required fields
            p.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(BundleIntegrityError):
                read_bundle_integrity(d)


class ReadBundleTagTests(unittest.TestCase):
    def test_prefers_integrity_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            ch = _write_bundle(d, ["2018-01-02", "2025-12-31"])
            self.assertEqual(read_bundle_tag(str(d)), f"2025-12-31@{ch}")

    def test_no_identity_falls_through_to_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_bundle(d, ["2018-01-02", "2025-12-31"], with_identity=False)
            self.assertEqual(read_bundle_tag(str(d)), _LEGACY_BUNDLE_TAG)


class ResumeFingerprintTests(unittest.TestCase):
    def test_real_identity_changes_fingerprint(self) -> None:
        cfg = _make_config()
        base = compute_config_fingerprint(cfg)
        with_id = compute_config_fingerprint(cfg, bundle_identity="2025-12-31@sha256:abc")
        self.assertNotEqual(base, with_id)

    def test_unknown_or_none_leaves_fingerprint_unchanged(self) -> None:
        # Adoption is free on identity-less bundles: "unknown"/None is NOT folded
        # in, so the digest equals the pre-PR-G+I fingerprint.
        cfg = _make_config()
        base = compute_config_fingerprint(cfg)
        self.assertEqual(base, compute_config_fingerprint(cfg, bundle_identity=None))
        self.assertEqual(base, compute_config_fingerprint(cfg, bundle_identity="unknown"))

    def test_resume_guard_sentinel_matches_read_bundle_tag_constant(self) -> None:
        # The resume guard hardcodes the literal "unknown" (to keep _resume.py
        # free of data-layer imports); pin it to read_bundle_tag's actual
        # _LEGACY_BUNDLE_TAG so a rename of the sentinel can't silently start
        # folding it into the fingerprint and force spurious full re-runs.
        cfg = _make_config()
        self.assertEqual(
            compute_config_fingerprint(cfg),
            compute_config_fingerprint(cfg, bundle_identity=_LEGACY_BUNDLE_TAG),
        )


class ResolveBundleFreshnessTests(unittest.TestCase):
    """The _resolve_bundle_freshness precedence contract: prefer _fetch_integrity
    identity, fall back to bundle_manifest.json, degrade a malformed stamp."""

    @staticmethod
    def _write_manifest(bundle_dir: Path, tail_date: str, content_hash: str | None) -> None:
        payload = {
            "provider_uri": str(bundle_dir), "tail_date": tail_date,
            "instrument_count": 1, "built_at": "2026-01-01T00:00:00+00:00",
        }
        if content_hash is not None:
            payload["content_hash"] = content_hash
        (bundle_dir / "bundle_manifest.json").write_text(
            json.dumps(payload), encoding="utf-8")

    def test_integrity_identity_wins_over_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            ch = _write_bundle(d, ["2018-01-02", "2025-12-31"])  # identity tail 2025-12-31
            self._write_manifest(d, "2020-06-30", None)  # older manifest (not reached)
            fresh = _resolve_bundle_freshness(str(d))
            assert fresh is not None
            self.assertEqual(fresh.tail_date.isoformat(), "2025-12-31")  # identity won
            self.assertEqual(fresh.content_hash, ch)

    def test_manifest_only_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            valid_hash = "sha256:" + "a" * 64  # load_manifest enforces 64-hex
            self._write_manifest(d, "2024-03-31", valid_hash)  # no integrity stamp
            fresh = _resolve_bundle_freshness(str(d))
            assert fresh is not None
            self.assertEqual(fresh.tail_date.isoformat(), "2024-03-31")
            self.assertEqual(fresh.content_hash, valid_hash)

    def test_malformed_identity_degrades_to_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # integrity stamp present but identity tail_date is non-ISO garbage
            write_bundle_integrity(d, built_from_holey_fetch=False)
            p = d / "_fetch_integrity.json"
            raw = json.loads(p.read_text(encoding="utf-8"))
            raw["identity"] = {
                "tail_date": "not-a-date", "content_hash": "x",
                "instrument_count": 1, "calendar_start": "x", "calendar_end": "x",
            }
            p.write_text(json.dumps(raw), encoding="utf-8")
            self._write_manifest(d, "2024-03-31", None)
            fresh = _resolve_bundle_freshness(str(d))  # must NOT raise
            assert fresh is not None
            self.assertEqual(fresh.tail_date.isoformat(), "2024-03-31")  # fell back

    def test_no_source_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(_resolve_bundle_freshness(tmp))


class WfFreshnessLightsUpTests(unittest.TestCase):
    def test_freshness_check_fires_from_integrity_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_bundle(d, ["2018-01-02", "2025-12-31"])
            # test_end inside the bundle → passes silently
            validate_test_end_against_bundle(str(d), "2025-12-31")
            # test_end PAST tail_date → the check (previously a no-op) now fires
            with self.assertRaises(BundleStaleError):
                validate_test_end_against_bundle(str(d), "2026-06-30")


class LockstepInvalidationTests(unittest.TestCase):
    """Governance pin: a same-WINDOW re-ingest with different calendar bytes
    (e.g. a holiday correction — same start/tail, different interior days)
    invalidates BOTH the feature-cache tag AND the resume fingerprint."""

    def test_cache_tag_and_resume_invalidate_together(self) -> None:
        cfg = _make_config()
        with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
            d1, d2 = Path(t1), Path(t2)
            # same window (start 2018-01-02, tail 2025-12-31) but different
            # interior calendar bytes → different content_hash.
            _write_bundle(d1, ["2018-01-02", "2025-12-30", "2025-12-31"])
            _write_bundle(d2, ["2018-01-02", "2025-12-29", "2025-12-31"])
            tag1, tag2 = read_bundle_tag(str(d1)), read_bundle_tag(str(d2))
            self.assertNotEqual(tag1, tag2)  # cache key differs
            self.assertNotEqual(  # resume fingerprint differs in lockstep
                compute_config_fingerprint(cfg, bundle_identity=tag1),
                compute_config_fingerprint(cfg, bundle_identity=tag2),
            )


if __name__ == "__main__":
    unittest.main()
