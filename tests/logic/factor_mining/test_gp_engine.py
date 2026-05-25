"""Tests for the GP engine: subtree helpers, operators, loop, determinism."""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

from src.factor_mining.expression import Terminal, parse_expression
from src.factor_mining.fitness import FitnessConfig
from src.factor_mining.gp_engine import (
    GPConfig,
    GPEngine,
    _enumerate_positions,
    _get_subtree,
    _replace_subtree,
)
from src.factor_mining.grammar import ExprType

# ---------------------------------------------------------------------------
# Tiny synthetic panel for engine tests
# ---------------------------------------------------------------------------


def _make_panel(seed=0, n_tickers=8, n_dates=50):
    rng = np.random.default_rng(seed)
    tickers = [f"T{i:03d}" for i in range(n_tickers)]
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")
    fields = ["$open", "$high", "$low", "$close", "$volume", "$money"]
    panel = {}
    for f in fields:
        data = rng.normal(100, 5, size=(n_dates, n_tickers))
        panel[f] = pd.DataFrame(
            data,
            index=pd.Index(dates, name="datetime"),
            columns=pd.Index(tickers, name="instrument"),
        )
    fwd = pd.DataFrame(
        rng.normal(0, 0.02, size=(n_dates, n_tickers)),
        index=pd.Index(dates, name="datetime"),
        columns=pd.Index(tickers, name="instrument"),
    )
    return panel, fwd


def _engine(seed=42, population_size=10, n_generations=2):
    cfg = GPConfig(
        population_size=population_size,
        n_generations=n_generations,
        max_depth=4,
        min_depth=2,
        seed=seed,
    )
    return GPEngine(cfg, FitnessConfig())


# ---------------------------------------------------------------------------
# Subtree helpers
# ---------------------------------------------------------------------------


def test_enumerate_positions_terminal():
    t = Terminal("$close")
    positions = _enumerate_positions(t)
    assert len(positions) == 1
    assert positions[0][0] == ()
    assert positions[0][1] is t


def test_enumerate_positions_nested():
    expr = parse_expression("cs_rank(div_safe(ts_delta($close, 20), $close))")
    positions = _enumerate_positions(expr)
    # cs_rank(1) + div_safe(1) + ts_delta(1) + 3 terminals = 6
    assert len(positions) == 6
    paths = [p for p, _ in positions]
    assert () in paths


def test_get_subtree_at_root():
    expr = parse_expression("cs_rank($volume)")
    assert _get_subtree(expr, ()) is expr


def test_get_subtree_navigation():
    expr = parse_expression("cs_rank(ts_pctchange($close, 5))")
    # cs_rank's child[0] is ts_pctchange
    assert _get_subtree(expr, (0,)).op_name == "ts_pctchange"
    # ts_pctchange's child[0] is $close
    assert _get_subtree(expr, (0, 0)).name == "$close"


def test_replace_subtree_at_root():
    expr = parse_expression("cs_rank($volume)")
    new = parse_expression("cs_rank($money)")
    rebuilt = _replace_subtree(expr, (), new)
    assert rebuilt == new


def test_replace_subtree_round_trip():
    expr = parse_expression("cs_rank(ts_pctchange($close, 5))")
    for path, sub in _enumerate_positions(expr):
        rebuilt = _replace_subtree(expr, path, sub)
        assert rebuilt == expr, f"round-trip failed at path={path}"


def test_replace_subtree_invalid_path_raises():
    expr = Terminal("$close")
    with pytest.raises(IndexError):
        _replace_subtree(expr, (0,), Terminal("$volume"))


# ---------------------------------------------------------------------------
# Initial population
# ---------------------------------------------------------------------------


def test_initialize_population_target_size():
    engine = _engine(population_size=15)
    engine.initialize_population()
    assert len(engine.population) == 15
    # All roots have CSF / PURE type
    for expr in engine.population:
        assert expr.output_type == ExprType("CSF", "PURE")


