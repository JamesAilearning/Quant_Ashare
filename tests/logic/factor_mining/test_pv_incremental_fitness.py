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


class FrozenIcTermTests(unittest.TestCase):
    """codex #401 r1: the frozen breeding criterion must be CONSUMED
    by the fitness function, not merely pinned in YAML — otherwise
    candidates are selected by a metric other than the registered one."""

    @staticmethod
    def _result(**kw):
        from src.factor_mining.evaluator import EvaluationResult
        base = dict(
            factor_values=pd.DataFrame(
                {"a": [1.0, 2.0], "b": [3.0, 4.0]},
                index=pd.DatetimeIndex(["2023-01-02", "2023-01-03"])),
            ic_mean=0.9, ic_std=0.1, ir=5.0, rank_ic_mean=0.04,
            rank_ic_std=0.1, rank_ir=0.4, turnover_daily=0.5,
            coverage=1.0, n_obs_per_day_min=2)
        base.update(kw)
        return EvaluationResult(**base)

    def test_default_stays_v1_composite(self) -> None:
        self.assertEqual("v1_composite", FitnessConfig().ic_term)

    def test_abs_rank_ic_is_rankic_minus_parsimony_only(self) -> None:
        from src.factor_mining.fitness import compute_fitness
        cfg = FitnessConfig(ic_term="abs_rank_ic", w_complexity=0.002)
        score = compute_fitness(
            self._result(), expr_size=5, novelty_penalty=0.8, config=cfg)
        # |rank_ic| - 0.002*5; the v1 IR / turnover-cost / novelty
        # terms must NOT participate (their values above are large and
        # would visibly move the score).
        self.assertAlmostEqual(0.04 - 0.01, score)

    def test_abs_rank_ic_uses_absolute_value(self) -> None:
        # Decision ③: direction is a degree of freedom of the
        # expression — a sign-flipped factor is the same factor.
        from src.factor_mining.fitness import compute_fitness
        cfg = FitnessConfig(ic_term="abs_rank_ic", w_complexity=0.0)
        pos = compute_fitness(self._result(rank_ic_mean=0.04),
                              expr_size=3, novelty_penalty=0.0, config=cfg)
        neg = compute_fitness(self._result(rank_ic_mean=-0.04),
                              expr_size=3, novelty_penalty=0.0, config=cfg)
        self.assertAlmostEqual(pos, neg)

    def test_abs_rank_ic_subtracts_orthogonality(self) -> None:
        from src.factor_mining.fitness import compute_fitness
        cfg = FitnessConfig(ic_term="abs_rank_ic", w_complexity=0.0,
                            w_orthogonality=2.0, orthogonality_band=0.30)
        score = compute_fitness(
            self._result(), expr_size=3, novelty_penalty=0.0, config=cfg,
            baseline_mean_abs_rho=0.50)
        self.assertAlmostEqual(0.04 - 0.4, score)

    def test_unknown_ic_term_refuses(self) -> None:
        from src.factor_mining.fitness import compute_fitness
        with self.assertRaises(ValueError):
            compute_fitness(self._result(), expr_size=1,
                            novelty_penalty=0.0,
                            config=FitnessConfig(ic_term="typo"))

    def test_breeding_metric_drops_the_frozen_thin_days(self) -> None:
        # codex #401 r6: the generic evaluator only drops days with
        # <3 names, while the frozen metric (and the OOS evaluator)
        # drop days below min_names_per_day. Without threading the
        # floor, thin-day ICs steer GP selection and then vanish at
        # adjudication — the breeding metric is not the registered one.
        from src.factor_mining.evaluator import _ic_per_day
        dates = pd.date_range("2023-01-02", periods=2, freq="B")
        # Day 0 has 5 names, day 1 has 3 (thin under a floor of 5).
        factor = pd.DataFrame(
            [[1.0, 2.0, 3.0, 4.0, 5.0], [1.0, 2.0, 3.0, np.nan, np.nan]],
            index=dates, columns=list("abcde"))
        fwd = pd.DataFrame(
            [[5.0, 4.0, 3.0, 2.0, 1.0], [1.0, 2.0, 3.0, np.nan, np.nan]],
            index=dates, columns=list("abcde"))
        both = _ic_per_day(factor, fwd, method="rank")
        self.assertEqual(2, len(both))          # legacy: both days
        floored = _ic_per_day(factor, fwd, method="rank",
                              min_names_per_day=5)
        self.assertEqual(1, len(floored))       # thin day dropped
        self.assertEqual(dates[0], floored.index[0])

    def test_engine_forwards_floor_only_when_set(self) -> None:
        # Legacy call shape must stay literally unchanged (no new
        # keyword) when no campaign floor is configured.
        import src.factor_mining.gp_engine as ge
        from src.factor_mining.expression import parse_expression
        seen: list[dict] = []

        def recorder(expr, panel, fwd, **kw):
            seen.append(kw)
            raise RuntimeError("stop after recording")

        original = ge.evaluate_factor
        ge.evaluate_factor = recorder
        try:
            expr = parse_expression("cs_rank(ts_pctchange($close, 20))")
            panel = {"$close": pd.DataFrame(
                1.0, index=pd.date_range("2023-01-02", periods=3,
                                         freq="B"),
                columns=["a", "b", "c"])}
            fwd = panel["$close"]
            ge.GPEngine(ge.GPConfig(seed=1),
                        FitnessConfig()).evaluate_individual(
                expr, panel, fwd)
            self.assertNotIn("min_names_per_day", seen[-1])
            ge.GPEngine(ge.GPConfig(seed=1),
                        FitnessConfig(min_names_per_day=300)
                        ).evaluate_individual(expr, panel, fwd)
            self.assertEqual(300, seen[-1]["min_names_per_day"])
        finally:
            ge.evaluate_factor = original

    def test_frozen_min_names_is_expressible(self) -> None:
        # The plan's metric floor must map onto a FitnessConfig field
        # the engine passes down — not exist only in YAML.
        plan_floor = _plan()["metric"]["min_names_per_day"]
        self.assertEqual(300, plan_floor)
        cfg = FitnessConfig(ic_term="abs_rank_ic",
                            min_names_per_day=plan_floor)
        self.assertEqual(300, cfg.min_names_per_day)
        self.assertEqual(0, FitnessConfig().min_names_per_day)

    def test_plan_ic_term_is_a_recognised_mode(self) -> None:
        # The YAML value and the code's vocabulary must agree — a
        # frozen term the engine cannot consume is the whole defect.
        from src.factor_mining.fitness import _IC_TERMS
        self.assertIn(_plan()["fitness"]["ic_term"], _IC_TERMS)


