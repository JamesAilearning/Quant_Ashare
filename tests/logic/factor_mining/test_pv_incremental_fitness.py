"""pv_incremental_v1 GP wiring + baseline exporter tests.

Dimensions: orthogonality hinge (inert default / band / weight /
uncovered) × engine threading (baseline plumbing, per-day Spearman,
cache invalidation, checkpoint key) × miner baseline loading
(provenance binding refusals) × exporter (plan discipline, fold sha
verification, window discipline, ensemble semantics, assembly,
sidecar disclosure).
"""

from __future__ import annotations

import hashlib
import json
import pickle
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import scripts.research.pv_incremental_baseline_export as bx  # noqa: E402
from src.factor_mining.fitness import (  # noqa: E402
    FitnessConfig,
    orthogonality_penalty,
)


def _plan() -> dict:
    return bx.load_frozen_plan()


class OrthogonalityHingeTests(unittest.TestCase):
    def test_inert_by_default(self) -> None:
        # The v1 formula must be untouched unless a campaign opts in.
        cfg = FitnessConfig()
        self.assertEqual(0.0, cfg.w_orthogonality)
        self.assertEqual(0.0, orthogonality_penalty(0.99, cfg))

    def test_band_is_free_excess_is_charged(self) -> None:
        cfg = FitnessConfig(w_orthogonality=2.0, orthogonality_band=0.30)
        self.assertEqual(0.0, orthogonality_penalty(0.10, cfg))
        self.assertEqual(0.0, orthogonality_penalty(0.30, cfg))
        self.assertAlmostEqual(0.4, orthogonality_penalty(0.50, cfg))
        self.assertAlmostEqual(1.4, orthogonality_penalty(1.00, cfg))

    def test_uncovered_days_carry_no_penalty(self) -> None:
        # Operator decision A: the IS window starts before the
        # baseline's first out-of-fold date, so an expression with no
        # baseline overlap is unpenalised (and the gap is reported at
        # run level), never scored against a baseline that isn't there.
        cfg = FitnessConfig(w_orthogonality=2.0, orthogonality_band=0.30)
        self.assertEqual(0.0, orthogonality_penalty(float("nan"), cfg))

    def test_frozen_constants_match_the_plan(self) -> None:
        orth = _plan()["fitness"]["orthogonality"]
        self.assertEqual(0.30, orth["fitness_band_abs_rho"])
        self.assertEqual(2.0, orth["fitness_penalty_weight"])
        self.assertEqual("penalize_covered_days_only",
                         orth["is_coverage_policy"])


