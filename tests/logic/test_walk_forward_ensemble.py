"""Tests for ``src.core.walk_forward.ensemble``.

`apply_ensemble` averages the current fold's predictions with up to
N-1 prior fold models. The risk surface is wide: prior model loading,
index alignment, version mismatches, sidecar verification,
backward-compat for bare-path refs vs tuple refs. We cover the
behaviors dimensionally — one test per category, not "≥N cases".
"""

from __future__ import annotations

import hashlib
import json
import pickle
import sys
import unittest
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.walk_forward.ensemble import (  # noqa: E402
    apply_ensemble,
    write_prediction_artifact,
)


def _make_preds(n_dates=5, instruments=("A", "B", "C"), value=0.5) -> pd.Series:
    dates = pd.date_range("2024-01-01", periods=n_dates)
    idx = pd.MultiIndex.from_product(
        [dates, instruments], names=["datetime", "instrument"],
    )
    return pd.Series(value, index=idx, name="score")


class _FakeModel:
    """Stand-in for a qlib model. Returns a Series with the given value."""

    def __init__(self, predictions: pd.Series):
        self._predictions = predictions

    def predict(self, _dataset, _segment):
        return self._predictions


class _BadModel:
    """Returns a DataFrame instead of a Series (rejected by ensemble)."""

    def predict(self, _ds, _seg):
        return pd.DataFrame({"x": [1, 2, 3]})


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(obj, f)


# ---------------------------------------------------------------------------
# No-op fast paths
# ---------------------------------------------------------------------------


class EnsembleNoopTests(unittest.TestCase):
    def test_window_one_returns_input_unchanged(self):
        preds = _make_preds()
        out, meta = apply_ensemble(
            current_predictions=preds, current_dataset=None,
            prior_model_paths=[("a.pkl",)], ensemble_window=1,
            current_fold_index=0,
        )
        self.assertIs(out, preds)
        self.assertFalse(meta["used"])
        self.assertEqual(meta["n_models"], 1)
        self.assertEqual(meta["window"], 1)

    def test_no_prior_paths_returns_input_unchanged(self):
        preds = _make_preds()
        out, meta = apply_ensemble(
            current_predictions=preds, current_dataset=None,
            prior_model_paths=[], ensemble_window=3,
            current_fold_index=2,
        )
        self.assertIs(out, preds)
        self.assertFalse(meta["used"])

    def test_meta_shape_present_even_on_noop(self):
        preds = _make_preds()
        _out, meta = apply_ensemble(
            current_predictions=preds, current_dataset=None,
            prior_model_paths=[], ensemble_window=1,
            current_fold_index=0,
        )
        # The downstream report consumer relies on these keys always
        # being present so the JSON shape is uniform across folds.
        for key in (
            "window", "used", "n_models", "contributing_folds",
            "contributing_model_refs", "prior_models_attempted",
            "prior_models_loaded", "prior_models_index_mismatched",
            "rejected_priors",
        ):
            self.assertIn(key, meta)


# ---------------------------------------------------------------------------
# Successful averaging
# ---------------------------------------------------------------------------


class EnsembleAveragingTests(unittest.TestCase):
    def test_averages_two_models(self):
        import tempfile

        preds = _make_preds(value=1.0)
        prior_preds = _make_preds(value=3.0)
        with tempfile.TemporaryDirectory() as td:
            prior_path = Path(td) / "fold0.pkl"
            _dump(prior_path, _FakeModel(prior_preds))
            out, meta = apply_ensemble(
                current_predictions=preds, current_dataset=None,
                prior_model_paths=[(0, str(prior_path))], ensemble_window=2,
                current_fold_index=1,
            )
        # mean(1.0, 3.0) = 2.0 across every (date, instrument)
        self.assertTrue((out == 2.0).all())
        self.assertTrue(meta["used"])
        self.assertEqual(meta["n_models"], 2)
        self.assertEqual(meta["contributing_folds"], [0, 1])
        self.assertEqual(meta["prior_models_loaded"], 1)

    def test_window_caps_at_window_minus_one_priors(self):
        """``ensemble_window=3`` with 5 priors must only use the most
        recent 2 (window-1)."""
        import tempfile

        preds = _make_preds(value=1.0)
        with tempfile.TemporaryDirectory() as td:
            priors = []
            for i in range(5):
                p = Path(td) / f"fold{i}.pkl"
                _dump(p, _FakeModel(_make_preds(value=float(i))))
                priors.append((i, str(p)))
            _out, meta = apply_ensemble(
                current_predictions=preds, current_dataset=None,
                prior_model_paths=priors, ensemble_window=3,
                current_fold_index=5,
            )
        # Only 2 priors (window - 1) attempted.
        self.assertEqual(meta["prior_models_attempted"], 2)
        # The most-recent priors (indices 3 and 4) were the ones picked.
        self.assertEqual(meta["contributing_folds"], [3, 4, 5])