class BaselinePanelBindingTests(unittest.TestCase):
    """codex #401 r1: an instrument-namespace or date mismatch is a
    CONFIG error that must refuse before scoring — not an 'uncovered'
    expression that silently disables the incremental criterion."""

    @staticmethod
    def _panel(cols, dates):
        return {"$close": pd.DataFrame(
            1.0, index=pd.DatetimeIndex(dates), columns=list(cols))}

    def test_two_shared_names_refuse_matching_scorer_floor(self) -> None:
        # codex #401 r4: the setup guard's floor must equal the
        # scorer's. With exactly two shared names the old `< 2` guard
        # passed and then the scorer's `< 3` skipped every single day —
        # the whole run recorded as uncovered, penalty zeroed.
        from src.factor_mining.gp_engine import (
            _MIN_ORTHOGONALITY_CROSS_SECTION,
            _assert_baseline_meets_panel,
        )
        self.assertEqual(3, _MIN_ORTHOGONALITY_CROSS_SECTION)
        dates = pd.date_range("2023-01-02", periods=3, freq="B")
        panel = self._panel(["a", "b", "c"], dates)
        baseline = pd.DataFrame(0.5, index=dates, columns=["a", "b"])
        with self.assertRaises(ValueError) as ctx:
            _assert_baseline_meets_panel(baseline, panel)
        self.assertIn("instrument", str(ctx.exception))
        # Three shared names is the boundary that passes.
        _assert_baseline_meets_panel(
            pd.DataFrame(0.5, index=dates, columns=["a", "b", "c"]),
            panel)

    def test_campaign_floor_applies_to_orthogonality_days(self) -> None:
        # codex #401 r7: the r6 fix threaded the frozen floor into the
        # rank-IC path but the orthogonality penalty still admitted
        # 3-name days, while the OOS evaluator's orthogonality_series
        # skips days below min_names_per_day — the breeding penalty
        # would again be measured over days adjudication discards.
        from src.factor_mining.gp_engine import (
            GPConfig,
            GPEngine,
            _orthogonality_floor,
        )
        self.assertEqual(3, _orthogonality_floor(FitnessConfig()))
        self.assertEqual(300, _orthogonality_floor(
            FitnessConfig(min_names_per_day=300)))
        dates = pd.date_range("2023-01-02", periods=2, freq="B")
        cols = [f"n{i}" for i in range(6)]
        rng = np.random.default_rng(31)
        factor = pd.DataFrame(rng.normal(size=(2, 6)), index=dates,
                              columns=cols)
        baseline = pd.DataFrame(rng.normal(size=(2, 6)), index=dates,
                                columns=cols)
        # Legacy floor (3): both 6-name days score.
        eng = GPEngine(GPConfig(seed=1),
                       FitnessConfig(w_orthogonality=2.0,
                                     orthogonality_band=0.30))
        eng._baseline = baseline
        self.assertTrue(np.isfinite(eng._baseline_orthogonality(factor)))
        # Campaign floor of 10 > 6 names: no day qualifies, so the
        # expression is UNCOVERED (no penalty) rather than penalised
        # on days the OOS gate would drop.
        eng2 = GPEngine(GPConfig(seed=1),
                        FitnessConfig(w_orthogonality=2.0,
                                      orthogonality_band=0.30,
                                      min_names_per_day=10))
        eng2._baseline = baseline
        self.assertTrue(np.isnan(eng2._baseline_orthogonality(factor)))
        self.assertEqual(1, eng2._orthogonality_uncovered)

    def test_penalty_restricted_to_eligible_ic_days(self) -> None:
        # codex #401 r8: the OOS evaluator adjudicates orthogonality on
        # ic.index only (`orth.reindex(ic.index).dropna()`), so days
        # where factor and baseline both exist but no forward return
        # does (lag tail / PIT gaps) must not steer breeding either.
        from src.factor_mining.gp_engine import GPConfig, GPEngine
        dates = pd.date_range("2023-01-02", periods=4, freq="B")
        cols = [f"n{i}" for i in range(6)]
        rng = np.random.default_rng(41)
        factor = pd.DataFrame(rng.normal(size=(4, 6)), index=dates,
                              columns=cols)
        # Baseline perfectly tracks the factor on the two eligible
        # days and is scrambled on the two ineligible (lag-tail) days.
        baseline = factor.copy()
        baseline.loc[dates[2:]] = rng.normal(size=(2, 6))
        eng = GPEngine(GPConfig(seed=1),
                       FitnessConfig(w_orthogonality=2.0,
                                     orthogonality_band=0.30))
        eng._baseline = baseline
        # All four days: the scrambled tail drags the mean |rho| down.
        all_days = eng._baseline_orthogonality(factor)
        # Restricted to the eligible IC axis: only the tracking days
        # count, so rho is 1.0 — the days adjudication will judge.
        eligible = eng._baseline_orthogonality(factor, dates[:2])
        self.assertAlmostEqual(1.0, eligible)
        self.assertLess(all_days, eligible)

    def test_evaluator_exposes_eligible_ic_dates(self) -> None:
        # The axis must come from the evaluator itself (the days that
        # actually produced a finite rank-IC), not be re-derived.
        from src.factor_mining.evaluator import evaluate_factor
        from src.factor_mining.expression import parse_expression
        dates = pd.date_range("2023-01-02", periods=10, freq="B")
        cols = [f"n{i}" for i in range(5)]
        rng = np.random.default_rng(42)
        close = pd.DataFrame(
            rng.normal(100, 5, size=(10, 5)), index=dates, columns=cols)
        fwd = pd.DataFrame(rng.normal(size=(10, 5)), index=dates,
                           columns=cols)
        fwd.loc[dates[-1]] = np.nan          # lag tail: no return
        result = evaluate_factor(
            parse_expression("cs_rank(ts_pctchange($close, 5))"),
            {"$close": close}, fwd, method="normal")
        self.assertIsNotNone(result.ic_dates)
        self.assertNotIn(dates[-1], result.ic_dates)
        # Legacy default construction still works (additive field).
        from src.factor_mining.evaluator import EvaluationResult
        legacy = EvaluationResult(
            factor_values=close, ic_mean=0.0, ic_std=0.1, ir=0.0,
            rank_ic_mean=0.0, rank_ic_std=0.1, rank_ir=0.0,
            turnover_daily=0.0, coverage=1.0, n_obs_per_day_min=5)
        self.assertIsNone(legacy.ic_dates)

    def test_setup_guard_uses_campaign_floor(self) -> None:
        from src.factor_mining.gp_engine import _assert_baseline_meets_panel
        dates = pd.date_range("2023-01-02", periods=3, freq="B")
        cols = [f"n{i}" for i in range(5)]
        panel = self._panel(cols, dates)
        baseline = pd.DataFrame(0.5, index=dates, columns=cols)
        _assert_baseline_meets_panel(baseline, panel)          # floor 3
        with self.assertRaises(ValueError) as ctx:
            _assert_baseline_meets_panel(baseline, panel, floor=300)
        self.assertIn("300", str(ctx.exception))

    def test_guard_counts_jointly_finite_cells(self) -> None:
        # codex #401 r12: counting non-null baseline cells alone lets a
        # baseline whose populated cells are DISJOINT from the panel's
        # (or which carries infinities) clear the floor while the
        # scorer still finds no scoreable cross-section.
        from src.factor_mining.gp_engine import _assert_baseline_meets_panel
        dates = pd.date_range("2023-01-02", periods=2, freq="B")
        cols = [f"n{i}" for i in range(6)]
        panel_frame = pd.DataFrame(np.nan, index=dates, columns=cols)
        panel_frame.iloc[:, :3] = 1.0          # panel populates n0..n2
        baseline = pd.DataFrame(np.nan, index=dates, columns=cols)
        baseline.iloc[:, 3:] = 0.5             # baseline populates n3..n5
        with self.assertRaises(ValueError) as ctx:
            _assert_baseline_meets_panel(baseline, {"$close": panel_frame})
        self.assertIn("jointly finite", str(ctx.exception))
        # Infinities do not count as observations either.
        inf_baseline = pd.DataFrame(np.inf, index=dates, columns=cols)
        with self.assertRaises(ValueError):
            _assert_baseline_meets_panel(
                inf_baseline, {"$close": pd.DataFrame(1.0, index=dates,
                                                      columns=cols)})

    def test_namespace_mismatch_refuses(self) -> None:
        from src.factor_mining.gp_engine import _assert_baseline_meets_panel
        dates = pd.date_range("2023-01-02", periods=3, freq="B")
        panel = self._panel(["SH600000", "SZ000001"], dates)
        baseline = pd.DataFrame(
            0.5, index=dates, columns=["600000.SH", "000001.SZ"])
        with self.assertRaises(ValueError) as ctx:
            _assert_baseline_meets_panel(baseline, panel)
        self.assertIn("instrument", str(ctx.exception))

    def test_zero_date_overlap_refuses(self) -> None:
        from src.factor_mining.gp_engine import _assert_baseline_meets_panel
        panel = self._panel(
            ["a", "b", "c"],
            pd.date_range("2018-01-02", periods=3, freq="B"))
        baseline = pd.DataFrame(
            0.5, index=pd.date_range("2023-01-02", periods=3, freq="B"),
            columns=["a", "b", "c"])
        with self.assertRaises(ValueError) as ctx:
            _assert_baseline_meets_panel(baseline, panel)
        self.assertIn("overlap", str(ctx.exception))

    def test_partial_overlap_is_allowed(self) -> None:
        # The accepted geometry (decision A): the baseline starts
        # later than the mining window — allowed and disclosed.
        from src.factor_mining.gp_engine import _assert_baseline_meets_panel
        panel = self._panel(
            ["a", "b", "c"],
            pd.date_range("2018-01-02", periods=500, freq="B"))
        baseline = pd.DataFrame(
            0.5, index=pd.date_range("2019-06-03", periods=100, freq="B"),
            columns=["a", "b", "c"])
        _assert_baseline_meets_panel(baseline, panel)


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