class EngineBaselineWiringTests(unittest.TestCase):
    @staticmethod
    def _engine(**fit_kw):
        from src.factor_mining.gp_engine import GPConfig, GPEngine
        return GPEngine(GPConfig(seed=7), FitnessConfig(**fit_kw))

    @staticmethod
    def _frame(seed: int, dates, cols=("a", "b", "c", "d")):
        rng = np.random.default_rng(seed)
        return pd.DataFrame(
            rng.normal(size=(len(dates), len(cols))),
            index=pd.DatetimeIndex(dates), columns=list(cols))

    def test_identical_frames_give_rho_one(self) -> None:
        dates = pd.date_range("2023-01-02", periods=5, freq="B")
        f = self._frame(1, dates)
        eng = self._engine(w_orthogonality=2.0, orthogonality_band=0.30)
        eng._baseline = f
        self.assertAlmostEqual(1.0, eng._baseline_orthogonality(f))

    def test_disjoint_dates_are_uncovered_not_zero(self) -> None:
        # NaN (uncovered) and 0.0 (measured, orthogonal) must not be
        # conflated — the first means "no baseline here", the second
        # "baseline here and uncorrelated".
        eng = self._engine(w_orthogonality=2.0, orthogonality_band=0.30)
        eng._baseline = self._frame(
            2, pd.date_range("2023-01-02", periods=5, freq="B"))
        early = self._frame(
            3, pd.date_range("2018-01-02", periods=5, freq="B"))
        self.assertTrue(np.isnan(eng._baseline_orthogonality(early)))
        self.assertEqual(1, eng._orthogonality_uncovered)
        self.assertEqual(1, eng._orthogonality_scored)

    def test_inert_when_weight_zero(self) -> None:
        # No baseline work at all under the default fitness — the
        # legacy path pays nothing.
        dates = pd.date_range("2023-01-02", periods=5, freq="B")
        eng = self._engine()
        eng._baseline = self._frame(4, dates)
        self.assertTrue(np.isnan(
            eng._baseline_orthogonality(self._frame(4, dates))))
        self.assertEqual(0, eng._orthogonality_scored)

    def test_degenerate_days_skipped(self) -> None:
        dates = pd.date_range("2023-01-02", periods=3, freq="B")
        flat = pd.DataFrame(1.0, index=dates,
                            columns=["a", "b", "c", "d"])
        eng = self._engine(w_orthogonality=2.0, orthogonality_band=0.30)
        eng._baseline = flat
        self.assertTrue(np.isnan(
            eng._baseline_orthogonality(self._frame(5, dates))))

    def test_baseline_key_invalidates_cache(self) -> None:
        # A resume against a DIFFERENT baseline must discard scores:
        # the orthogonality penalty is invisible to the coverage key.
        from src.factor_mining.gp_engine import _baseline_key_for
        dates = pd.date_range("2023-01-02", periods=5, freq="B")
        b1, b2 = self._frame(6, dates), self._frame(7, dates)
        self.assertEqual("no_baseline", _baseline_key_for(None))
        self.assertNotEqual(_baseline_key_for(b1), _baseline_key_for(b2))
        # Stable across calls (sha256, not the salted builtin hash).
        self.assertEqual(_baseline_key_for(b1), _baseline_key_for(b1))

    def test_checkpoint_carries_baseline_key(self) -> None:
        from src.factor_mining.gp_engine import GPEngine
        eng = self._engine(w_orthogonality=2.0, orthogonality_band=0.30)
        dates = pd.date_range("2023-01-02", periods=4, freq="B")
        eng._baseline = self._frame(8, dates)
        eng._baseline_key = "baseline:deadbeefdeadbeef"
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ckpt.json"
            eng.save_checkpoint(p)
            state = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual("baseline:deadbeefdeadbeef",
                             state["baseline_key"])
            restored = GPEngine.load_checkpoint(
                p, fitness_config=FitnessConfig())
            self.assertEqual("baseline:deadbeefdeadbeef",
                             restored._baseline_key)

    def test_legacy_checkpoint_defaults_to_no_baseline(self) -> None:
        from src.factor_mining.gp_engine import GPEngine
        eng = self._engine()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ckpt.json"
            eng.save_checkpoint(p)
            state = json.loads(p.read_text(encoding="utf-8"))
            del state["baseline_key"]          # pre-campaign checkpoint
            p.write_text(json.dumps(state), encoding="utf-8")
            restored = GPEngine.load_checkpoint(
                p, fitness_config=FitnessConfig())
            self.assertEqual("no_baseline", restored._baseline_key)


class MinerBaselineLoadingTests(unittest.TestCase):
    """The miner must refuse an unbound baseline: it keys the only
    incremental criterion the campaign has."""

    @staticmethod
    def _config(tmp: str, *, path: str, model: str):
        from src.factor_mining.gp_engine import GPConfig
        from src.factor_mining.miner import DataConfig, MinerConfig
        return MinerConfig(
            data=DataConfig(baseline_preds_path=path,
                            baseline_model=model),
            gp=GPConfig(seed=1), fitness=FitnessConfig(),
            output_dir=Path(tmp))

    def _write_baseline(self, tmp: str) -> tuple[Path, str]:
        frame = pd.DataFrame(
            {"a": [0.1, 0.2], "b": [0.3, 0.4]},
            index=pd.DatetimeIndex(["2023-01-02", "2023-01-03"]))
        path = Path(tmp) / "baseline_preds.parquet"
        frame.to_parquet(path)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_absent_path_is_inert(self) -> None:
        from src.factor_mining.miner import load_baseline_predictions
        with tempfile.TemporaryDirectory() as d:
            cfg = self._config(d, path="", model="")
            self.assertIsNone(load_baseline_predictions(cfg))

    def test_provenance_refusals_and_happy_path(self) -> None:
        from src.factor_mining.miner import load_baseline_predictions
        model = "alpha158_lgb_csi800_walkforward"
        with tempfile.TemporaryDirectory() as d:
            path, sha = self._write_baseline(d)
            cfg = self._config(d, path=str(path), model=model)
            sidecar = path.with_name(path.name + ".provenance.json")
            # 1. sidecar missing
            with self.assertRaises(ValueError) as ctx:
                load_baseline_predictions(cfg)
            self.assertIn("provenance sidecar", str(ctx.exception))
            good = {"model": model, "file_sha256": sha,
                    "run_config_sha256": "ab" * 32, "source_git": "c" * 40}
            for label, mutate in (
                    ("wrong model", {"model": "adhoc"}),
                    ("stale sha", {"file_sha256": "0" * 64}),
                    ("empty run_config", {"run_config_sha256": ""}),
                    ("missing git", {"source_git": None})):
                prov = dict(good)
                prov.update(mutate)
                sidecar.write_text(json.dumps(prov), encoding="utf-8")
                with self.assertRaises(ValueError, msg=label):
                    load_baseline_predictions(cfg)
            sidecar.write_text(json.dumps(good), encoding="utf-8")
            frame = load_baseline_predictions(cfg)
            self.assertEqual((2, 2), frame.shape)

    def test_missing_model_declaration_refuses(self) -> None:
        from src.factor_mining.miner import load_baseline_predictions
        with tempfile.TemporaryDirectory() as d:
            path, _ = self._write_baseline(d)
            cfg = self._config(d, path=str(path), model="")
            with self.assertRaises(ValueError) as ctx:
                load_baseline_predictions(cfg)
            self.assertIn("baseline_model", str(ctx.exception))