# ---------------------------------------------------------------------------
# Skip / rejection paths
# ---------------------------------------------------------------------------


class EnsembleRejectionTests(unittest.TestCase):
    def test_index_mismatch_skips_and_records(self):
        import tempfile

        preds = _make_preds(value=1.0)
        # Prior predicts a different instrument universe → index mismatch.
        prior_preds = _make_preds(instruments=("X", "Y", "Z"), value=3.0)
        with tempfile.TemporaryDirectory() as td:
            prior_path = Path(td) / "fold0.pkl"
            _dump(prior_path, _FakeModel(prior_preds))
            out, meta = apply_ensemble(
                current_predictions=preds, current_dataset=None,
                prior_model_paths=[(0, str(prior_path))], ensemble_window=2,
                current_fold_index=1,
            )
        # Average not done → falls back to current preds only.
        self.assertTrue((out == 1.0).all())
        self.assertEqual(meta["prior_models_index_mismatched"], 1)
        self.assertEqual(meta["prior_models_loaded"], 0)
        self.assertFalse(meta["used"])
        self.assertEqual(len(meta["rejected_priors"]), 1)
        self.assertEqual(meta["rejected_priors"][0]["reason"], "index_mismatch")

    def test_non_series_prediction_rejected(self):
        import tempfile

        preds = _make_preds(value=1.0)

        with tempfile.TemporaryDirectory() as td:
            prior_path = Path(td) / "fold0.pkl"
            _dump(prior_path, _BadModel())
            out, meta = apply_ensemble(
                current_predictions=preds, current_dataset=None,
                prior_model_paths=[(0, str(prior_path))], ensemble_window=2,
                current_fold_index=1,
            )
        self.assertTrue((out == 1.0).all())
        self.assertEqual(meta["prior_models_loaded"], 0)

    def test_corrupt_pickle_skipped_not_raised(self):
        import tempfile

        preds = _make_preds(value=1.0)
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "fold0.pkl"
            bad.write_bytes(b"not a valid pickle")
            # Must NOT raise — just skip and continue.
            out, meta = apply_ensemble(
                current_predictions=preds, current_dataset=None,
                prior_model_paths=[(0, str(bad))], ensemble_window=2,
                current_fold_index=1,
            )
        self.assertIs(out, preds)
        self.assertEqual(meta["prior_models_loaded"], 0)


# ---------------------------------------------------------------------------
# Sidecar (provenance) verification
# ---------------------------------------------------------------------------