class TerminalWhitelistTests(unittest.TestCase):
    """codex #401 r9: the frozen seven-field protocol must constrain
    the GENERATOR too — a panel whitelist alone would only make every
    forbidden-terminal individual crash at evaluation."""

    _SEVEN = frozenset({"$open", "$high", "$low", "$close", "$volume",
                        "$money", "$turnover_rate"})
    _FORBIDDEN = ("$pe", "$pb", "$ps", "$circ_mv", "$total_mv")

    def test_generator_never_samples_excluded_terminals(self) -> None:
        from random import Random

        from src.factor_mining.grammar import ExprType, random_expression
        rng = Random(7)
        for _ in range(200):
            expr = str(random_expression(
                ExprType("CSF", "PURE"), 6, 2, rng,
                allowed_terminals=self._SEVEN))
            for bad in self._FORBIDDEN:
                self.assertNotIn(bad, expr)

    def test_generator_unrestricted_by_default(self) -> None:
        # Legacy sampling must still reach the fundamentals.
        from random import Random

        from src.factor_mining.grammar import ExprType, random_expression
        rng = Random(3)
        seen = " ".join(
            str(random_expression(ExprType("CSF", "PURE"), 6, 2, rng))
            for _ in range(200))
        self.assertTrue(any(bad in seen for bad in self._FORBIDDEN))

    def test_empty_intersection_refuses(self) -> None:
        from random import Random

        from src.factor_mining.grammar import (
            ExprType,
            GrammarError,
            random_expression,
        )
        with self.assertRaises(GrammarError):
            random_expression(ExprType("CSF", "PURE"), 6, 2, Random(1),
                              allowed_terminals=frozenset({"$nonexistent"}))

    def test_point_mutation_respects_whitelist(self) -> None:
        from src.factor_mining.gp_engine import GPConfig, GPEngine
        from src.factor_mining.grammar import ExprType
        eng = GPEngine(GPConfig(seed=5), FitnessConfig())
        eng._allowed_terminals = self._SEVEN
        for _ in range(50):
            term = eng._random_terminal_same_type(
                ExprType("FEATURE", "PURE"), exclude="$volume")
            self.assertIn(term.name, self._SEVEN)

    def test_unusable_whitelist_fails_loud_not_empty_pool(self) -> None:
        # codex #401 r10: the direct generator refuses, but the engine
        # swallowed each GrammarError until the retry budget expired
        # and returned an empty pool — a configuration error reading
        # like a clean negative campaign.
        from src.factor_mining.gp_engine import GPConfig, GPEngine
        from src.factor_mining.grammar import GrammarError
        eng = GPEngine(GPConfig(seed=1, population_size=4),
                       FitnessConfig())
        eng._allowed_terminals = frozenset({"$nonexistent"})
        with self.assertRaises(GrammarError) as ctx:
            eng.initialize_population()
        self.assertIn("empty campaign", str(ctx.exception))

    def test_run_derives_whitelist_from_panel(self) -> None:
        # A campaign panel (seven fields) restricts; a full-registry
        # panel leaves sampling untouched (legacy byte-identical).
        from src.factor_mining.gp_engine import GPConfig, GPEngine
        from src.factor_mining.grammar import FeatureRegistry
        dates = pd.date_range("2023-01-02", periods=3, freq="B")
        seven_panel = {f: pd.DataFrame(1.0, index=dates,
                                       columns=["a", "b", "c"])
                       for f in sorted(self._SEVEN)}
        eng = GPEngine(GPConfig(seed=1, population_size=1,
                                n_generations=0), FitnessConfig())
        eng.run(seven_panel, seven_panel["$close"], n_generations=0)
        self.assertEqual(self._SEVEN, eng._allowed_terminals)
        full_panel = {f: pd.DataFrame(1.0, index=dates,
                                      columns=["a", "b", "c"])
                      for f in FeatureRegistry.V1}
        eng2 = GPEngine(GPConfig(seed=1, population_size=1,
                                 n_generations=0), FitnessConfig())
        eng2.run(full_panel, full_panel["$close"], n_generations=0)
        self.assertIsNone(eng2._allowed_terminals)