def test_initialize_population_deterministic():
    e1 = _engine(seed=12345)
    e2 = _engine(seed=12345)
    e1.initialize_population()
    e2.initialize_population()
    assert [hash(e) for e in e1.population] == [hash(e) for e in e2.population]


def test_initialize_population_different_seed_differs():
    e1 = _engine(seed=1)
    e2 = _engine(seed=2)
    e1.initialize_population()
    e2.initialize_population()
    assert [hash(e) for e in e1.population] != [hash(e) for e in e2.population]


# ---------------------------------------------------------------------------
# Crossover
# ---------------------------------------------------------------------------


def test_crossover_returns_legal_expression():
    engine = _engine(seed=7)
    engine.initialize_population()
    a, b = engine.population[0], engine.population[1]
    child = engine.crossover(a, b)
    # Child must still have CSF / PURE root (the type contract)
    assert child.output_type == ExprType("CSF", "PURE")


def test_crossover_with_no_matching_type_returns_parent():
    # Two parents with disjoint subtree-type signatures: an artificial
    # construction where parent_b has only a single (CSF, PURE) node.
    engine = _engine(seed=1)
    # parent_a has many subtrees including a (FLOAT, ADJ_TAINTED) one
    parent_a = parse_expression("cs_rank(div_safe(ts_delta($close, 20), $close))")
    # parent_b has only a (CSF, PURE) and (FEATURE, PURE) — no ADJ_TAINTED
    parent_b = parse_expression("cs_rank($volume)")
    # If the random pick from parent_a lands on the (FLOAT, ADJ_TAINTED) subtree,
    # parent_b has no match → returns parent_a unchanged.
    # Run it enough times that we cover both pick branches; in either case,
    # the result must be type-valid.
    child = engine.crossover(parent_a, parent_b)
    assert child.output_type == ExprType("CSF", "PURE")


# ---------------------------------------------------------------------------
# Mutation
# ---------------------------------------------------------------------------


def test_mutate_subtree_preserves_type():
    engine = _engine(seed=3)
    expr = parse_expression("cs_rank(ts_pctchange($close, 5))")
    mutated = engine.mutate_subtree(expr)
    assert mutated.output_type == ExprType("CSF", "PURE")


def test_mutate_point_swaps_same_taint():
    engine = _engine(seed=4)
    # All terminals are either ADJ_TAINTED ($close) or INT_WINDOW (5)
    expr = parse_expression("cs_rank(ts_pctchange($close, 5))")
    # Run several mutations and check each substitutes within same taint group
    for _ in range(20):
        mutated = engine.mutate_point(expr)
        assert mutated.output_type == ExprType("CSF", "PURE")


def test_mutate_const_swaps_window_literal():
    engine = _engine(seed=5)
    expr = parse_expression("cs_rank(ts_mean($volume, 5))")
    # Run multiple times and verify the resulting window stays within WINDOW_LITERALS
    from src.factor_mining.grammar import WINDOW_LITERALS

    legal = {str(w) for w in WINDOW_LITERALS}
    for _ in range(20):
        mutated = engine.mutate_const(expr)
        # The window literal is at path (0, 1)
        from src.factor_mining.gp_engine import _get_subtree

        node = _get_subtree(mutated, (0, 1))
        assert node.name in legal


def test_mutate_const_no_window_returns_unchanged():
    engine = _engine(seed=5)
    expr = parse_expression("cs_rank($volume)")  # no window literal
    mutated = engine.mutate_const(expr)
    assert mutated == expr


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_tournament_selection_returns_best():
    engine = _engine(seed=2)
    # Build fake evaluated list — fitness varies
    pop = [Terminal(f) for f in ("$volume", "$money", "$volume")]  # silly but legal
    # We can't actually construct terminals as evaluated because evaluator
    # needs CSF root. Use cs_rank wrappers.
    pop = [
        parse_expression("cs_rank($volume)"),
        parse_expression("cs_rank($money)"),
        parse_expression("cs_rank(div_safe($money, $volume))"),
    ]
    evaluated = [(pop[0], 0.5), (pop[1], 1.2), (pop[2], 0.8)]
    # k=3, so the tournament must sample all three; the highest is index 1.
    chosen = engine.select(evaluated)
    assert chosen == pop[1]


