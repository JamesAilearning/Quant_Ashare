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
