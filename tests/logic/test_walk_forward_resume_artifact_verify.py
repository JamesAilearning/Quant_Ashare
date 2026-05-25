"""Tests for audit P2 — walk-forward resume must verify that the
artifacts referenced by a manifest actually exist before treating
the fold as resumable.

Before this fix, ``FoldManifest.discover`` loaded the manifest JSON
and ran a version check, but did not verify that ``model_path`` /
``report_path`` / ``predictions_path`` existed on disk. An operator
who deleted or moved per-fold artifacts (cleanup script, accidental
``rm``, archive operation) would see the engine "resume" a fold
whose model pickle no longer exists — the ensemble loader would
then crash later, or worse, silently fall back to fewer models.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.walk_forward._resume import (  # noqa: E402
    FoldManifest,
    _missing_required_artifacts,
)
from src.core.walk_forward._types import WalkForwardFold  # noqa: E402
from src.core.walk_forward.config import WalkForwardConfig  # noqa: E402


def _make_fold(idx: int = 3) -> WalkForwardFold:
    return WalkForwardFold(
        fold_index=idx,
        train_period="2022-01-01 ~ 2023-12-31",
        valid_period="2024-01-01 ~ 2024-03-31",
        test_period="2024-04-01 ~ 2024-06-30",
        ic_1d=0.045, ic_5d=0.038,
        annualized_return=0.12, max_drawdown=-0.08,
        information_ratio=0.85,
        prediction_shape=(123, 250),
    )


def _populate_artifacts(
    output_dir: Path, fold_idx: int, *,
    model=True, report=True, predictions=True,
) -> FoldManifest:
    """Create a manifest under ``output_dir`` whose model_path /
    report_path / predictions_path point at real files (subject to the
    flag toggles). Returns a **rebased** manifest (absolute paths)
    suitable for passing to :func:`_missing_required_artifacts`
    directly — the on-disk manifest still carries basenames per
    Codex P1 on PR #147, but the unit tests for the artifact-verify
    helper want the rebased shape the engine consumes."""
    output_dir.mkdir(parents=True, exist_ok=True)
    cfg = WalkForwardConfig()
    fold = _make_fold(idx=fold_idx)
    model_path = output_dir / f"model_fold{fold_idx}.pkl"
    report_path = output_dir / f"fold_{fold_idx:02d}_report.json"
    predictions_path = output_dir / f"fold_{fold_idx:02d}_predictions.pkl"

    if model:
        model_path.write_bytes(b"fake pickle")
    if report:
        report_path.write_text('{"fold": 0}')
    if predictions:
        predictions_path.write_bytes(b"fake pickle")

    m = FoldManifest.from_fold(
        fold=fold,
        config=cfg,
        model_path=str(model_path),
        report_path=str(report_path),
        predictions_path=str(predictions_path),
        positions_path=None,
    )
    m.save(output_dir)
    # discover() rebases stored basenames against the output dir;
    # mirror that here so callers get absolute paths.
    return m.with_paths_rebased(output_dir)


# ---------------------------------------------------------------------------
# _missing_required_artifacts — pure helper
# ---------------------------------------------------------------------------


class MissingRequiredArtifactsTests(unittest.TestCase):
    def test_all_present_returns_empty(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            m = _populate_artifacts(Path(td), fold_idx=0)
            self.assertEqual(_missing_required_artifacts(m), [])

    def test_missing_model_flagged(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            m = _populate_artifacts(Path(td), fold_idx=0)
            Path(m.model_path).unlink()
            self.assertEqual(
                _missing_required_artifacts(m), ["model_path"],
            )

    def test_missing_report_and_predictions_both_flagged(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            m = _populate_artifacts(Path(td), fold_idx=0)
            Path(m.report_path).unlink()
            Path(m.predictions_path).unlink()
            self.assertEqual(
                _missing_required_artifacts(m),
                ["report_path", "predictions_path"],
            )

    def test_positions_path_not_required(self):
        """positions_path is optional — the backtest doesn't write it
        when there are no positions. Must NOT be flagged as missing."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            m = _populate_artifacts(Path(td), fold_idx=0)
            # positions_path is None in our test — confirm it doesn't
            # surface in the missing list.
            self.assertIsNone(m.positions_path)
            self.assertEqual(_missing_required_artifacts(m), [])

    def test_empty_path_string_flagged(self):
        """An empty-string path (manifest schema accepts it but the
        engine would crash on load) must be flagged as missing."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            m = _populate_artifacts(Path(td), fold_idx=0)
            empty = replace(m, model_path="")
            self.assertIn("model_path", _missing_required_artifacts(empty))


# ---------------------------------------------------------------------------
# discover() — verify_artifacts behavior
# ---------------------------------------------------------------------------


class DiscoverVerifyArtifactsTests(unittest.TestCase):
    def test_default_verify_skips_manifest_with_missing_model(self):
        """The bug: operator rm'd model_fold3.pkl but manifest stayed.
        Resume should treat this fold as not-resumable."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            output_dir = Path(td) / "out"
            _populate_artifacts(output_dir, fold_idx=0)
            _populate_artifacts(output_dir, fold_idx=1)
            # Delete fold 1's model.
            (output_dir / "model_fold1.pkl").unlink()

            discovered = FoldManifest.discover(output_dir)
            # Fold 0 kept, fold 1 dropped because model is missing.
            self.assertEqual(set(discovered), {0})

    def test_default_verify_skips_manifest_with_missing_predictions(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            output_dir = Path(td) / "out"
            _populate_artifacts(output_dir, fold_idx=2)
            (output_dir / "fold_02_predictions.pkl").unlink()
            self.assertNotIn(2, FoldManifest.discover(output_dir))

    def test_default_verify_skips_manifest_with_missing_report(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            output_dir = Path(td) / "out"
            _populate_artifacts(output_dir, fold_idx=4)
            (output_dir / "fold_04_report.json").unlink()
            self.assertNotIn(4, FoldManifest.discover(output_dir))

    def test_all_artifacts_present_manifest_returned(self):
        """Sanity: when every artifact is present, the manifest is
        returned as before."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            output_dir = Path(td) / "out"
            _populate_artifacts(output_dir, fold_idx=0)
            _populate_artifacts(output_dir, fold_idx=1)
            discovered = FoldManifest.discover(output_dir)
            self.assertEqual(set(discovered), {0, 1})

    def test_verify_off_returns_manifest_despite_missing_artifacts(self):
        """``verify_artifacts=False`` is the escape hatch tests use to
        inspect discover internals without populating artifact files."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            output_dir = Path(td) / "out"
            _populate_artifacts(
                output_dir, fold_idx=0,
                model=False, report=False, predictions=False,
            )
            # All artifacts are missing → with verify on, dropped.
            self.assertEqual(
                FoldManifest.discover(output_dir, verify_artifacts=True),
                {},
            )
            # With verify off, kept.
            self.assertIn(
                0,
                FoldManifest.discover(output_dir, verify_artifacts=False),
            )


# ---------------------------------------------------------------------------
# Engine-level regression: deleted artifact → re-runs fold
# ---------------------------------------------------------------------------


class EngineReRunsFoldWhenArtifactMissingTests(unittest.TestCase):
    """Highest-level proof that the audit P2 bug is closed: if the
    operator deletes a model.pkl between runs, the second run must
    NOT skip that fold (which would crash later when the ensemble
    tries to load the missing pickle)."""

    def test_decide_fold_returns_no_manifest_when_artifact_missing(self):
        """Discover filters out the missing-artifact manifest →
        decide_fold sees an empty discovered map for that index →
        returns "no_manifest" decision so the fold runs."""
        import tempfile

        from src.core.walk_forward._resume import ResumeMode, decide_fold

        with tempfile.TemporaryDirectory() as td:
            output_dir = Path(td) / "out"
            m = _populate_artifacts(output_dir, fold_idx=3)
            # Operator deletes the model artifact.
            Path(m.model_path).unlink()

            # Discover sees the manifest but rejects it because model
            # is missing; the engine then asks decide_fold whether
            # fold 3 should run, finds it absent from discovered, and
            # returns "no_manifest" → fold runs.
            discovered = FoldManifest.discover(output_dir)
            self.assertNotIn(3, discovered)

            decision = decide_fold(
                fold_index=3,
                train_period=m.train_period,
                test_period=m.test_period,
                config_fingerprint=m.config_fingerprint,
                discovered=discovered,
                resume_mode=ResumeMode.AUTO,
            )
            self.assertFalse(decision.skip)
            self.assertEqual(decision.reason, "no_manifest")


if __name__ == "__main__":
    unittest.main()