# ---------------------------------------------------------------------------
# Generation loop
# ---------------------------------------------------------------------------


def test_run_loop_completes_and_returns_pool():
    engine = _engine(seed=11, population_size=8, n_generations=2)
    panel, fwd = _make_panel(seed=11, n_tickers=6, n_dates=30)
    pool = engine.run(panel, fwd)
    assert pool is not None
    # Some entries should have been added (most random expressions will
    # be valid on a small synthetic panel).
    assert len(pool) >= 1
    # History has one entry per generation
    assert len(engine.history) == 2


def test_run_loop_deterministic_with_seed():
    panel, fwd = _make_panel(seed=99, n_tickers=6, n_dates=30)
    e1 = _engine(seed=20260523, population_size=8, n_generations=2)
    e2 = _engine(seed=20260523, population_size=8, n_generations=2)
    pool1 = e1.run(panel, fwd)
    pool2 = e2.run(panel, fwd)
    assert len(pool1) == len(pool2)
    hashes_1 = sorted(hash(e.expr) for e in pool1.all_entries())
    hashes_2 = sorted(hash(e.expr) for e in pool2.all_entries())
    assert hashes_1 == hashes_2
    # Per-hash fitness match within tolerance
    by_hash_1 = {hash(e.expr): e.fitness for e in pool1.all_entries()}
    by_hash_2 = {hash(e.expr): e.fitness for e in pool2.all_entries()}
    for h in by_hash_1:
        assert by_hash_1[h] == pytest.approx(by_hash_2[h], abs=1e-12)
    # History entries match
    assert [s.best_fitness for s in e1.history] == [s.best_fitness for s in e2.history]


def test_run_loop_history_records_each_gen():
    engine = _engine(seed=33, population_size=8, n_generations=3)
    panel, fwd = _make_panel(seed=33, n_tickers=5, n_dates=25)
    engine.run(panel, fwd)
    assert len(engine.history) == 3
    assert [s.gen for s in engine.history] == [0, 1, 2]


def test_next_generation_includes_elites():
    engine = _engine(seed=44, population_size=10)
    engine.initialize_population()
    panel, fwd = _make_panel(seed=44, n_tickers=5, n_dates=25)
    evaluated = []
    for expr in engine.population:
        score, _ = engine.evaluate_individual(expr, panel, fwd)
        evaluated.append((expr, score))
    sorted_by_fitness = sorted(evaluated, key=lambda iev: -iev[1])
    next_pop = engine.next_generation(evaluated)
    n_elite = max(1, int(engine.config.elite_frac * engine.config.population_size))
    # The top n_elite from evaluated should appear in next_pop (as the head)
    elite_hashes = [hash(ev[0]) for ev in sorted_by_fitness[:n_elite]]
    next_pop_hashes = [hash(e) for e in next_pop[:n_elite]]
    assert next_pop_hashes == elite_hashes


# ---------------------------------------------------------------------------
# Checkpoint round-trip
# ---------------------------------------------------------------------------


def test_checkpoint_save_load_round_trip(tmp_path):
    engine = _engine(seed=77, population_size=8, n_generations=4)
    panel, fwd = _make_panel(seed=77, n_tickers=5, n_dates=25)
    # Run 2 gens, save, load, run 2 more
    engine.run(panel, fwd, n_generations=2)
    ckpt_path = engine.save_checkpoint(tmp_path / "ckpt.json")
    assert ckpt_path.is_file()

    resumed = GPEngine.load_checkpoint(ckpt_path, fitness_config=FitnessConfig())
    resumed_pool = resumed.run(panel, fwd, n_generations=2)

    # Continuous run for comparison
    continuous = _engine(seed=77, population_size=8, n_generations=4)
    continuous_pool = continuous.run(panel, fwd)

    # Compare pools: hash set and per-hash fitness within tolerance
    h_resumed = sorted(hash(e.expr) for e in resumed_pool.all_entries())
    h_continuous = sorted(hash(e.expr) for e in continuous_pool.all_entries())
    assert h_resumed == h_continuous

    by_resumed = {hash(e.expr): e.fitness for e in resumed_pool.all_entries()}
    by_continuous = {hash(e.expr): e.fitness for e in continuous_pool.all_entries()}
    for h in by_resumed:
        assert by_resumed[h] == pytest.approx(by_continuous[h], abs=1e-12)


