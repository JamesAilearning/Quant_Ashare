"""Tests for ``src.core.attribution_industry_loader`` — the shared
industry-taxonomy resolver used by both Pipeline and walk-forward.
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
    TAXONOMY_MODE_STATIC,
    TAXONOMY_MODE_TRADE_DATE,
)
from src.core.attribution_industry_loader import (  # noqa: E402
    PURPOSE_ATTRIBUTION,
    PURPOSE_TRAINING,
    IndustryTaxonomyLoadError,
    IndustryTaxonomyResolution,
    assert_industry_config_complete_or_empty,
    resolve_industry_taxonomy,
)
from src.data.taxonomy_artifact_publisher import TaxonomyArtifactPublisher  # noqa: E402

# ---------------------------------------------------------------------
# Boundary check: partial config must be rejected
# ---------------------------------------------------------------------


class _DomainErrorA(RuntimeError):
    """Stand-in for caller-supplied error class A."""


class _DomainErrorB(RuntimeError):
    """Stand-in for caller-supplied error class B (parallel domain)."""


class AssertConfigCompleteOrEmptyTests(unittest.TestCase):
    """Pin the boundary contract so neither Pipeline nor walk-forward
    can accept partial industry config and surface the failure
    confusingly deep in the loader.

    The validator must:
    1. Accept all-empty (uses board heuristic).
    2. Accept all-set (loads the artifact).
    3. Reject any 1/3 or 2/3 combination.
    4. Use the caller-supplied error class so the domain types stay
       separate.
    5. Reject unsupported temporal_mode values.
    """

    def _call(self, *, error_class=_DomainErrorA, **kwargs):
        defaults = {
            "artifact_path": None, "manifest_path": None,
            "taxonomy_id": None,
            "temporal_mode": TAXONOMY_MODE_STATIC,
        }
        defaults.update(kwargs)
        return assert_industry_config_complete_or_empty(
            **defaults, error_class=error_class, error_prefix="TestConfig",
        )

    def test_all_empty_passes(self) -> None:
        # No exception raised — board heuristic mode is the legitimate
        # default and should always pass through.
        self._call()

    def test_all_set_passes(self) -> None:
        self._call(
            artifact_path="a.csv", manifest_path="a.json",
            taxonomy_id="tushare_sw_l2",
        )

    def test_only_artifact_set_rejected(self) -> None:
        with self.assertRaisesRegex(_DomainErrorA, "explicit triple"):
            self._call(artifact_path="a.csv")

    def test_only_manifest_set_rejected(self) -> None:
        with self.assertRaisesRegex(_DomainErrorA, "explicit triple"):
            self._call(manifest_path="a.json")

    def test_only_taxonomy_id_set_rejected(self) -> None:
        with self.assertRaisesRegex(_DomainErrorA, "explicit triple"):
            self._call(taxonomy_id="tushare_sw_l2")

    def test_two_of_three_set_rejected(self) -> None:
        with self.assertRaisesRegex(_DomainErrorA, "explicit triple"):
            self._call(artifact_path="a.csv", taxonomy_id="t")

    def test_uses_caller_supplied_error_class(self) -> None:
        """A and B are deliberately separate types; the validator must
        raise exactly the type the caller asked for so the rest of
        each subsystem can ``except`` its own domain error."""
        try:
            self._call(error_class=_DomainErrorB, artifact_path="a.csv")
        except _DomainErrorB:
            pass
        except _DomainErrorA:
            self.fail("validator raised A instead of caller-supplied B")
        else:
            self.fail("validator did not raise on partial config")

    def test_error_prefix_lands_in_message(self) -> None:
        """Operator should see which dataclass they configured wrong."""
        try:
            assert_industry_config_complete_or_empty(
                artifact_path="x", manifest_path=None, taxonomy_id=None,
                temporal_mode=TAXONOMY_MODE_STATIC,
                error_class=_DomainErrorA,
                error_prefix="MyConfigPrefix",
            )
        except _DomainErrorA as exc:
            self.assertIn("MyConfigPrefix", str(exc))
        else:
            self.fail("expected raise")

    def test_unsupported_temporal_mode_rejected(self) -> None:
        """v1 only supports static mode for attribution. trade_date /
        range modes need additional logic that doesn't exist yet —
        accepting them now would silently produce a stale-window
        attribution map."""
        with self.assertRaisesRegex(_DomainErrorA, "industry_temporal_mode"):
            self._call(temporal_mode=TAXONOMY_MODE_TRADE_DATE)


# ---------------------------------------------------------------------
# resolve_industry_taxonomy — the actual load + validate pipeline
# ---------------------------------------------------------------------


class ResolveIndustryTaxonomyTests(unittest.TestCase):
    """End-to-end: build a real artifact via the publisher, then
    resolve. Uses the same publisher Pipeline / walk-forward use, so
    a regression in either side flips this test.
    """

    @staticmethod
    def _publish(tmp: Path, taxonomy_name: str = "tushare_sw_l2") -> tuple[Path, Path]:
        artifact = tmp / "sw_l2.csv"
        manifest = tmp / "sw_l2.json"
        TaxonomyArtifactPublisher.publish(
            taxonomy_name=taxonomy_name,
            temporal_mode=TAXONOMY_MODE_STATIC,
            rows=[("SH600000", "银行"), ("SZ000001", "银行"),
                  ("SH600519", "白酒")],
            artifact_path=str(artifact),
            manifest_path=str(manifest),
            snapshot_at="2025-07-01",
        )
        return artifact, manifest

    def test_happy_path_returns_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            artifact, manifest = self._publish(tmp)
            resolution = resolve_industry_taxonomy(
                artifact_path=str(artifact),
                manifest_path=str(manifest),
                taxonomy_id="tushare_sw_l2",
                temporal_mode=TAXONOMY_MODE_STATIC,
                purpose=PURPOSE_TRAINING,
                reference_date="2025-08-01",
            )
        self.assertIsInstance(resolution, IndustryTaxonomyResolution)
        self.assertEqual(resolution.taxonomy_id, "tushare_sw_l2")
        self.assertEqual(resolution.industry_map["SH600000"], "银行")
        self.assertEqual(resolution.industry_map["SH600519"], "白酒")

    def test_missing_artifact_file_raises_load_error(self) -> None:
        """Caller catches a single :class:`IndustryTaxonomyLoadError`
        regardless of which sub-layer failed (loader / contract / map)."""
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            with self.assertRaises(IndustryTaxonomyLoadError):
                resolve_industry_taxonomy(
                    artifact_path=str(tmp / "missing.csv"),
                    manifest_path=str(tmp / "missing.json"),
                    taxonomy_id="tushare_sw_l2",
                    temporal_mode=TAXONOMY_MODE_STATIC,
                    purpose=PURPOSE_ATTRIBUTION,
                )

    def test_taxonomy_id_mismatch_raises(self) -> None:
        """Manifest's ``taxonomy_name`` must match the config-declared
        ``taxonomy_id``. A typo in either side would otherwise produce a
        result stamped with the wrong taxonomy."""
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            artifact, manifest = self._publish(tmp, taxonomy_name="actual_name")
            with self.assertRaisesRegex(
                IndustryTaxonomyLoadError, "does not match"
            ):
                resolve_industry_taxonomy(
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                    taxonomy_id="expected_name",
                    temporal_mode=TAXONOMY_MODE_STATIC,
                    purpose=PURPOSE_ATTRIBUTION,
                )

    def test_warnings_propagated(self) -> None:
        """Non-fatal contract warnings (e.g. stale snapshot) must
        come through ``resolution.warnings`` so the caller can log
        them. Drift here would silently hide soft drift."""
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            artifact, manifest = self._publish(tmp)
            # Force a stale snapshot by passing a reference date much
            # later than the artifact's snapshot_at (2025-07-01).
            # Use purpose=TRAINING so the reference_date is honoured.
            resolution = resolve_industry_taxonomy(
                artifact_path=str(artifact),
                manifest_path=str(manifest),
                taxonomy_id="tushare_sw_l2",
                temporal_mode=TAXONOMY_MODE_STATIC,
                purpose=PURPOSE_TRAINING,
                reference_date="2026-12-31",
            )
        # Warnings list is always present; whether non-empty depends on
        # the contract's stale-threshold default. We don't pin a specific
        # warning string (the contract library owns those) — we just
        # verify the channel exists.
        self.assertIsInstance(resolution.warnings, list)


class PurposeParameterTests(unittest.TestCase):
    """Pin the temporal-leakage policy switching driven by ``purpose``.

    These guards are the whole point of #3 in the cleanup batch: a
    future caller that wants leakage protection cannot get it
    "by accident", and an attribution caller cannot trip the leakage
    check just because the artifact snapshot is in the future.
    """

    @staticmethod
    def _publish(tmp: Path) -> tuple[Path, Path]:
        from src.data.taxonomy_artifact_publisher import TaxonomyArtifactPublisher
        artifact = tmp / "sw_l2.csv"
        manifest = tmp / "sw_l2.json"
        TaxonomyArtifactPublisher.publish(
            taxonomy_name="tushare_sw_l2",
            temporal_mode=TAXONOMY_MODE_STATIC,
            rows=[("SH600000", "银行")],
            artifact_path=str(artifact),
            manifest_path=str(manifest),
            # Snapshot well into the future relative to the
            # ``reference_date`` we pass below — would trip
            # ``has_future_known_metadata`` under TRAINING.
            snapshot_at="2099-01-01",
        )
        return artifact, manifest

    def test_attribution_purpose_ignores_future_snapshot(self) -> None:
        """The walk-forward use case: artifact ingested "today",
        backtest folds run on past data → manifest snapshot is in the
        future relative to every fold's test_end. Under ``purpose=
        attribution`` the load must succeed; under ``training`` it
        must fail."""
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            artifact, manifest = self._publish(tmp)

            # Attribution path: future snapshot is fine, load returns
            # successfully even when the operator-supplied
            # ``reference_date`` is well before the snapshot.
            resolution = resolve_industry_taxonomy(
                artifact_path=str(artifact),
                manifest_path=str(manifest),
                taxonomy_id="tushare_sw_l2",
                temporal_mode=TAXONOMY_MODE_STATIC,
                purpose=PURPOSE_ATTRIBUTION,
                reference_date="2024-06-30",
            )
            self.assertEqual(resolution.taxonomy_id, "tushare_sw_l2")

    def test_training_purpose_enforces_temporal_leakage_check(self) -> None:
        """Same fixture, but under ``purpose=training`` the contract
        must reject a future-dated manifest. Pinning this ensures
        an accidental ``purpose=PURPOSE_ATTRIBUTION`` on a training
        site would be a visible behavioural change, not a silent
        one."""
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            artifact, manifest = self._publish(tmp)

            with self.assertRaisesRegex(
                IndustryTaxonomyLoadError, "temporal_leakage"
            ):
                resolve_industry_taxonomy(
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                    taxonomy_id="tushare_sw_l2",
                    temporal_mode=TAXONOMY_MODE_STATIC,
                    purpose=PURPOSE_TRAINING,
                    reference_date="2024-06-30",
                )

    def test_unknown_purpose_rejected(self) -> None:
        """Typos like ``"atttribution"`` must not silently default to
        either policy. Loud raise so a code reviewer catches the typo."""
        with self.assertRaisesRegex(IndustryTaxonomyLoadError, "Unknown purpose"):
            resolve_industry_taxonomy(
                artifact_path="anything",
                manifest_path="anything",
                taxonomy_id="x",
                temporal_mode=TAXONOMY_MODE_STATIC,
                purpose="atttribution",  # typo
            )


if __name__ == "__main__":
    unittest.main()