class MinerBaselineLoadingTests(unittest.TestCase):
    """The miner must refuse an unbound baseline: it keys the only
    incremental criterion the campaign has."""

    @staticmethod
    def _config(tmp: str, *, path: str, model: str, w_orth: float = 0.0):
        from src.factor_mining.gp_engine import GPConfig
        from src.factor_mining.miner import DataConfig, MinerConfig
        return MinerConfig(
            data=DataConfig(baseline_preds_path=path,
                            baseline_model=model),
            gp=GPConfig(seed=1),
            fitness=FitnessConfig(w_orthogonality=w_orth,
                                  orthogonality_band=0.30),
            output_dir=Path(tmp))

    def test_enabled_penalty_without_baseline_refuses(self) -> None:
        # codex #401 r2 — the symmetric failure to a namespace
        # mismatch: a campaign config that enables the penalty but
        # forgets to bind the baseline would breed with NO incremental
        # criterion while looking like a healthy legacy run.
        from src.factor_mining.miner import load_baseline_predictions
        with tempfile.TemporaryDirectory() as d:
            cfg = self._config(d, path="", model="", w_orth=2.0)
            with self.assertRaises(ValueError) as ctx:
                load_baseline_predictions(cfg)
            self.assertIn("w_orthogonality", str(ctx.exception))

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
    def _bundle(tmp: Path, *, start="2018-01-02", end="2026-08-03"):
        """A self-contained PIT bundle stamp — never the machine's real
        bundle, so the test is hermetic (and CI-safe)."""
        from src.data.pit.bundle_integrity import (
            BundleIdentity,
            write_bundle_integrity,
        )
        tmp.mkdir(parents=True, exist_ok=True)
        write_bundle_integrity(
            tmp, built_from_holey_fetch=False,
            identity=BundleIdentity(
                tail_date=end, content_hash="sha256:" + "0" * 64,
                instrument_count=800, calendar_start=start,
                calendar_end=end))
        return tmp

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

    _PRESET = (_PROJECT_ROOT / "config" / "presets"
               / "pv_incremental_baseline.yaml")

    @classmethod
    def _run_config(cls, **overrides):
        # Mirror the SHAPE of a real aggregate report: the engine
        # captures every WalkForwardConfig field, so a stub with a
        # handful of keys would not exercise the completeness check.
        cfg = dict(bx.materialize_preset(cls._PRESET))
        cfg.update(overrides)
        return cfg

    def test_incomplete_captured_config_refuses(self) -> None:
        # codex #401 r11: a truncated/older report cannot establish
        # which values produced the predictions — skipping absent
        # fields would leave the materialization guarantee with a
        # missing-field fallback.
        cfg = self._run_config()
        del cfg["label_horizon_days"]
        with self.assertRaises(bx.PVBaselineError) as ctx:
            bx.check_run_config_binding(_plan(), {"config": cfg},
                                        self._PRESET)
        self.assertIn("omits materialized field", str(ctx.exception))

    @classmethod
    def _agg(cls, run_dir: Path, *, n_folds: int, commit: str = "a" * 40,
             dirty: bool = False, fold_indices=None, config=None):
        indices = (list(range(n_folds)) if fold_indices is None
                   else list(fold_indices))
        (run_dir / "walk_forward_report.json").write_text(
            json.dumps({
                "git_commit": commit, "git_dirty": dirty,
                "num_folds": n_folds,
                "config": config if config is not None
                else cls._run_config(),
                "folds": [
                    {"fold_index": i,
                     "report_path": str(
                         run_dir / f"fold_{i:02d}_report.json")}
                    for i in indices
                ],
            }), encoding="utf-8")

    def test_foreign_preset_run_refuses(self) -> None:
        # codex #401 r4: a CLEAN run directory from another
        # preset/universe (e.g. the csi300 reference run) must not be
        # exportable with a sidecar claiming the frozen csi800
        # baseline — that certifies the WRONG incremental reference.
        plan = _plan()
        preset = self._PRESET
        for label, override in (
                ("other universe", {"instruments": "csi300"}),
                ("holdout tail", {"overall_end": "2025-12-31"}),
                ("single model", {"ensemble_window": 1}),
                ("other handler", {"feature_handler": "Alpha360"})):
            agg = {"config": self._run_config(**override)}
            with self.assertRaises(bx.PVBaselineError, msg=label) as ctx:
                bx.check_run_config_binding(plan, agg, preset)
            self.assertIn("frozen baseline definition",
                          str(ctx.exception))
        # The frozen combination passes and is returned as evidence.
        identity = bx.check_run_config_binding(
            plan, {"config": self._run_config()}, preset)
        self.assertEqual("csi800", identity["instruments"])
        self.assertEqual("2024-12-31", identity["overall_end"])

    def test_resolved_config_seal_covers_inherited_fields(self) -> None:
        # codex #401 r5: the preset is only an `extends` child, so
        # sealing its bytes leaves inherited training/model fields
        # unsealed. The seal is over the RESOLVED config the engine
        # captured — a changed inherited field must change the hash.
        base = self._run_config()
        sealed = bx.resolved_config_sha256(base)
        self.assertEqual(sealed, bx.resolved_config_sha256(dict(base)))
        # An inherited field the five-field check never inspects.
        drifted = dict(base, train_months=12, seed=99)
        self.assertNotEqual(sealed, bx.resolved_config_sha256(drifted))
        # Key order must not matter (canonical JSON).
        reordered = dict(reversed(list(base.items())))
        self.assertEqual(sealed, bx.resolved_config_sha256(reordered))

    def test_config_chain_hashes_parents(self) -> None:
        chain = bx.config_chain_sha256(
            _PROJECT_ROOT / "config" / "presets"
            / "pv_incremental_baseline.yaml")
        # Child AND the inherited parent must both be recorded.
        self.assertIn("config/presets/pv_incremental_baseline.yaml",
                      chain)
        self.assertIn("config_walk.yaml", chain)
        for sha in chain.values():
            self.assertRegex(sha, r"^[0-9a-f]{64}$")

    def test_config_chain_cycle_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "a.yaml"
            b = Path(d) / "b.yaml"
            a.write_text("extends: b.yaml\n", encoding="utf-8")
            b.write_text("extends: a.yaml\n", encoding="utf-8")
            with self.assertRaises(bx.PVBaselineError) as ctx:
                bx.config_chain_sha256(a)
            self.assertIn("cycle", str(ctx.exception))

    def test_missing_captured_config_refuses(self) -> None:
        with self.assertRaises(bx.PVBaselineError):
            bx.check_run_config_binding(_plan(), {}, self._PRESET)

    def test_materialized_preset_covers_dataclass_defaults(self) -> None:
        # codex #401 r10: fields neither YAML declares (e.g.
        # label_horizon_days) come from WalkForwardConfig defaults but
        # ARE captured in the run report — a run with a non-default
        # value would otherwise pass the binding check.
        frozen = self._PRESET
        materialized = bx.materialize_preset(frozen)
        self.assertIn("label_horizon_days", materialized)
        self.assertNotIn("label_horizon_days",
                         bx.resolve_preset(frozen))   # YAML-only view
        # A run whose captured horizon differs from the materialized
        # default must refuse.
        cfg = self._run_config(
            label_horizon_days=materialized["label_horizon_days"] + 1)
        with self.assertRaises(bx.PVBaselineError) as ctx:
            bx.check_run_config_binding(_plan(), {"config": cfg}, frozen)
        self.assertIn("did not drive this run", str(ctx.exception))

    def test_failed_fold_placeholder_refuses(self) -> None:
        # codex #401 r10: a failed fold is recorded with
        # report_path: null; falling back to the canonical filename
        # would resolve a stale same-index artifact from an earlier run.
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d) / "run"
            run_dir.mkdir()
            self._fold(run_dir, 0, "2023-01-02", "2023-03-31")
            agg = {
                "git_commit": "a" * 40, "git_dirty": False,
                "num_folds": 1,
                "folds": [{"fold_index": 0, "report_path": None}],
            }
            with self.assertRaises(bx.PVBaselineError) as ctx:
                bx.resolve_fold_reports(run_dir, agg)
            self.assertIn("FAILED fold", str(ctx.exception))

    def test_sibling_preset_refuses_via_resolved_inheritance(self) -> None:
        # codex #401 r6: a sibling preset declares the same overlapping
        # keys but OMITS overall_end, inheriting 2025-12-31 — comparing
        # only declared keys let it pass and the sidecar would name the
        # wrong config in a later provenance dispute.
        sibling = (_PROJECT_ROOT / "config" / "presets"
                   / "csi800_campaign_base.yaml")
        resolved = bx.resolve_preset(sibling)
        # Inheritance really does supply the holdout-year tail here.
        self.assertEqual("2025-12-31", resolved["overall_end"])
        with self.assertRaises(bx.PVBaselineError) as ctx:
            bx.check_run_config_binding(
                _plan(), {"config": self._run_config()}, sibling)
        self.assertIn("did not drive this run", str(ctx.exception))

    def test_frozen_preset_resolves_and_passes(self) -> None:
        frozen = self._PRESET
        resolved = bx.resolve_preset(frozen)
        self.assertEqual("2024-12-31", resolved["overall_end"])
        self.assertEqual("csi800", resolved["instruments"])
        # Inherited (not declared in the child) fields are present.
        self.assertEqual(3, resolved["ensemble_window"])
        self.assertEqual("Alpha158", resolved["feature_handler"])
        identity = bx.check_run_config_binding(
            _plan(), {"config": self._run_config(
                **{k: v for k, v in resolved.items()
                   if k in ("train_months", "valid_months",
                            "test_months", "step_months")})},
            frozen)
        self.assertEqual("csi800", identity["instruments"])

    def test_wrong_run_config_flag_refuses(self) -> None:
        # The supplied --run-config must be the preset that actually
        # drove the run, else run_config_sha256 binds the wrong file.
        with tempfile.TemporaryDirectory() as d:
            other = Path(d) / "other.yaml"
            other.write_text("instruments: csi300\n", encoding="utf-8")
            with self.assertRaises(bx.PVBaselineError) as ctx:
                bx.check_run_config_binding(
                    _plan(), {"config": self._run_config()}, other)
            self.assertIn("did not drive this run", str(ctx.exception))

    def test_stale_extra_fold_refuses_even_when_count_matches(self) -> None:
        # codex #401 r2: a run dir missing one declared fold while
        # carrying a stale fold from ANOTHER run has the same file
        # count — the count check passes and the stray fold would be
        # exported. The aggregate report's folds[] is the authority.
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d) / "run"
            run_dir.mkdir()
            self._fold(run_dir, 0, "2023-01-02", "2023-03-31")
            self._fold(run_dir, 9, "2023-04-03", "2023-06-30")  # stray
            # Aggregate declares folds 0 and 1 — fold 1 is missing,
            # fold 9 is a leftover; the COUNT still matches (2 == 2).
            self._agg(run_dir, n_folds=2, fold_indices=[0, 1])
            self.assertEqual(2, bx.main(
                ["--run-dir", str(run_dir),
                 "--out-dir", str(Path(d) / "out"),
                 "--provider-uri", str(self._bundle(Path(d) / "bundle"))]))

    def test_non_contiguous_declared_indexes_refuse(self) -> None:
        # codex #401 r3: uniqueness is not enough — an aggregate
        # declaring [0, 9] with num_folds=2 would let a stale fold
        # stand in for a missing fold 1 with every count check passing.
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d) / "run"
            run_dir.mkdir()
            self._fold(run_dir, 0, "2023-01-02", "2023-03-31")
            self._fold(run_dir, 9, "2023-04-03", "2023-06-30")
            self._agg(run_dir, n_folds=2, fold_indices=[0, 9])
            self.assertEqual(2, bx.main(
                ["--run-dir", str(run_dir),
                 "--out-dir", str(Path(d) / "out"),
                 "--provider-uri", str(self._bundle(Path(d) / "bundle"))]))

    def test_report_path_outside_run_dir_refuses(self) -> None:
        # codex #401 r3: a stored absolute path from the original run
        # must never let the exporter certify one directory while
        # reading fold evidence from another.
        with tempfile.TemporaryDirectory() as d:
            foreign = Path(d) / "foreign"
            foreign.mkdir()
            self._fold(foreign, 0, "2023-01-02", "2023-03-31")
            run_dir = Path(d) / "run"
            run_dir.mkdir()
            (run_dir / "walk_forward_report.json").write_text(
                json.dumps({
                    "git_commit": "a" * 40, "git_dirty": False,
                    "num_folds": 1,
                    "folds": [{"fold_index": 0,
                               "report_path": str(
                                   foreign / "fold_00_report.json")}],
                }), encoding="utf-8")
            with self.assertRaises(bx.PVBaselineError) as ctx:
                bx.resolve_fold_reports(
                    run_dir,
                    json.loads((run_dir / "walk_forward_report.json")
                               .read_text(encoding="utf-8")))
            self.assertIn("OUTSIDE", str(ctx.exception))

    def test_moved_run_dir_still_resolves(self) -> None:
        # The legitimate case the basename fallback exists for: the
        # run dir was moved wholesale, so the recorded absolute path
        # no longer exists but the local file does.
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d) / "moved"
            run_dir.mkdir()
            self._fold(run_dir, 0, "2023-01-02", "2023-03-31")
            agg = {
                "git_commit": "a" * 40, "git_dirty": False,
                "num_folds": 1,
                "folds": [{"fold_index": 0,
                           "report_path":
                               "D:/old/place/fold_00_report.json"}],
            }
            resolved = bx.resolve_fold_reports(run_dir, agg)
            self.assertEqual(
                [(0, run_dir / "fold_00_report.json")], resolved)

    def test_mismatched_fold_index_payload_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d) / "run"
            run_dir.mkdir()
            self._fold(run_dir, 0, "2023-01-02", "2023-03-31")
            # Rewrite the payload's own index so file name and content
            # disagree — a stale/renamed report.
            rp = run_dir / "fold_00_report.json"
            payload = json.loads(rp.read_text(encoding="utf-8"))
            payload["fold_index"] = 7
            rp.write_text(json.dumps(payload), encoding="utf-8")
            self._agg(run_dir, n_folds=1)
            self.assertEqual(2, bx.main(
                ["--run-dir", str(run_dir),
                 "--out-dir", str(Path(d) / "out"),
                 "--provider-uri", str(self._bundle(Path(d) / "bundle"))]))

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
            bundle = self._bundle(Path(d) / "bundle")
            rc = bx.main(["--run-dir", str(run_dir),
                          "--out-dir", str(out),
                          "--provider-uri", str(bundle)])
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
            # r5: the sealed hash is over the RESOLVED config, and the
            # snapshot + chain hashes ride along as evidence.
            self.assertEqual("resolved_walk_forward_config",
                             sidecar["run_config_sha256_kind"])
            self.assertEqual(
                bx.resolved_config_sha256(self._run_config()),
                sidecar["run_config_sha256"])
            self.assertEqual("csi800",
                             sidecar["resolved_config"]["instruments"])
            self.assertIn("config_walk.yaml",
                          sidecar["config_chain_sha256"])
            # Decision A disclosure: partial IS coverage is recorded.
            self.assertEqual(3, sidecar["coverage"]["is_days_covered"])
            self.assertEqual(3, sidecar["coverage"]["oos_days_covered"])
            self.assertEqual("2020-04-01",
                             sidecar["coverage"]["first_baseline_date"])
            # A second export into the same dir refuses (never
            # overwrite a baseline other scores are keyed to).
            self.assertEqual(2, bx.main(["--run-dir", str(run_dir),
                                         "--out-dir", str(out),
                                         "--provider-uri", str(bundle)]))

    def test_stray_row_outside_declared_window_refuses(self) -> None:
        # codex #401 r1 (sacred invariant): a report may declare an
        # in-range test window while the pickle carries extra rows —
        # the sha check passes (the file is unmodified since the run)
        # yet blinded/forbidden rows would enter the baseline.
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d) / "run"
            run_dir.mkdir()
            self._fold(
                run_dir, 0, "2024-10-01", "2024-12-31",
                dates=pd.DatetimeIndex(
                    ["2024-10-01", "2024-12-31", "2025-01-02"]))
            self._agg(run_dir, n_folds=1)
            self.assertEqual(2, bx.main(
                ["--run-dir", str(run_dir),
                 "--out-dir", str(Path(d) / "out"),
                 "--provider-uri", str(self._bundle(Path(d) / "bundle"))]))

    def test_bundle_calendar_must_contain_exported_rows(self) -> None:
        # codex #401 r12: config hashes cannot say WHICH data produced
        # the baseline. A bundle whose calendar cannot contain the
        # exported rows provably did not produce them.
        with tempfile.TemporaryDirectory() as d:
            idx = pd.DatetimeIndex(["2023-01-02", "2023-03-31"])
            ok = bx.bind_source_bundle(
                str(self._bundle(Path(d) / "ok")), idx)
            self.assertEqual("sha256:" + "0" * 64, ok["content_hash"])
            self.assertIn("observed_at_export_time",
                          ok["binding_strength"])
            narrow = self._bundle(Path(d) / "narrow",
                                  start="2024-01-02", end="2024-12-31")
            with self.assertRaises(bx.PVBaselineError) as ctx:
                bx.bind_source_bundle(str(narrow), idx)
            self.assertIn("did not produce these predictions",
                          str(ctx.exception))
            # No stamp at all → refuse (provenance unestablished).
            bare = Path(d) / "bare"
            bare.mkdir()
            with self.assertRaises(bx.PVBaselineError):
                bx.bind_source_bundle(str(bare), idx)

    def test_tampered_fold_pickle_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d) / "run"
            run_dir.mkdir()
            self._fold(run_dir, 0, "2023-01-02", "2023-03-31",
                       tamper=True)
            self._agg(run_dir, n_folds=1)
            self.assertEqual(2, bx.main(
                ["--run-dir", str(run_dir),
                 "--out-dir", str(Path(d) / "out"),
                 "--provider-uri", str(self._bundle(Path(d) / "bundle"))]))

    def test_partial_run_dir_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d) / "run"
            run_dir.mkdir()
            self._fold(run_dir, 0, "2023-01-02", "2023-03-31")
            self._agg(run_dir, n_folds=5)      # declares more folds
            self.assertEqual(2, bx.main(
                ["--run-dir", str(run_dir),
                 "--out-dir", str(Path(d) / "out"),
                 "--provider-uri", str(self._bundle(Path(d) / "bundle"))]))


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