class EnsembleSidecarTests(unittest.TestCase):
    def test_sidecar_sha_mismatch_rejects(self):
        import tempfile

        preds = _make_preds(value=1.0)
        prior_preds = _make_preds(value=3.0)
        with tempfile.TemporaryDirectory() as td:
            prior_path = Path(td) / "fold0.pkl"
            _dump(prior_path, _FakeModel(prior_preds))
            sidecar = prior_path.with_suffix(".pkl.meta.json")
            # Write an INTENTIONALLY-WRONG sha for the pickle.
            sidecar.write_text(json.dumps({
                "pkl_sha256": "0" * 64,
            }))
            out, meta = apply_ensemble(
                current_predictions=preds, current_dataset=None,
                prior_model_paths=[(0, str(prior_path))], ensemble_window=2,
                current_fold_index=1,
            )
        self.assertIs(out, preds)
        self.assertEqual(len(meta["rejected_priors"]), 1)
        self.assertIn("sha256 mismatch", meta["rejected_priors"][0]["reason"])

    def test_sidecar_sha_match_accepts(self):
        import tempfile

        preds = _make_preds(value=1.0)
        prior_preds = _make_preds(value=3.0)
        with tempfile.TemporaryDirectory() as td:
            prior_path = Path(td) / "fold0.pkl"
            _dump(prior_path, _FakeModel(prior_preds))
            real_sha = hashlib.sha256(prior_path.read_bytes()).hexdigest()
            sidecar = prior_path.with_suffix(".pkl.meta.json")
            sidecar.write_text(json.dumps({
                "pkl_sha256": real_sha,
            }))
            out, meta = apply_ensemble(
                current_predictions=preds, current_dataset=None,
                prior_model_paths=[(0, str(prior_path))], ensemble_window=2,
                current_fold_index=1,
            )
        self.assertTrue((out == 2.0).all())
        self.assertEqual(meta["prior_models_loaded"], 1)

    # ----------------------------------------------------------------
    # Regression for bug.md P2-9: previously the sidecar version
    # check ONLY looked at ``lightgbm_version``. XGB/CatBoost models
    # could be silently loaded across incompatible framework versions
    # because the relevant version field was never read.
    # ----------------------------------------------------------------

    def test_sidecar_xgb_version_mismatch_rejects(self):
        """An XGB prior model with a stale ``xgboost_version`` must be
        skipped, exactly as the lightgbm path does."""
        import tempfile

        preds = _make_preds(value=1.0)
        prior_preds = _make_preds(value=3.0)
        with tempfile.TemporaryDirectory() as td:
            prior_path = Path(td) / "fold0.pkl"
            _dump(prior_path, _FakeModel(prior_preds))
            real_sha = hashlib.sha256(prior_path.read_bytes()).hexdigest()
            sidecar = prior_path.with_suffix(".pkl.meta.json")
            sidecar.write_text(json.dumps({
                "pkl_sha256": real_sha,
                "model_type": "XGBModel",
                # Fictional ancient version — virtually guaranteed not
                # to match the installed xgboost.
                "xgboost_version": "0.0.0-not-a-real-version",
            }))
            out, meta = apply_ensemble(
                current_predictions=preds, current_dataset=None,
                prior_model_paths=[(0, str(prior_path))], ensemble_window=2,
                current_fold_index=1,
            )
        self.assertIs(out, preds)
        self.assertEqual(len(meta["rejected_priors"]), 1)
        self.assertIn("xgboost", meta["rejected_priors"][0]["reason"])

    def test_sidecar_catboost_version_mismatch_rejects(self):
        """Same as the XGB case but for CatBoost."""
        import tempfile

        preds = _make_preds(value=1.0)
        prior_preds = _make_preds(value=3.0)
        with tempfile.TemporaryDirectory() as td:
            prior_path = Path(td) / "fold0.pkl"
            _dump(prior_path, _FakeModel(prior_preds))
            real_sha = hashlib.sha256(prior_path.read_bytes()).hexdigest()
            sidecar = prior_path.with_suffix(".pkl.meta.json")
            sidecar.write_text(json.dumps({
                "pkl_sha256": real_sha,
                "model_type": "CatBoostModel",
                "catboost_version": "0.0.0-not-a-real-version",
            }))
            out, meta = apply_ensemble(
                current_predictions=preds, current_dataset=None,
                prior_model_paths=[(0, str(prior_path))], ensemble_window=2,
                current_fold_index=1,
            )
        self.assertIs(out, preds)
        self.assertEqual(len(meta["rejected_priors"]), 1)
        self.assertIn("catboost", meta["rejected_priors"][0]["reason"])

    def test_sidecar_lgb_model_ignores_unrelated_framework_version(self):
        """A LGB model's sidecar may opportunistically carry an
        ``xgboost_version`` (the writer logs all installed framework
        versions). A mismatch on the IRRELEVANT framework must not
        reject the model — only the framework matching ``model_type``
        is load-blocking."""
        import tempfile

        import lightgbm as _lgb

        preds = _make_preds(value=1.0)
        prior_preds = _make_preds(value=3.0)
        with tempfile.TemporaryDirectory() as td:
            prior_path = Path(td) / "fold0.pkl"
            _dump(prior_path, _FakeModel(prior_preds))
            real_sha = hashlib.sha256(prior_path.read_bytes()).hexdigest()
            sidecar = prior_path.with_suffix(".pkl.meta.json")
            sidecar.write_text(json.dumps({
                "pkl_sha256": real_sha,
                "model_type": "LGBModel",
                # CORRECT lightgbm version (load should succeed)…
                "lightgbm_version": _lgb.__version__,
                # …but a stale xgboost version (must be ignored).
                "xgboost_version": "0.0.0-not-a-real-version",
            }))
            out, meta = apply_ensemble(
                current_predictions=preds, current_dataset=None,
                prior_model_paths=[(0, str(prior_path))], ensemble_window=2,
                current_fold_index=1,
            )
        self.assertTrue((out == 2.0).all())
        self.assertEqual(meta["prior_models_loaded"], 1)
        self.assertEqual(meta["rejected_priors"], [])

    def test_sidecar_without_model_type_falls_back_to_lightgbm(self):
        """Pre-fix sidecars (and any third-party producers) may omit
        ``model_type``. The check must still happen against
        ``lightgbm_version`` so the historical safety net survives."""
        import tempfile

        preds = _make_preds(value=1.0)
        prior_preds = _make_preds(value=3.0)
        with tempfile.TemporaryDirectory() as td:
            prior_path = Path(td) / "fold0.pkl"
            _dump(prior_path, _FakeModel(prior_preds))
            real_sha = hashlib.sha256(prior_path.read_bytes()).hexdigest()
            sidecar = prior_path.with_suffix(".pkl.meta.json")
            sidecar.write_text(json.dumps({
                "pkl_sha256": real_sha,
                # No model_type → must fall back to lightgbm check.
                "lightgbm_version": "0.0.0-not-a-real-version",
            }))
            out, meta = apply_ensemble(
                current_predictions=preds, current_dataset=None,
                prior_model_paths=[(0, str(prior_path))], ensemble_window=2,
                current_fold_index=1,
            )
        self.assertIs(out, preds)
        self.assertEqual(len(meta["rejected_priors"]), 1)
        self.assertIn("lightgbm", meta["rejected_priors"][0]["reason"])