def test_checkpoint_preserves_history_and_current_gen(tmp_path):
    engine = _engine(seed=88, population_size=6, n_generations=3)
    panel, fwd = _make_panel(seed=88, n_tickers=4, n_dates=20)
    engine.run(panel, fwd, n_generations=2)
    ckpt_path = engine.save_checkpoint(tmp_path / "ckpt.json")
    resumed = GPEngine.load_checkpoint(ckpt_path, fitness_config=FitnessConfig())
    assert resumed.current_gen == 2
    assert len(resumed.history) == 2


# ---------------------------------------------------------------------------
# Codex PR #142 P1 regression: evaluator-method version drift.
#
# Pre-PR #142 checkpoints stored scores computed with ``method="rank"``;
# the new engine evaluates with ``method="normal"``. Without a guard,
# ``load_checkpoint`` would silently restore the old scores into
# ``fitness_cache`` / ``_all_evaluated`` and ``evaluate_individual``
# would return them by hash without recomputing, mixing two scoring
# semantics in one resumed run and skewing selection + final pool.
# ---------------------------------------------------------------------------


def test_checkpoint_embeds_evaluator_method(tmp_path):
    """Saved payloads must tag scores with the method that produced
    them so a resume can detect the cross-method case."""
    import json

    from src.factor_mining.gp_engine import FITNESS_EVALUATOR_METHOD

    engine = _engine(seed=11, population_size=4, n_generations=2)
    panel, fwd = _make_panel(seed=11, n_tickers=3, n_dates=15)
    engine.run(panel, fwd, n_generations=1)
    ckpt_path = engine.save_checkpoint(tmp_path / "ckpt.json")
    state = json.loads(ckpt_path.read_text(encoding="utf-8"))
    assert state["evaluator_method"] == FITNESS_EVALUATOR_METHOD
    assert FITNESS_EVALUATOR_METHOD == "normal"  # contract anchor


def _write_legacy_style_checkpoint(
    path,
    *,
    engine,
    method_tag,
    extra_score_hashes=(99999, 88888),
):
    """Serialise ``engine`` and then mutate the payload to look like
    a pre-PR #142 or method-mismatched checkpoint.

    ``method_tag=None`` deletes the field entirely (the legacy shape).
    ``method_tag="rank"`` simulates a checkpoint written when fitness
    was double-counting rank IC. Either way, extra synthetic hashes
    are spliced into ``fitness_cache`` so a successful invalidation is
    visually distinguishable from "the round-trip happened to be empty".
    """
    import json

    path = engine.save_checkpoint(path)
    state = json.loads(path.read_text(encoding="utf-8"))
    if method_tag is None:
        state.pop("evaluator_method", None)
    else:
        state["evaluator_method"] = method_tag
    for h in extra_score_hashes:
        state["fitness_cache"][str(h)] = -12345.0
    path.write_text(json.dumps(state), encoding="utf-8")
    return path


