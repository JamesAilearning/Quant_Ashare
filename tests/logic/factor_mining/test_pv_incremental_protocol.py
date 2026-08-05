"""pv_incremental_v1 three-piece protocol tests (pure cores).

Dimensions: window discipline (frozen-only / holdout / 2026) ×
IC semantics (lag alignment / thin-day drop) × orthogonality (band) ×
FWER (min_n exclusion / dual threshold / tri-state / determinism /
foreign-protocol refusal) × plan loading (drift refusals).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import scripts.research.pv_incremental_eval as ev  # noqa: E402
import scripts.research.pv_incremental_fwer_adjudication as fw  # noqa: E402


def _plan() -> dict:
    return ev.load_frozen_plan()


class FrozenPlanTests(unittest.TestCase):
    def test_committed_plan_loads(self) -> None:
        plan = _plan()
        self.assertEqual("pv_incremental_v1", plan["protocol_id"])
        self.assertIs(False, plan["holdout_unblinded"])

    def test_drifted_plan_refused(self) -> None:
        import tempfile

        import yaml

        for label, mutate in (
                ("wrong protocol", {"protocol_id": "other"}),
                ("unblinded", {"holdout_unblinded": True})):
            plan = dict(_plan())
            plan.update(mutate)
            with tempfile.TemporaryDirectory() as t:
                p = Path(t) / "plan.yaml"
                p.write_text(yaml.safe_dump(plan), encoding="utf-8")
                with self.assertRaises(ev.PVEvalError, msg=label):
                    ev.load_frozen_plan(p)


class WindowDisciplineTests(unittest.TestCase):
    def test_frozen_oos_window_admits(self) -> None:
        ev.check_window_discipline(_plan(), "2023-01-01", "2024-12-31")

    def test_any_other_window_refused(self) -> None:
        plan = _plan()
        for label, (s, e) in (
                ("shifted start", ("2023-01-02", "2024-12-31")),
                ("holdout touch", ("2023-01-01", "2025-06-30")),
                ("2026 cross", ("2023-01-01", "2026-01-02")),
                ("IS window", ("2018-01-01", "2022-12-31"))):
            with self.assertRaises(ev.PVEvalError, msg=label):
                ev.check_window_discipline(plan, s, e)


class MetricSemanticsTests(unittest.TestCase):
    def _frames(self):
        dates = pd.date_range("2023-01-02", periods=6, freq="B")
        cols = [f"S{i}" for i in range(4)]
        close = pd.DataFrame(
            np.cumprod(1 + np.arange(24).reshape(6, 4) * 0.001, axis=0),
            index=dates, columns=cols)
        return dates, cols, close

    def test_forward_return_lag_alignment(self) -> None:
        dates, cols, close = self._frames()
        fwd = ev.forward_returns(close, lag=1)
        # Signal at t uses close[t+1] -> close[t+2]; verify one cell.
        expect = close.iloc[2, 0] / close.iloc[1, 0] - 1.0
        self.assertAlmostEqual(expect, fwd.iloc[0, 0])
        # Last two signal days have no complete forward path.
        self.assertTrue(fwd.iloc[-2:].isna().all().all())

    def test_daily_rank_ic_and_thin_day_drop(self) -> None:
        dates, cols, _ = self._frames()
        factor = pd.DataFrame(
            [[1, 2, 3, 4]] * 6, index=dates, columns=cols, dtype=float)
        fwd = pd.DataFrame(
            [[0.1, 0.2, 0.3, 0.4]] * 6, index=dates, columns=cols)
        ic, dropped = ev.daily_rank_ic(factor, fwd, min_names=4)
        self.assertEqual(0, dropped)
        self.assertTrue(np.allclose(ic.values, 1.0))
        # Thin the cross-section below min_names on one day.
        factor.iloc[0, :2] = np.nan
        ic2, dropped2 = ev.daily_rank_ic(factor, fwd, min_names=4)
        self.assertEqual(1, dropped2)
        self.assertEqual(ic.shape[0] - 1, ic2.shape[0])

    def test_orthogonality_series_detects_clone(self) -> None:
        dates, cols, _ = self._frames()
        base = pd.DataFrame(
            np.random.default_rng(0).normal(size=(6, 4)),
            index=dates, columns=cols)
        orth = ev.orthogonality_series(base, base.copy(), min_names=4)
        self.assertTrue(np.allclose(orth.values, 1.0))

    def test_degenerate_day_dropped_not_recorded(self) -> None:
        # codex #399 r2: a constant/tied cross-section yields NaN IC —
        # it must be dropped AND counted, never written into the
        # series the FWER consumes.
        dates, cols, _ = self._frames()
        factor = pd.DataFrame(
            [[1, 2, 3, 4]] * 6, index=dates, columns=cols, dtype=float)
        factor.iloc[0] = 7.0                      # constant day
        fwd = pd.DataFrame(
            [[0.1, 0.2, 0.3, 0.4]] * 6, index=dates, columns=cols)
        ic, dropped = ev.daily_rank_ic(factor, fwd, min_names=4)
        self.assertEqual(1, dropped)
        self.assertTrue(np.isfinite(ic.values).all())

    def test_coverage_counts_only_eligible_ic_days(self) -> None:
        # codex #399 r2: baseline days OUTSIDE ic.index must not
        # compensate for missing eligible ones.
        plan = _plan()
        ic_dates = pd.date_range("2023-01-02", periods=10, freq="B")
        other_dates = pd.date_range("2024-06-03", periods=10, freq="B")
        ic = pd.Series(0.01, index=ic_dates)
        # 100% volume of orth days, but only 30% on eligible dates.
        orth = pd.Series(
            0.1, index=ic_dates[:3].append(other_dates[:7]))
        with self.assertRaises(ev.PVEvalError):
            ev.build_artifact(plan, "c1", "$close", ic, 0, orth, "ab")

    def test_partial_baseline_coverage_fails_loud(self) -> None:
        # codex #399 r1: the orthogonality gate is the ONLY guard
        # against non-incremental promotion — a sliver of baseline
        # overlap must not adjudicate the band.
        plan = _plan()
        dates = pd.date_range("2023-01-02", periods=10, freq="B")
        ic = pd.Series(0.01, index=dates)
        orth = pd.Series(0.1, index=dates[:3])   # 30% coverage
        with self.assertRaises(ev.PVEvalError) as ctx:
            ev.build_artifact(plan, "c1", "$close", ic, 0, orth, "ab")
        self.assertIn("partial overlap", str(ctx.exception))
        # Full coverage builds fine.
        art = ev.build_artifact(
            plan, "c1", "$close", ic, 0,
            pd.Series(0.1, index=dates), "ab")
        self.assertIs(True, art["orth_within_hard_band"])


class CandidateIdTests(unittest.TestCase):
    def test_unsafe_ids_refused_safe_pass(self) -> None:
        # codex #399 r11: candidate_id becomes a filename under the
        # sealed batch dir — separators/absolute prefixes would write
        # outside --out-dir and make the batch unadjudicable.
        for bad in ("../escape", "/tmp/escape", "a/b", "a\\b",
                    ".hidden", "", "a b"):
            with self.assertRaises(ev.PVEvalError, msg=repr(bad)):
                ev.check_candidate_id(bad)
        for ok in ("c1", "pv_batch1-c07", "X"):
            ev.check_candidate_id(ok)

    def test_preflight_refuses_before_any_write(self) -> None:
        # codex #399 r12: a bad id AFTER a valid one must refuse the
        # whole manifest up front — mid-loop discovery would leave a
        # dirty, unadjudicable batch (artifacts without a stamp).
        with self.assertRaises(ev.PVEvalError):
            ev.preflight_candidates(
                [{"candidate_id": "ok1"},
                 {"candidate_id": "../escape"}])
        with self.assertRaises(ev.PVEvalError) as ctx:
            ev.preflight_candidates(
                [{"candidate_id": "dup"}, {"candidate_id": "dup"}])
        self.assertIn("more than once", str(ctx.exception))
        ev.preflight_candidates(
            [{"candidate_id": "a"}, {"candidate_id": "b"}])


class BaselineProvenanceTests(unittest.TestCase):
    # codex #399 r10: the frozen plan requires the LEDGERED Alpha158
    # walk-forward baseline — an ad hoc/stale parquet would key the
    # only incremental gate to the wrong baseline. Refusal predicates:
    # missing sidecar / wrong model / stale sha / missing provenance
    # fields; a fully bound sidecar passes.

    def _setup(self, tmp: str) -> tuple[Path, str, dict]:
        import hashlib
        path = Path(tmp) / "baseline_preds.parquet"
        path.write_bytes(b"parquet-bytes")
        sha = hashlib.sha256(b"parquet-bytes").hexdigest()
        good = {"model": _plan()["fitness"]["baseline"]["model"],
                "file_sha256": sha,
                "run_config_sha256": "cafe" * 16,
                "source_git": "abc1234"}
        return path, sha, good

    def test_refusal_predicates_and_pass(self) -> None:
        import json as jsonlib
        import tempfile
        plan = _plan()
        with tempfile.TemporaryDirectory() as tmp:
            path, sha, good = self._setup(tmp)
            sidecar = path.with_name(path.name + ".provenance.json")
            with self.assertRaises(ev.PVEvalError) as ctx:
                ev.check_baseline_provenance(plan, path, sha)
            self.assertIn("sidecar", str(ctx.exception))
            for label, mutate in (
                    ("wrong model", {"model": "adhoc_lgb"}),
                    ("stale sha", {"file_sha256": "0" * 64}),
                    ("missing run_config", {"run_config_sha256": ""}),
                    ("missing source_git", {"source_git": None})):
                prov = dict(good)
                prov.update(mutate)
                sidecar.write_text(jsonlib.dumps(prov),
                                   encoding="utf-8")
                with self.assertRaises(ev.PVEvalError, msg=label):
                    ev.check_baseline_provenance(plan, path, sha)
            sidecar.write_text(jsonlib.dumps(good), encoding="utf-8")
            out = ev.check_baseline_provenance(plan, path, sha)
            self.assertEqual(good["model"], out["model"])


class FwerTests(unittest.TestCase):
    @staticmethod
    def _artifact(cid: str, series: np.ndarray, *,
                  within_band: bool = True,
                  protocol: str = "pv_incremental_v1") -> dict:
        stats = ev.summarize(pd.Series(series))
        dates = pd.date_range("2023-01-02", periods=len(series),
                              freq="B")
        return {"protocol_id": protocol, "candidate_id": cid,
                "expression": f"cs_rank($close_{cid})",
                "window": {"start": "2023-01-01", "end": "2024-12-31"},
                "daily_ic": [{"date": str(d)[:10], "ic": float(v)}
                             for d, v in zip(dates, series,
                                             strict=True)],
                "ic_mean": stats["ic_mean"], "t_stat": stats["t_stat"],
                "orth_mean_abs_rho": 0.1 if within_band else 0.9,
                "orth_within_hard_band": within_band}

    def _plan(self) -> dict:
        plan = _plan()
        plan = dict(plan)
        plan["fwer"] = dict(plan["fwer"])
        plan["fwer"]["n_boot"] = 200   # test-speed; mechanism identical
        return plan

    def test_sparse_trial_excluded_and_reported(self) -> None:
        rng = np.random.default_rng(1)
        arts = [self._artifact("dense", rng.normal(0, 0.05, 300)),
                self._artifact("sparse", rng.normal(0.5, 0.05, 10))]
        out = fw.adjudicate(self._plan(), arts, seed=7)
        self.assertEqual(["sparse"], out["sparse_excluded"])
        self.assertEqual(1, out["family_size"])
        # The sparse trial's numbers are still reported honestly.
        self.assertTrue(any(r["candidate_id"] == "sparse"
                            for r in out["trials"]))

    def test_clean_negative_when_family_all_below(self) -> None:
        rng = np.random.default_rng(2)
        arts = [self._artifact(f"c{i}", rng.normal(0, 0.05, 300))
                for i in range(3)]
        out = fw.adjudicate(self._plan(), arts, seed=7)
        self.assertEqual("clean_negative", out["verdict"])
        self.assertEqual([], out["survivors"])

    def test_survivor_requires_orthogonality_too(self) -> None:
        rng = np.random.default_rng(3)
        strong = rng.normal(0.06, 0.05, 480)   # t >> floor
        arts = [self._artifact("hi_orth", strong, within_band=False),
                self._artifact("noise", rng.normal(0, 0.05, 480))]
        out = fw.adjudicate(self._plan(), arts, seed=7)
        # Standalone significance is NOT a pass (incremental criterion)
        # — and it is NOT clean_negative either (codex #399 r1): the
        # significant-but-non-incremental state routes to the operator
        # instead of laundering into reject_iff.
        self.assertNotIn("hi_orth", out["survivors"])
        self.assertEqual("significant_non_incremental", out["verdict"])
        self.assertIn("hi_orth", out["significant_non_incremental"])
        arts2 = [self._artifact("ok", strong, within_band=True),
                 self._artifact("noise", rng.normal(0, 0.05, 480))]
        out2 = fw.adjudicate(self._plan(), arts2, seed=7)
        self.assertIn("ok", out2["survivors"])
        self.assertEqual("survivors", out2["verdict"])

    def test_only_sparse_gives_no_verdict(self) -> None:
        arts = [self._artifact(
            "s", np.array([0.5, 0.42, 0.58, 0.47, 0.53]))]
        out = fw.adjudicate(self._plan(), arts, seed=7)
        self.assertEqual("no_verdict", out["verdict"])

    def test_foreign_protocol_refused(self) -> None:
        arts = [self._artifact("x", np.zeros(300),
                               protocol="quality_profitability_v1")]
        with self.assertRaises(fw.PVFwerError):
            fw.adjudicate(self._plan(), arts, seed=7)

    @staticmethod
    def _series(values: np.ndarray) -> pd.Series:
        return pd.Series(
            values,
            index=pd.date_range("2023-01-02", periods=len(values),
                                freq="B"))

    def test_bootstrap_bar_is_deterministic_under_seed(self) -> None:
        rng = np.random.default_rng(4)
        fam = {"a": self._series(rng.normal(0, 1, 260)),
               "b": self._series(rng.normal(0, 1, 260))}
        b1 = fw.block_bootstrap_bar(fam, n_boot=100, block_len=21,
                                    quantile=0.95, seed=42)
        b2 = fw.block_bootstrap_bar(fam, n_boot=100, block_len=21,
                                    quantile=0.95, seed=42)
        self.assertEqual(b1, b2)
        self.assertGreater(b1, 0.0)

    def test_joint_draw_preserves_family_comovement(self) -> None:
        # codex #399 r2: identical block positions across members —
        # a clone family's max-|t| null equals the single-member null
        # exactly; independent draws would inflate it.
        rng = np.random.default_rng(5)
        base = rng.normal(0, 1, 260)
        one = {"a": self._series(base)}
        clones = {"a": self._series(base),
                  "b": self._series(base.copy())}
        b_one = fw.block_bootstrap_bar(one, n_boot=200, block_len=21,
                                       quantile=0.95, seed=42)
        b_two = fw.block_bootstrap_bar(clones, n_boot=200,
                                       block_len=21, quantile=0.95,
                                       seed=42)
        self.assertAlmostEqual(b_one, b_two, places=12)

    def test_inconsistent_scalar_t_refused(self) -> None:
        # codex #399 r3: the daily series is canonical — a stale or
        # hand-edited scalar t must never clear the threshold.
        art = self._artifact("stale", np.random.default_rng(6).normal(
            0, 0.05, 300))
        art["t_stat"] = 9.99
        with self.assertRaises(fw.PVFwerError) as ctx:
            fw.adjudicate(self._plan(), [art], seed=7)
        self.assertIn("inconsistent", str(ctx.exception))

    @staticmethod
    def _manifest(*arts: dict) -> list[dict]:
        return [{"candidate_id": a["candidate_id"],
                 "expression": a["expression"]} for a in arts]

    def test_family_manifest_binding(self) -> None:
        # codex #399 r3/r6: the registered batch manifest defines the
        # family — ids AND expressions; missing/extra/duplicate/
        # expression-drift all refuse.
        rng = np.random.default_rng(8)
        a = self._artifact("a", rng.normal(0, 0.05, 300))
        b = self._artifact("b", rng.normal(0, 0.05, 300))
        fw.check_family_manifest([a, b], self._manifest(a, b))  # OK
        reregistered = [{"candidate_id": "a",
                         "expression": "cs_rank($volume)"},
                        self._manifest(b)[0]]
        for label, arts, manifest in (
                ("missing", [a], self._manifest(a, b)),
                ("extra", [a, b], self._manifest(a)),
                ("duplicate", [a, a], self._manifest(a)),
                ("expression drift", [a, b], reregistered)):
            with self.assertRaises(fw.PVFwerError, msg=label):
                fw.check_family_manifest(arts, manifest)

    def test_out_of_domain_rho_refused(self) -> None:
        # codex #399 r5: mean ABSOLUTE rho lives in [0,1] — negative/
        # bool/non-finite scalars are corrupt, never "within band".
        for label, rho in (("negative", -0.5), ("bool", False),
                           ("nan", float("nan")), ("gt1", 1.5)):
            art = self._artifact(
                "bad_rho", np.random.default_rng(10).normal(0, 0.05, 300))
            art["orth_mean_abs_rho"] = rho
            art["orth_within_hard_band"] = True
            with self.assertRaises(fw.PVFwerError, msg=label) as ctx:
                fw.adjudicate(self._plan(), [art], seed=7)
            self.assertIn("domain", str(ctx.exception))

    def test_run_identity_binding(self) -> None:
        # codex #399 r5: the family binds to ONE completed run.
        a = self._artifact("a", np.zeros(300) + 0.01)
        b = self._artifact("b", np.zeros(300) + 0.01)
        a["run_id"] = b["run_id"] = "r1"
        good = {"protocol_id": "pv_incremental_v1", "run_id": "r1",
                "candidate_ids": ["a", "b"]}
        fw.check_run_identity([a, b], good)          # sealed: OK
        for label, arts, comp in (
                ("mixed run", [a, dict(b, run_id="r0")], good),
                ("partial stamp", [a, b],
                 dict(good, candidate_ids=["a"])),
                ("foreign protocol", [a, b],
                 dict(good, protocol_id="other")),
                ("no run_id", [a, b],
                 {"protocol_id": "pv_incremental_v1",
                  "candidate_ids": ["a", "b"]})):
            with self.assertRaises(fw.PVFwerError, msg=label):
                fw.check_run_identity(arts, comp)

    def test_stale_orthogonality_boolean_refused(self) -> None:
        # codex #399 r4: the boolean is DERIVED from rho vs the frozen
        # band — a stale True over an out-of-band rho refuses.
        art = self._artifact("stale_orth", np.random.default_rng(9)
                             .normal(0, 0.05, 300))
        art["orth_mean_abs_rho"] = 0.9      # > band 0.5
        art["orth_within_hard_band"] = True  # stale claim
        with self.assertRaises(fw.PVFwerError) as ctx:
            fw.adjudicate(self._plan(), [art], seed=7)
        self.assertIn("inconsistent", str(ctx.exception))

    def test_duplicate_manifest_ids_refused(self) -> None:
        # codex #399 r4: a manifest repeating an id would let one
        # artifact satisfy two registered trials via set collapse.
        a = self._artifact("a", np.zeros(300) + 0.01)
        with self.assertRaises(fw.PVFwerError) as ctx:
            fw.check_family_manifest(
                [a], self._manifest(a) + self._manifest(a))
        self.assertIn("repeats", str(ctx.exception))

    def test_pv_terminal_whitelist(self) -> None:
        # codex #399 r7: PV-DP-1 enforcement — a CSF/PURE root over a
        # non-PV terminal ($pe: valuation, explicitly excluded by the
        # freeze) refuses as a registration error.
        fields = ["open", "high", "low", "close", "volume", "money",
                  "turnover_rate"]
        ev.check_pv_terminals(
            "cs_rank(ts_pctchange($close, 20))", fields)   # OK
        with self.assertRaises(ev.PVEvalError) as ctx:
            ev.check_pv_terminals("cs_rank(ts_rank($pe, 20))", fields)
        self.assertIn("$pe", str(ctx.exception))

    def test_off_window_daily_ic_refused(self) -> None:
        # codex #399 r8: rows from the blinded holdout or forbidden
        # 2026 period must never be bootstrapped as OOS — and a
        # non-frozen window declaration refuses outright.
        art = self._artifact("leak", np.zeros(300) + 0.01)
        art["daily_ic"][0]["date"] = "2025-03-03"    # holdout row
        with self.assertRaises(fw.PVFwerError) as ctx:
            fw.adjudicate(self._plan(), [art], seed=7)
        self.assertIn("outside the frozen OOS window",
                      str(ctx.exception))
        art2 = self._artifact("wrongwin", np.zeros(300) + 0.01)
        art2["window"] = {"start": "2023-01-01", "end": "2025-12-31"}
        with self.assertRaises(fw.PVFwerError):
            fw.adjudicate(self._plan(), [art2], seed=7)

    def test_bootstrap_statistic_is_one_sided(self) -> None:
        # codex #399 r10: the frozen statistic is the SIGNED max-t
        # (Gate-4A-sourced) — survival is one-sided, so |t| would let
        # negative excursions inflate the positive bar. Discriminator:
        # for a single-member demeaned null the signed-t draw
        # distribution is roughly symmetric, so a LOW quantile of the
        # max is negative — an abs-statistic can never go below zero.
        rng = np.random.default_rng(11)
        series = pd.Series(
            rng.normal(0, 0.05, 300),
            index=pd.date_range("2023-01-02", periods=300, freq="B"))
        bar = fw.block_bootstrap_bar(
            {"solo": series}, n_boot=400, block_len=21,
            quantile=0.10, seed=7)
        self.assertLess(bar, 0.0)

    def test_bootstrap_redraw_budget_fails_loud(self) -> None:
        # codex #399 r10 (Gate-4A redraw semantics): a member whose
        # draws are always degenerate must exhaust the redraw budget
        # and refuse — never silently shrink the family max.
        rng = np.random.default_rng(12)
        dense = pd.Series(
            rng.normal(0, 0.05, 300),
            index=pd.date_range("2023-01-02", periods=300, freq="B"))
        lone = pd.Series(
            [0.5], index=pd.DatetimeIndex(["2023-06-01"]))
        with self.assertRaises(fw.PVFwerError) as ctx:
            fw.block_bootstrap_bar(
                {"dense": dense, "lone": lone}, n_boot=10,
                block_len=21, quantile=0.95, seed=7)
        self.assertIn("redraw budget", str(ctx.exception))

    def test_zero_variance_ic_series_refused(self) -> None:
        # codex #399 r9: a zero-variance daily series recomputes an
        # UNDEFINED observed t; the finite-both mismatch guard skips
        # it, so without this refusal the trial would join the family
        # on n_days alone and launder into a clean-negative verdict.
        art = self._artifact("flat", np.zeros(300) + 0.01)
        with self.assertRaises(fw.PVFwerError) as ctx:
            fw.adjudicate(self._plan(), [art], seed=7)
        self.assertIn("non-finite observed t", str(ctx.exception))

    def test_degenerate_sparse_trial_reaches_sparse_bucket(self) -> None:
        # codex #399 r12: the r9 refusal is scoped to FAMILY-ELIGIBLE
        # series — a sparse trial leaves the family per the frozen
        # per_trial_min_n_days and reports separately (undefined t as
        # null); it must never abort the whole batch.
        rng = np.random.default_rng(21)
        arts = [self._artifact("dense", rng.normal(0, 0.05, 300)),
                self._artifact("flat5", np.full(5, 0.01))]
        out = fw.adjudicate(self._plan(), arts, seed=7)
        self.assertEqual(["flat5"], out["sparse_excluded"])
        flat_row = next(r for r in out["trials"]
                        if r["candidate_id"] == "flat5")
        self.assertIsNone(flat_row["t_stat"])
        # Sparse-only degenerate batch: no_verdict, not an abort.
        only = fw.adjudicate(
            self._plan(), [self._artifact("flat5", np.full(5, 0.01))],
            seed=7)
        self.assertEqual("no_verdict", only["verdict"])

    def test_duplicate_daily_ic_dates_refused(self) -> None:
        # codex #399 r7: repeated dates would silently collapse the
        # canonical series to a different one.
        art = self._artifact("dup", np.zeros(300) + 0.01)
        art["daily_ic"][1]["date"] = art["daily_ic"][0]["date"]
        with self.assertRaises(fw.PVFwerError) as ctx:
            fw.adjudicate(self._plan(), [art], seed=7)
        self.assertIn("repeats daily_ic", str(ctx.exception))

    def test_candidate_root_type_contract(self) -> None:
        # codex #399 r6: the refusal predicates — a raw price-level
        # root is NOT ExprType('CSF','PURE'); tainted input into
        # cs_* refuses at PARSE time; a properly neutralized
        # cross-sectional root passes.
        from src.factor_mining.expression import parse_expression
        from src.factor_mining.grammar import ExprType, GrammarError

        csf = ExprType("CSF", "PURE")
        self.assertNotEqual(csf, parse_expression("$close").output_type)
        with self.assertRaises(GrammarError):
            parse_expression("cs_rank($close)")   # taint gate
        self.assertEqual(
            csf,
            parse_expression(
                "cs_rank(ts_pctchange($close, 20))").output_type)

    def test_non_finite_ic_artifact_refused(self) -> None:
        art = self._artifact("bad", np.zeros(300))
        art["daily_ic"][5]["ic"] = float("nan")
        with self.assertRaises(fw.PVFwerError) as ctx:
            fw.adjudicate(self._plan(), [art], seed=7)
        self.assertIn("non-finite", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