class BaselineExporterTests(unittest.TestCase):
    @staticmethod
    def _fold(run_dir: Path, index: int, start: str, end: str, *,
              ensemble_window: int = 3, dates=None, tamper: bool = False):
        dates = dates if dates is not None else pd.date_range(
            start, periods=3, freq="B")
        idx = pd.MultiIndex.from_product(
            [dates, ["a", "b", "c"]], names=["datetime", "instrument"])
        scores = pd.Series(
            np.linspace(0.1, 0.9, len(idx)), index=idx)
        raw = pickle.dumps(scores)
        pkl = run_dir / f"fold_{index:02d}_predictions.pkl"
        pkl.write_bytes(raw)
        sha = hashlib.sha256(raw).hexdigest()
        if tamper:
            pkl.write_bytes(pickle.dumps(scores * 2.0))
        (run_dir / f"fold_{index:02d}_report.json").write_text(
            json.dumps({
                "fold_index": index,
                "windows": {"train": {"start": "2018-01-01",
                                      "end": "2019-12-27"},
                            "valid": {"start": "2020-01-01",
                                      "end": "2020-03-27"},
                            "test": {"start": start, "end": end}},
                "ensemble": {"window": ensemble_window,
                             "prediction_artifact_sha256": sha},
            }), encoding="utf-8")

    @staticmethod
    def _agg(run_dir: Path, *, n_folds: int, commit: str = "a" * 40,
             dirty: bool = False):
        (run_dir / "walk_forward_report.json").write_text(
            json.dumps({"git_commit": commit, "git_dirty": dirty,
                        "num_folds": n_folds}), encoding="utf-8")

    def test_frozen_plan_loads_and_refuses_unblinded(self) -> None:
        import yaml
        plan = _plan()
        self.assertEqual("pv_incremental_v1", plan["protocol_id"])
        with tempfile.TemporaryDirectory() as d:
            bad = dict(plan)
            bad["holdout_unblinded"] = True
            p = Path(d) / "plan.yaml"
            p.write_text(yaml.safe_dump(bad), encoding="utf-8")
            with self.assertRaises(bx.PVBaselineError):
                bx.load_frozen_plan(p)

    def test_run_provenance_refusals(self) -> None:
        bx.check_run_provenance({"git_commit": "a" * 40,
                                 "git_dirty": False})
        for label, agg in (
                ("mixed-commit resume",
                 {"git_commit": None, "git_dirty": False}),
                ("short sha", {"git_commit": "abc", "git_dirty": False}),
                ("dirty tree",
                 {"git_commit": "a" * 40, "git_dirty": True})):
            with self.assertRaises(bx.PVBaselineError, msg=label):
                bx.check_run_provenance(agg)

    def test_holdout_fold_refuses(self) -> None:
        # The sacred invariant at the baseline boundary: a fold whose
        # test window reaches into the blinded 2025 holdout (the
        # parent config's default overall_end would do exactly this)
        # must never be exported.
        plan = _plan()
        ok = [{"fold_index": 0, "test_start": "2020-04-01",
               "test_end": "2020-06-30"}]
        bx.check_fold_windows(plan, ok)
        bad = ok + [{"fold_index": 19, "test_start": "2025-01-02",
                     "test_end": "2025-03-31"}]
        with self.assertRaises(bx.PVBaselineError) as ctx:
            bx.check_fold_windows(plan, bad)
        self.assertIn("fold 19", str(ctx.exception))

    def test_ensemble_window_must_be_frozen(self) -> None:
        plan = _plan()
        self.assertEqual(3, bx.check_ensemble_semantics(
            plan, [{"fold_index": 0, "ensemble_window": 3}]))
        with self.assertRaises(bx.PVBaselineError) as ctx:
            bx.check_ensemble_semantics(
                plan, [{"fold_index": 0, "ensemble_window": 3},
                       {"fold_index": 1, "ensemble_window": 1}])
        self.assertIn("fold 1", str(ctx.exception))

    def test_overlapping_folds_refuse(self) -> None:
        dates = pd.date_range("2023-01-02", periods=2, freq="B")
        idx = pd.MultiIndex.from_product(
            [dates, ["a"]], names=["datetime", "instrument"])
        s = pd.Series([0.1, 0.2], index=idx)
        with self.assertRaises(bx.PVBaselineError) as ctx:
            bx.assemble_wide([{"scores": s}, {"scores": s}])
        self.assertIn("duplicated", str(ctx.exception))

    def test_end_to_end_export_and_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d) / "run"
            run_dir.mkdir()
            self._fold(run_dir, 0, "2020-04-01", "2020-06-30",
                       dates=pd.date_range("2020-04-01", periods=3,
                                           freq="B"))
            self._fold(run_dir, 1, "2023-01-02", "2023-03-31",
                       dates=pd.date_range("2023-01-02", periods=3,
                                           freq="B"))
            self._agg(run_dir, n_folds=2)
            out = Path(d) / "out"
            rc = bx.main(["--run-dir", str(run_dir),
                          "--out-dir", str(out)])
            self.assertEqual(0, rc)
            wide = pd.read_parquet(out / "baseline_preds.parquet")
            self.assertEqual((6, 3), wide.shape)
            sidecar = json.loads(
                (out / "baseline_preds.parquet.provenance.json")
                .read_text(encoding="utf-8"))
            self.assertEqual("alpha158_lgb_csi800_walkforward",
                             sidecar["model"])
            self.assertEqual("a" * 40, sidecar["source_git"])
            self.assertEqual(3, sidecar["ensemble_window"])
            self.assertEqual(
                hashlib.sha256(
                    (out / "baseline_preds.parquet").read_bytes()
                ).hexdigest(), sidecar["file_sha256"])
            # Decision A disclosure: partial IS coverage is recorded.
            self.assertEqual(3, sidecar["coverage"]["is_days_covered"])
            self.assertEqual(3, sidecar["coverage"]["oos_days_covered"])
            self.assertEqual("2020-04-01",
                             sidecar["coverage"]["first_baseline_date"])
            # A second export into the same dir refuses (never
            # overwrite a baseline other scores are keyed to).
            self.assertEqual(2, bx.main(["--run-dir", str(run_dir),
                                         "--out-dir", str(out)]))

    def test_tampered_fold_pickle_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d) / "run"
            run_dir.mkdir()
            self._fold(run_dir, 0, "2023-01-02", "2023-03-31",
                       tamper=True)
            self._agg(run_dir, n_folds=1)
            self.assertEqual(2, bx.main(
                ["--run-dir", str(run_dir),
                 "--out-dir", str(Path(d) / "out")]))

    def test_partial_run_dir_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d) / "run"
            run_dir.mkdir()
            self._fold(run_dir, 0, "2023-01-02", "2023-03-31")
            self._agg(run_dir, n_folds=5)      # declares more folds
            self.assertEqual(2, bx.main(
                ["--run-dir", str(run_dir),
                 "--out-dir", str(Path(d) / "out")]))


class D5GateTests(unittest.TestCase):
    """The baseline path must not weaken the D5 strict gate: the new
    loader is plain pandas/json/hashlib, and the orthogonality term
    lives entirely inside factor_mining."""

    # Same predicates as the existing per-module D5 gates: IMPORT
    # statements, not any mention of the words (the docstrings state
    # the guarantee out loud on purpose).
    _FORBIDDEN = ("from qlib", "import qlib", "qlib.data", "qlib.init",
                  "from src.pit", "import src.pit")

    def test_miner_baseline_loader_is_qlib_and_pit_free(self) -> None:
        import inspect

        from src.factor_mining.miner import load_baseline_predictions
        src = inspect.getsource(load_baseline_predictions)
        for forbidden in self._FORBIDDEN:
            self.assertNotIn(forbidden, src)

    def test_engine_orthogonality_is_qlib_and_pit_free(self) -> None:
        import inspect

        from src.factor_mining.gp_engine import GPEngine
        src = inspect.getsource(GPEngine._baseline_orthogonality)
        for forbidden in self._FORBIDDEN:
            self.assertNotIn(forbidden, src)


if __name__ == "__main__":
    unittest.main()