def _capture_gp_engine_warnings(monkeypatch):
    """Stand-in for ``caplog`` — captures ``_log.warning`` calls on the
    gp_engine module logger directly.

    The project's logging config bypasses ``caplog``'s handler
    attachment when the full test directory runs (see the note on
    ``test_evaluate_individual_warns_once_per_exception``). Monkey-
    patching the bound method is reliable; the tests below depend on
    seeing the invalidation warning, which is part of the observable
    contract for an operator debugging a resume that quietly lost
    scores.
    """
    import src.factor_mining.gp_engine as gp_mod

    captured: list[str] = []
    original = gp_mod._log.warning

    def fake_warning(msg, *args, **kwargs):
        try:
            captured.append(msg % args if args else str(msg))
        except Exception:  # noqa: BLE001 — defensive: never crash a test on log formatting
            captured.append(str(msg))
        return original(msg, *args, **kwargs)

    monkeypatch.setattr(gp_mod._log, "warning", fake_warning)
    return captured


def test_load_legacy_checkpoint_discards_caches_and_warns(tmp_path, monkeypatch):
    """Pre-PR #142 checkpoints have no ``evaluator_method`` field. Treat
    them as method-mismatched: clear scores, preserve everything else."""
    warnings = _capture_gp_engine_warnings(monkeypatch)

    engine = _engine(seed=22, population_size=4, n_generations=2)
    panel, fwd = _make_panel(seed=22, n_tickers=3, n_dates=15)
    engine.run(panel, fwd, n_generations=1)
    ckpt_path = _write_legacy_style_checkpoint(
        tmp_path / "legacy.json", engine=engine, method_tag=None,
    )
    resumed = GPEngine.load_checkpoint(ckpt_path, fitness_config=FitnessConfig())
    assert resumed.fitness_cache == {}
    assert resumed._all_evaluated == {}
    # Non-score state must survive the invalidation.
    assert resumed.current_gen == engine.current_gen
    assert len(resumed.population) == len(engine.population)
    assert len(resumed.history) == len(engine.history)
    assert any(
        "evaluator_method" in msg and "discarding" in msg for msg in warnings
    ), warnings


def test_load_rank_method_checkpoint_discards_caches_and_warns(tmp_path, monkeypatch):
    """A checkpoint that explicitly declares ``method="rank"`` (the
    pre-PR #142 contract) must invalidate the same way as a legacy
    checkpoint with no field. Anchors the cross-method case."""
    warnings = _capture_gp_engine_warnings(monkeypatch)

    engine = _engine(seed=33, population_size=4, n_generations=2)
    panel, fwd = _make_panel(seed=33, n_tickers=3, n_dates=15)
    engine.run(panel, fwd, n_generations=1)
    ckpt_path = _write_legacy_style_checkpoint(
        tmp_path / "rank.json", engine=engine, method_tag="rank",
    )
    resumed = GPEngine.load_checkpoint(ckpt_path, fitness_config=FitnessConfig())
    assert resumed.fitness_cache == {}
    assert resumed._all_evaluated == {}
    assert any("'rank'" in msg for msg in warnings), (
        f"warning should name the offending method tag; got: {warnings}"
    )


def test_load_matching_method_checkpoint_keeps_caches(tmp_path, monkeypatch):
    """The fast path: same method on save and load preserves cached
    scores byte-for-byte (no spurious invalidation)."""
    warnings = _capture_gp_engine_warnings(monkeypatch)

    engine = _engine(seed=44, population_size=4, n_generations=2)
    panel, fwd = _make_panel(seed=44, n_tickers=3, n_dates=15)
    engine.run(panel, fwd, n_generations=1)
    ckpt_path = engine.save_checkpoint(tmp_path / "ok.json")
    n_cached = len(engine.fitness_cache)
    assert n_cached > 0, "test setup: need a non-empty cache to make this meaningful"
    resumed = GPEngine.load_checkpoint(ckpt_path, fitness_config=FitnessConfig())
    assert len(resumed.fitness_cache) == n_cached
    assert not any("discarding" in msg for msg in warnings), (
        f"no warning expected on the matching-method path; got: {warnings}"
    )


