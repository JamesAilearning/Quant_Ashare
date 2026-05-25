"""Tests for ``src.core.walk_forward._resume``: manifest roundtrip,
fingerprint computation, resume decision matrix.

The engine integration test (running a real fold then resuming) lives
behind ``@skip_unless_e2e`` because it touches qlib + model training.
This file is fast unit tests on the pure-arithmetic core.
"""

from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.walk_forward._resume import (  # noqa: E402
    MANIFEST_VERSION,
    FoldManifest,
    ResumeDecision,
    ResumeMode,
    compute_config_fingerprint,
    decide_fold,
)
from src.core.walk_forward._types import WalkForwardFold  # noqa: E402
from src.core.walk_forward.config import WalkForwardConfig  # noqa: E402


def _make_fold(idx: int = 3) -> WalkForwardFold:
    return WalkForwardFold(
        fold_index=idx,
        train_period="2022-01-01 ~ 2023-12-31",
        valid_period="2024-01-01 ~ 2024-03-31",
        test_period="2024-04-01 ~ 2024-06-30",
        ic_1d=0.045,
        ic_5d=0.038,
        annualized_return=0.12,
        max_drawdown=-0.08,
        information_ratio=0.85,
        prediction_shape=(123, 250),
        report_path="output/wf/fold_03_report.json",
    )


def _make_config(**overrides) -> WalkForwardConfig:
    base = dict(
        instruments="csi300",
        feature_handler="Alpha158",
        overall_start="2022-01-01",
        overall_end="2025-12-31",
        train_months=24,
        valid_months=3,
        test_months=3,
        step_months=3,
        topk=50,
        output_dir="output/wf",
    )
    base.update(overrides)
    return WalkForwardConfig(**base)


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------


class FingerprintTests(unittest.TestCase):
    def test_excludes_output_dir(self) -> None:
        """Renaming output_dir must not change the fingerprint —
        otherwise an operator who renames their results folder loses
        all resume eligibility."""
        a = _make_config(output_dir="output/wf_run1")
        b = _make_config(output_dir="output/wf_run2_renamed")
        self.assertEqual(
            compute_config_fingerprint(a),
            compute_config_fingerprint(b),
        )

    def test_includes_train_months(self) -> None:
        a = _make_config(train_months=24)
        b = _make_config(train_months=18)
        self.assertNotEqual(
            compute_config_fingerprint(a),
            compute_config_fingerprint(b),
        )

    def test_includes_feature_handler(self) -> None:
        a = _make_config(feature_handler="Alpha158")
        b = _make_config(feature_handler="MinedFactor")
        self.assertNotEqual(
            compute_config_fingerprint(a),
            compute_config_fingerprint(b),
        )

    def test_deterministic_across_calls(self) -> None:
        cfg = _make_config()
        self.assertEqual(
            compute_config_fingerprint(cfg),
            compute_config_fingerprint(cfg),
        )

    def test_rejects_non_dataclass(self) -> None:
        with self.assertRaises(TypeError):
            compute_config_fingerprint({"a": 1})


# ---------------------------------------------------------------------------
# Manifest roundtrip
# ---------------------------------------------------------------------------