# ---------------------------------------------------------------------------
# Backward-compat ref shapes
# ---------------------------------------------------------------------------


class EnsembleRefShapeTests(unittest.TestCase):
    def test_bare_path_ref_still_works(self):
        """Tests / direct callers may pass bare path strings instead of
        ``(idx, path)`` tuples. Engine itself always passes tuples."""
        import tempfile

        preds = _make_preds(value=1.0)
        prior_preds = _make_preds(value=3.0)
        with tempfile.TemporaryDirectory() as td:
            prior_path = Path(td) / "fold0.pkl"
            _dump(prior_path, _FakeModel(prior_preds))
            out, meta = apply_ensemble(
                current_predictions=preds, current_dataset=None,
                prior_model_paths=[str(prior_path)],  # bare path, no tuple
                ensemble_window=2,
                current_fold_index=1,
            )
        self.assertTrue((out == 2.0).all())
        self.assertEqual(meta["prior_models_loaded"], 1)


# ---------------------------------------------------------------------------
# write_prediction_artifact — module-level pytest fns so we can use the
# tmp_path fixture (Windows + tempfile.TemporaryDirectory + pickle write
# has a flaky interaction; tmp_path bypasses it).
# ---------------------------------------------------------------------------


def test_write_prediction_artifact_roundtrip(tmp_path):
    preds = _make_preds(value=2.5)
    path = tmp_path / "preds.pkl"
    sha = write_prediction_artifact(path, preds)
    assert path.exists()
    with path.open("rb") as f:
        loaded = pickle.load(f)
    pd.testing.assert_series_equal(loaded, preds)
    # SHA matches the file's bytes
    assert sha == hashlib.sha256(path.read_bytes()).hexdigest()


def test_write_prediction_artifact_sha_is_deterministic(tmp_path):
    preds = _make_preds(value=1.0)
    p1 = tmp_path / "a.pkl"
    p2 = tmp_path / "b.pkl"
    sha1 = write_prediction_artifact(p1, preds)
    sha2 = write_prediction_artifact(p2, preds)
    assert sha1 == sha2


# ---------------------------------------------------------------------------
# Regression for bug.md P2-10: pre-fix ``write_prediction_artifact``
# did ``pickle.dump`` directly into the target path. A crash mid-write
# (OOM, SIGKILL during a long pickle) left a partial pickle, and the
# next resume would EOFError. The fix writes to ``<path>.tmp`` and
# ``os.replace`` only after the dump completes.
# ---------------------------------------------------------------------------


def test_write_prediction_artifact_is_atomic_no_tmp_left(tmp_path):
    """Successful write leaves no ``.tmp`` sibling — the file was
    renamed into place, not copied."""
    preds = _make_preds(value=4.2)
    path = tmp_path / "preds.pkl"
    write_prediction_artifact(path, preds)
    assert path.exists()
    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == [], (
        f"atomic-write must clean up the tmp; found {leftover}"
    )


def test_write_prediction_artifact_failure_does_not_corrupt_existing(
    tmp_path, monkeypatch,
):
    """If a previous write succeeded and a later write fails mid-dump,
    the prior contents must remain intact (the tmp swap never
    completes). Pre-fix the in-place ``pickle.dump`` would truncate
    the target file the moment ``open(path, "wb")`` ran, so any crash
    during ``pickle.dump`` would leave a corrupt file at ``path``."""
    preds_v1 = _make_preds(value=1.0)
    preds_v2 = _make_preds(value=2.0)
    path = tmp_path / "preds.pkl"

    # First successful write.
    write_prediction_artifact(path, preds_v1)
    original_bytes = path.read_bytes()

    # Force pickle.dump to raise mid-write on the second attempt.
    real_dump = pickle.dump

    def boom(*_a, **_kw):
        raise RuntimeError("simulated mid-write crash")

    monkeypatch.setattr(
        "src.core.walk_forward.ensemble.pickle.dump", boom,
    )
    import pytest
    with pytest.raises(RuntimeError, match="simulated mid-write crash"):
        write_prediction_artifact(path, preds_v2)

    # Restore the real dump and verify the original file is intact.
    monkeypatch.setattr("src.core.walk_forward.ensemble.pickle.dump", real_dump)
    assert path.read_bytes() == original_bytes, (
        "atomic write contract violated — a failed write corrupted the "
        "previously-good file (P2-10 regression)"
    )
    # And no tmp leftover.
    assert list(tmp_path.glob("*.tmp")) == []


if __name__ == "__main__":
    unittest.main()