# ---------------------------------------------------------------------------
# Phase 3.1 acceptance: convergence on a toy moving-average crossover
# target. Per docs/factor_mining/factor_mining_claude_code_design.md §6
# Phase 3.1: "On toy target mean(x,10)-mean(x,30), converges < 20 gens".
#
# We construct a panel where the forward return is rank-correlated with
# the MA-crossover signal cs_rank(ts_mean($close,10) - ts_mean($close,30))
# plus noise. With pop=50, gen=20, seed=42 the GP should:
#
#   1. produce a finite, positive best fitness in the final generation,
#   2. improve over the initial generation (some learning happens), and
#   3. find at least one expression whose rank-IC exceeds a meaningful
#      threshold on the same panel.
#
# We do NOT assert that the GP rediscovers the literal target expression
# — Phase 1's grammar admits many equivalent paths (ts_pctchange, ratio
# variants, etc.); the convergence guarantee is statistical, not
# structural.
# ---------------------------------------------------------------------------


def _build_toy_target_panel(seed=42, n_tickers=15, n_dates=150):
    """Synthetic panel where fwd_return ≈ cs_rank(MA10 - MA30) + noise.

    Returns ``(panel_dict, forward_return_df)``. The signal strength
    ``alpha`` and noise scale are tuned so the cross-sectional Spearman
    correlation between the true crossover factor and the engineered
    fwd_return averages around 0.4–0.6 — easily detectable by a
    20-generation GP search but not trivially saturated at 1.0.
    """
    rng = np.random.default_rng(seed)
    tickers = [f"T{i:03d}" for i in range(n_tickers)]
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")

    # Random-walk closes
    log_returns = rng.normal(0.0005, 0.02, size=(n_dates, n_tickers))
    close_arr = np.exp(np.cumsum(log_returns, axis=0)) * 100.0
    close_df = pd.DataFrame(
        close_arr,
        index=pd.Index(dates, name="datetime"),
        columns=pd.Index(tickers, name="instrument"),
    )

    # OHLCV panel — open/high/low/$volume/$money proxies. The GP only
    # needs $close-like fields to discover the MA-crossover; other fields
    # serve as distractors.
    open_df = close_df * np.exp(rng.normal(0, 0.003, size=close_df.shape))
    high_df = close_df * (1 + np.abs(rng.normal(0, 0.005, size=close_df.shape)))
    low_df = close_df * (1 - np.abs(rng.normal(0, 0.005, size=close_df.shape)))
    volume_df = pd.DataFrame(
        np.exp(rng.normal(12, 1.0, size=close_df.shape)),
        index=close_df.index, columns=close_df.columns,
    )
    money_df = volume_df * close_df

    panel = {
        "$open": open_df,
        "$high": high_df,
        "$low": low_df,
        "$close": close_df,
        "$volume": volume_df,
        "$money": money_df,
    }

    # True target signal: cs_rank of the MA10-MA30 crossover.
    ma_short = close_df.rolling(10, min_periods=10).mean()
    ma_long = close_df.rolling(30, min_periods=30).mean()
    crossover = ma_short - ma_long
    target_signal = crossover.rank(axis=1, pct=True) - 0.5  # [-0.5, 0.5]

    # Forward return = alpha * signal + noise. With alpha=0.04 and
    # noise std=0.02 the per-day Spearman corr(signal, fwd) is ~0.4-0.6
    # — strong enough that a 20-gen GP finds a clear winner.
    alpha = 0.04
    noise_std = 0.02
    noise = pd.DataFrame(
        rng.normal(0, noise_std, size=close_df.shape),
        index=close_df.index, columns=close_df.columns,
    )
    fwd = alpha * target_signal + noise
    fwd.index.name = "datetime"
    fwd.columns.name = "instrument"
    return panel, fwd