class ManifestRoundTripTests(unittest.TestCase):
    def test_to_dict_from_dict_roundtrip(self) -> None:
        cfg = _make_config()
        fold = _make_fold(idx=3)
        m = FoldManifest.from_fold(
            fold=fold,
            config=cfg,
            model_path="output/wf/model_fold3.pkl",
            report_path="output/wf/fold_03_report.json",
            predictions_path="output/wf/fold_03_predictions.pkl",
            positions_path="output/wf/fold_03_positions.json",
        )
        payload = m.to_dict()
        reborn = FoldManifest.from_dict(payload)
        self.assertEqual(reborn.fold_index, 3)
        self.assertEqual(reborn.fold, fold)
        self.assertEqual(reborn.config_fingerprint, m.config_fingerprint)
        self.assertEqual(reborn.version, MANIFEST_VERSION)

    def test_prediction_shape_tuple_survives_json(self) -> None:
        """JSON serializes tuples as lists; round-trip must restore tuple."""
        cfg = _make_config()
        fold = _make_fold()
        m = FoldManifest.from_fold(
            fold=fold, config=cfg,
            model_path="m", report_path="r",
            predictions_path="p", positions_path=None,
        )
        s = json.dumps(m.to_dict())
        reborn = FoldManifest.from_dict(json.loads(s))
        self.assertIsInstance(reborn.fold.prediction_shape, tuple)
        self.assertEqual(reborn.fold.prediction_shape, (123, 250))

    def test_save_load_via_disk(self) -> None:
        import tempfile

        cfg = _make_config()
        fold = _make_fold(idx=5)
        m = FoldManifest.from_fold(
            fold=fold, config=cfg,
            model_path="m", report_path="r",
            predictions_path="p", positions_path=None,
        )
        with tempfile.TemporaryDirectory() as td:
            written = m.save(td)
            self.assertTrue(written.exists())
            self.assertEqual(written.name, "fold_05_manifest.json")
            reloaded = FoldManifest.load(td, fold_index=5)
            self.assertEqual(reloaded, m)

    def test_save_is_atomic_no_tmp_left_behind(self) -> None:
        """The .tmp + rename pattern must not leave the .tmp file."""
        import tempfile

        cfg = _make_config()
        m = FoldManifest.from_fold(
            fold=_make_fold(idx=0), config=cfg,
            model_path="m", report_path="r",
            predictions_path="p", positions_path=None,
        )
        with tempfile.TemporaryDirectory() as td:
            m.save(td)
            tmp_files = list(Path(td).glob("*.tmp"))
            self.assertEqual(tmp_files, [])


# ---------------------------------------------------------------------------
# Discover
# ---------------------------------------------------------------------------