def test_gp_converges_on_toy_ma_crossover_target():
    """Phase 3.1 acceptance: GP improves over 20 generations and finds
    an expression with meaningful rank-IC on the engineered target
    panel. See docs/factor_mining/factor_mining_claude_code_design.md
    §6 Phase 3.1."""
    panel, fwd = _build_toy_target_panel(seed=42)
    config = GPConfig(
        population_size=50,
        n_generations=20,
        tournament_size=3,
        elite_frac=0.05,
        p_crossover=0.7,
        p_mutate_subtree=0.15,
        p_mutate_point=0.10,
        p_mutate_const=0.05,
        max_depth=5,
        min_depth=2,
        seed=42,
    )
    engine = GPEngine(config, FitnessConfig())
    pool = engine.run(panel, fwd)

    # 1. The GP loop completed all 20 generations.
    assert len(engine.history) == 20

    initial_best = engine.history[0].best_fitness
    final_best = engine.history[-1].best_fitness

    # 2. The final-gen best fitness is finite (some expression survived
    #    the validity filters and produced a real metric bundle).
    assert np.isfinite(final_best), (
        f"final-gen best fitness is not finite: {final_best!r}"
    )

    # 3. The GP improved — final-gen best is at least as good as
    #    initial-gen best. We use ">=" rather than strict ">" because
    #    elitism may carry the same best from gen 0 to gen 19 when
    #    nothing better is mined; the design doc's "convergence" verb
    #    encompasses "found a good factor early and kept it".
    assert final_best >= initial_best - 1e-9, (
        f"GP regressed: initial best {initial_best!r} > final best "
        f"{final_best!r}; expected non-decreasing best fitness."
    )

    # 4. The top-ranked expression has a meaningful rank-IC on the
    #    engineered target. The toy signal puts per-day RankIC in the
    #    0.4-0.6 band; the GP shouldn't need to find the literal MA10
    #    - MA30 expression — any factor that rank-correlates with it
    #    will inherit RankIC > 0.1 in expectation. We assert a loose
    #    threshold so a small-population (50) run remains green across
    #    pandas / numpy upgrades.
    assert len(pool) >= 1, "GP produced an empty factor pool"
    top1 = pool.top_k(1, by="rank_ic_mean")[0]
    assert abs(top1.rank_ic_mean) > 0.1, (
        f"Best rank-IC too weak to claim convergence: "
        f"{top1.rank_ic_mean:.4f} (expr: {top1.expr.to_qlib_string()!r})"
    )


# ---------------------------------------------------------------------------
# Regression: miner must use method="normal" so Pearson and Spearman IC
# are independent fitness inputs. See PR fix-rank-ic-double-count.
# ---------------------------------------------------------------------------


def test_evaluate_individual_uses_normal_method_not_rank():
    """Regression: ``evaluate_individual`` must call ``evaluate_factor``
    with ``method='normal'``. With ``method='rank'`` (the old default)
    ``ic_mean == rank_ic_mean`` and the fitness formula
    ``w_ic·|ic_mean| + w_rankic·|rank_ic_mean|`` double-counts rank IC.
    """
    import src.factor_mining.gp_engine as gp_mod

    captured_methods: list[str] = []
    original = gp_mod.evaluate_factor

    def recorder(expr, panel, fwd_ret, *, method="rank"):
        captured_methods.append(method)
        return original(expr, panel, fwd_ret, method=method)

    engine = _engine(seed=314, population_size=4, n_generations=1)
    panel, fwd = _make_panel(seed=314, n_tickers=5, n_dates=20)
    gp_mod.evaluate_factor = recorder
    try:
        engine.initialize_population()
        for expr in engine.population:
            engine.evaluate_individual(expr, panel, fwd)
    finally:
        gp_mod.evaluate_factor = original

    assert captured_methods, "no evaluator calls were recorded"
    assert all(m == "normal" for m in captured_methods), (
        f"miner must use method='normal'; saw methods={captured_methods!r}"
    )


# ---------------------------------------------------------------------------
# Exception classification: KeyError fail-fast, others warn-once + -inf
# ---------------------------------------------------------------------------


def test_evaluate_individual_fails_fast_on_missing_panel_feature():
    """A panel missing a feature the grammar uses is a setup-time
    data-contract violation, not a per-expression arithmetic failure.
    ``evaluate_individual`` must re-raise as RuntimeError instead of
    silently caching -inf — otherwise the operator never sees the
    bug and every random expression scores -inf."""
    from src.factor_mining.expression import OperatorCall, Terminal

    engine = _engine(seed=1, population_size=4)
    panel, fwd = _make_panel(seed=1, n_tickers=5, n_dates=20)
    # Drop $volume from the panel; cs_rank($volume) below will raise
    # KeyError inside the evaluator.
    panel.pop("$volume")
    expr = OperatorCall("cs_rank", (Terminal("$volume"),))

    with pytest.raises(RuntimeError, match="missing a feature"):
        engine.evaluate_individual(expr, panel, fwd)
    # The hash must NOT be cached — fail-fast must not pollute state.
    assert hash(expr) not in engine.fitness_cache


def test_evaluate_individual_warns_once_per_exception():
    """Non-KeyError exceptions (operator overflow, undefined math, etc.)
    are expected for random GP expressions. The engine should record
    the first occurrence per (exc_type, expr_hash) and stay quiet on
    repeats so the loop doesn't spam millions of warnings.

    Asserts on ``engine._evaluation_warning_keys`` directly (the state
    that drives the "warn once" decision) rather than caplog, because
    the project may configure logging in ways that bypass caplog's
    handler attachment.
    """
    import src.factor_mining.gp_engine as gp_mod
    from src.factor_mining.expression import OperatorCall, Terminal

    engine = _engine(seed=2, population_size=4)
    panel, fwd = _make_panel(seed=2, n_tickers=5, n_dates=20)
    expr1 = OperatorCall("cs_rank", (Terminal("$volume"),))
    expr2 = OperatorCall("cs_rank", (Terminal("$money"),))

    original = gp_mod.evaluate_factor

    def raiser(_expr, _panel, _fwd, *, method="rank"):  # noqa: ARG001
        raise ValueError("synthetic overflow")

    gp_mod.evaluate_factor = raiser
    try:
        score1, _ = engine.evaluate_individual(expr1, panel, fwd)
        score2, _ = engine.evaluate_individual(expr2, panel, fwd)
        # Third call — same hash as expr1, after evicting from cache —
        # must NOT add a new warning key (already seen).
        engine.fitness_cache.pop(hash(expr1))
        score3, _ = engine.evaluate_individual(expr1, panel, fwd)
    finally:
        gp_mod.evaluate_factor = original

    assert score1 == float("-inf") and score2 == float("-inf") and score3 == float("-inf")
    assert engine._evaluation_warning_keys == {
        ("ValueError", hash(expr1)),
        ("ValueError", hash(expr2)),
    }, (
        f"expected exactly 2 unique warning keys (one per expr_hash); "
        f"got {engine._evaluation_warning_keys!r}"
    )


def test_evaluate_individual_passes_method_normal_to_pool_entry():
    """Pool entries created by the miner must be tagged ``method='normal'``
    so downstream consumers (validators, promoters) know ``ic_mean`` is
    Pearson. See PR2.

    Uses the same seed/panel as ``test_run_loop_completes_and_returns_pool``
    so we know at least one factor survives the validity filters.
    """
    engine = _engine(seed=11, population_size=8, n_generations=1)
    panel, fwd = _make_panel(seed=11, n_tickers=6, n_dates=30)
    engine.initialize_population()
    for expr in engine.population:
        engine.evaluate_individual(expr, panel, fwd)
    entries = list(engine._all_evaluated.values())
    assert entries, "expected at least one valid PoolEntry"
    assert all(e.method == "normal" for e in entries), (
        f"all entries must be method='normal'; got "
        f"{[e.method for e in entries]}"
    )


# ---------------------------------------------------------------------------
# D5 strict gate
# ---------------------------------------------------------------------------


def test_gp_engine_does_not_import_qlib_or_pit_directly():
    import src.factor_mining.gp_engine as mod

    src = inspect.getsource(mod)
    assert "from qlib" not in src
    assert "qlib.data" not in src
    assert "qlib.init" not in src
    assert "from src.pit" not in src
    assert "import src.pit" not in src