class DiscoverTests(unittest.TestCase):
    def test_empty_dir_returns_empty_dict(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(FoldManifest.discover(td), {})

    def test_nonexistent_dir_returns_empty_dict(self) -> None:
        self.assertEqual(
            FoldManifest.discover("/does/not/exist/nope"), {},
        )

    def test_discover_finds_all_well_formed_manifests(self) -> None:
        import tempfile

        cfg = _make_config()
        with tempfile.TemporaryDirectory() as td:
            for i in (0, 1, 3):  # gap at 2
                m = FoldManifest.from_fold(
                    fold=_make_fold(idx=i),
                    config=cfg,
                    model_path=f"m{i}", report_path=f"r{i}",
                    predictions_path=f"p{i}", positions_path=None,
                )
                m.save(td)
            found = FoldManifest.discover(td)
            self.assertEqual(sorted(found.keys()), [0, 1, 3])

    def test_discover_skips_malformed_json(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "fold_00_manifest.json").write_text(
                "{not valid json", encoding="utf-8",
            )
            # Should not raise; should not include fold_00 in result.
            self.assertEqual(FoldManifest.discover(td), {})

    def test_discover_skips_wrong_schema_version(self) -> None:
        import tempfile

        cfg = _make_config()
        with tempfile.TemporaryDirectory() as td:
            m = FoldManifest.from_fold(
                fold=_make_fold(idx=0),
                config=cfg,
                model_path="m", report_path="r",
                predictions_path="p", positions_path=None,
            )
            payload = m.to_dict()
            payload["version"] = 999
            (Path(td) / "fold_00_manifest.json").write_text(
                json.dumps(payload), encoding="utf-8",
            )
            self.assertEqual(FoldManifest.discover(td), {})


# ---------------------------------------------------------------------------
# decide_fold — pure-function resume decision matrix
# ---------------------------------------------------------------------------


class DecideFoldTests(unittest.TestCase):
    def _make_manifest(self, idx, *, fingerprint="abc123", train="A", test="B"):
        cfg = _make_config()
        fold = replace(
            _make_fold(idx=idx),
            train_period=train, test_period=test,
        )
        m = FoldManifest.from_fold(
            fold=fold, config=cfg,
            model_path=f"m{idx}", report_path=f"r{idx}",
            predictions_path=f"p{idx}", positions_path=None,
        )
        return replace(m, config_fingerprint=fingerprint)

    def test_auto_with_no_manifest_runs(self) -> None:
        decision = decide_fold(
            fold_index=0,
            train_period="A", test_period="B",
            config_fingerprint="fp1",
            discovered={},
            resume_mode=ResumeMode.AUTO,
        )
        self.assertFalse(decision.skip)
        self.assertIsNone(decision.manifest)
        self.assertEqual(decision.reason, "no_manifest")

    def test_auto_with_matching_manifest_skips(self) -> None:
        m = self._make_manifest(0, fingerprint="fp1")
        decision = decide_fold(
            fold_index=0,
            train_period=m.train_period, test_period=m.test_period,
            config_fingerprint="fp1",
            discovered={0: m},
            resume_mode=ResumeMode.AUTO,
        )
        self.assertTrue(decision.skip)
        self.assertEqual(decision.manifest, m)
        self.assertEqual(decision.reason, "resumed_from_manifest")

    def test_auto_with_fingerprint_mismatch_reruns(self) -> None:
        m = self._make_manifest(0, fingerprint="stale")
        decision = decide_fold(
            fold_index=0,
            train_period=m.train_period, test_period=m.test_period,
            config_fingerprint="current",
            discovered={0: m},
            resume_mode=ResumeMode.AUTO,
        )
        self.assertFalse(decision.skip)
        self.assertIsNone(decision.manifest)
        self.assertIn("fingerprint_mismatch", decision.reason)

    def test_auto_with_window_mismatch_reruns(self) -> None:
        m = self._make_manifest(0, fingerprint="fp1", train="X", test="Y")
        decision = decide_fold(
            fold_index=0,
            train_period="A", test_period="B",
            config_fingerprint="fp1",
            discovered={0: m},
            resume_mode=ResumeMode.AUTO,
        )
        self.assertFalse(decision.skip)
        self.assertIn("window_mismatch", decision.reason)

    def test_force_rerun_ignores_matching_manifest(self) -> None:
        m = self._make_manifest(0, fingerprint="fp1")
        decision = decide_fold(
            fold_index=0,
            train_period=m.train_period, test_period=m.test_period,
            config_fingerprint="fp1",
            discovered={0: m},
            resume_mode=ResumeMode.FORCE_RERUN,
        )
        self.assertFalse(decision.skip)
        self.assertEqual(decision.reason, "force_rerun")

    def test_resume_from_fold_n_below_n_resumes(self) -> None:
        m = self._make_manifest(1, fingerprint="fp1")
        decision = decide_fold(
            fold_index=1,
            train_period=m.train_period, test_period=m.test_period,
            config_fingerprint="fp1",
            discovered={1: m},
            resume_mode=ResumeMode.from_fold(3),
        )
        self.assertTrue(decision.skip)

    def test_resume_from_fold_n_at_n_reruns(self) -> None:
        m = self._make_manifest(3, fingerprint="fp1")
        decision = decide_fold(
            fold_index=3,
            train_period=m.train_period, test_period=m.test_period,
            config_fingerprint="fp1",
            discovered={3: m},
            resume_mode=ResumeMode.from_fold(3),
        )
        self.assertFalse(decision.skip)
        self.assertIn("resume_from_fold_3", decision.reason)


# ---------------------------------------------------------------------------
# ResumeMode constructors
# ---------------------------------------------------------------------------


class ResumeModeTests(unittest.TestCase):
    def test_from_fold_rejects_negative(self) -> None:
        with self.assertRaises(ValueError):
            ResumeMode.from_fold(-1)

    def test_from_fold_rejects_non_int(self) -> None:
        with self.assertRaises(ValueError):
            ResumeMode.from_fold("3")  # type: ignore[arg-type]

    def test_auto_and_force_rerun_constants_are_singletons(self) -> None:
        self.assertIs(ResumeMode.AUTO, ResumeMode.AUTO)
        self.assertIs(ResumeMode.FORCE_RERUN, ResumeMode.FORCE_RERUN)


if __name__ == "__main__":
    unittest.main()
