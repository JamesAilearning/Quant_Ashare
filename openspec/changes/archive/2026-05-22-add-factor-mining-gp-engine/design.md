# Design: GP Engine + Miner CLI (Phase 3)

> Long-form design at
> `docs/factor_mining/factor_mining_claude_code_design.md` §6 Phase 3
> row and `factor_mining_design.md` §4.4 genetic operations. The
> contract-level decisions surfaced into OpenSpec scope are below.

## Module additions (Phase 3 only)

```
src/factor_mining/
├── gp_engine.py     # GP loop: init, eval, select, cross, mutate
└── miner.py         # CLI orchestrator: config → panel → run → save

config/factor_mining/
└── smoke.yaml       # synthetic-data smoke config (sub-30s end-to-end)

tests/logic/factor_mining/
├── test_gp_engine.py
└── test_miner.py
```

No edits to Phase 1-2 source modules. Phase 3 imports them as a
stable upstream layer.

## GP Engine (`gp_engine.py`)

### `GPConfig` (frozen dataclass)

```python
@dataclass(frozen=True)
class GPConfig:
    population_size: int = 500
    n_generations: int = 50
    tournament_size: int = 3
    elite_frac: float = 0.05
    p_crossover: float = 0.7
    p_mutate_subtree: float = 0.15
    p_mutate_point: float = 0.10
    p_mutate_const: float = 0.05
    max_depth: int = 6
    min_depth: int = 2
    target_kind: str = "CSF"
    target_taint: str = "PURE"
    seed: int = 42
```

The target type is `ExprType("CSF", "PURE")` per Phase 1's root rule;
exposed as two strings on the config so YAML serialisation is
straightforward.

### `GenerationStats` (frozen dataclass)

```python
@dataclass(frozen=True)
class GenerationStats:
    gen: int
    best_fitness: float
    mean_fitness: float
    median_fitness: float
    n_unique: int          # distinct expr_hash count
    n_invalid: int         # count of fitness == -inf
    best_expr_str: str     # to_qlib_string() of the gen's best
```

### `GPEngine` class

Lifecycle:

1. `__init__(config, fitness_config)` — stores configs, seeds an
   internal `random.Random(config.seed)`.
2. `initialize_population()` — generates `population_size` random
   expressions via `grammar.random_expression(target_type,
   max_depth, min_depth, rng)`.
3. `evaluate_individual(expr, panel, fwd_ret) -> tuple[EvaluationResult,
   float]` — checks the fitness cache by `hash(expr)`; on miss,
   calls `evaluator.evaluate_factor(...)`, computes the novelty
   correlation against cached factor values, calls
   `fitness.compute_fitness(...)`. Caches both the result and the
   factor values (for novelty).
4. `select(evaluated) -> Expression` — tournament selection:
   `tournament_size` random individuals, return the one with the
   highest fitness. Ties broken by insertion order (deterministic).
5. `crossover(parent_a, parent_b) -> Expression` — type-preserving
   subtree exchange:
   - Enumerate all subtree positions in `parent_a`.
   - Pick one at random; note its output type.
   - Enumerate `parent_b`'s subtrees with matching output type.
   - If any: replace `parent_a`'s subtree with one of `parent_b`'s
     matching subtrees.
   - If none (no type-matching subtree in B): return `parent_a`
     unchanged.
   - The result is type-checked at construction (Phase 1
     `__post_init__`); any GrammarError is treated as a failed
     crossover and the parent is returned unchanged.
6. Three mutation operators:
   - `mutate_subtree(expr)`: pick a random subtree position, note
     its type, replace with a freshly generated random subtree of
     the same type (depth bounded by `max_depth - depth_at_position`).
   - `mutate_point(expr)`: pick a random Terminal in the tree,
     replace with a different Terminal of the same (kind, taint).
     ADJ_TAINTED feature → another ADJ_TAINTED feature; PURE feature
     → another PURE feature; INT_WINDOW literal → another from
     `WINDOW_LITERALS`.
   - `mutate_const(expr)`: like point mutation but restricted to
     INT_WINDOW literals (constant mutation is faster than full
     point mutation when only the window matters).
7. `next_generation(evaluated) -> list[Expression]`:
   - Sort `evaluated` by fitness descending.
   - `n_elite = max(1, int(elite_frac * population_size))`.
   - Carry the top `n_elite` to the next generation unchanged.
   - Fill the rest via select → crossover (with prob
     `p_crossover`) → one of the three mutations (with probs
     summing to ≤ 1; the residual is "do nothing").
   - Dedup by `hash(expr)` per generation; if dedup leaves the new
     generation short, generate fresh random expressions to fill.
8. `run(panel, fwd_ret, *, n_generations=None) -> FactorPool` —
   driver loop. After all generations, builds a `FactorPool` from
   the union of all evaluated individuals across generations whose
   fitness is finite.

### Crossover / mutation type-safety

All AST operations preserve the Phase 1 type contract by construction
— a new `OperatorCall(...)` is type-checked at `__post_init__` and
raises `GrammarError` on illegal taint combinations. The mutation /
crossover functions catch `GrammarError` and treat the attempt as a
no-op (returning the original parent). This is the type-safety
guarantee from `scale_invariance.md` §6.

### Determinism contract

Identical `seed` → identical:
- Initial population (random_expression with seeded rng).
- Tournament picks (rng.choice over indices).
- Crossover / mutation choices (rng.choice for subtree positions and
  new content).
- Per-generation outputs (because all randomness flows through the
  single `random.Random(seed)`).
- Final FactorPool entries (because hash dedup is deterministic and
  fitness arithmetic is deterministic on the same panel + fwd_ret).

### Checkpoint contract

`save_checkpoint(path)` writes:
- `gp_state.json` — config (as dict), generation index, RNG state
  (`rng.getstate()`), fitness cache keys (we cannot pickle the
  factor_values cache since DataFrames don't round-trip JSON cheaply
  — the factor_values cache is rebuilt on first eval after resume).
- `population.json` — list of current population expressions
  (`Expression.to_dict`).
- `gp_history.json` — list of `GenerationStats` as dicts.

`GPEngine.load_checkpoint(path, *, fitness_config)` reads the above
and reconstructs an engine instance. The resumed engine continues from
the saved generation index; the fitness cache (for `hash → float`) is
restored from `gp_state.json`, but factor_values are not (they are
recomputed lazily on the next evaluation that needs them).

Tolerance: a fully-continuous run and a kill-resume run should produce
identical FactorPool contents if all fitness recomputations land
within `1e-12` of cached values (they should, since arithmetic is
deterministic). The acceptance test verifies entry-by-entry equality.

## Miner CLI (`miner.py`)

### `MinerConfig` (frozen dataclass)

Holds parsed YAML config:

```python
@dataclass(frozen=True)
class DataConfig:
    mode: str                       # "synthetic" | "pit"
    # synthetic mode:
    synthetic_n_tickers: int = 30
    synthetic_n_dates: int = 100
    synthetic_seed: int = 1234
    # pit mode:
    pit_provider_uri: str = ""
    delisted_registry_path: str = ""
    universe_name: str = "csi300"
    start_date: str = "2018-01-01"
    end_date: str = "2025-12-31"
    forward_horizon: int = 1

@dataclass(frozen=True)
class MinerConfig:
    data: DataConfig
    gp: GPConfig
    fitness: FitnessConfig
    output_dir: Path
    run_id: str | None = None       # autogenerated if None
```

### `build_panel(config) -> tuple[dict, DataFrame]`

Branching:
- `mode == "synthetic"`: builds a deterministic synthetic OHLCV
  panel (random walk closes seeded by `synthetic_seed`; volume /
  money as plausible-magnitude lognormals) and a synthetic
  forward-return panel (a noisy linear combination of price /
  volume features so a non-trivial GP search finds a meaningful
  factor).
- `mode == "pit"`: constructs `PITDataProvider(pit_provider_uri,
  delisted_registry_path)` and wraps it in a `FactorMiningDataView`;
  returns `view.load_panel()` and `view.forward_return(horizon)`.
  Raises `ValueError` if `pit_provider_uri` or
  `delisted_registry_path` is empty (per the default.yaml comment
  re: operator-fill).

### `run_mining(config) -> RunResult`

Orchestrates:
1. `panel, fwd_ret = build_panel(config)`.
2. `engine = GPEngine(config.gp, config.fitness)`.
3. `pool = engine.run(panel, fwd_ret)`.
4. Determine `run_id` (autogen if missing —
   `YYYYMMDDTHHmmss-<seed>` is the format).
5. Create `output_dir/runs/{run_id}/` and save:
   - `factor_pool.parquet` + `factor_expressions.json` (via
     `pool.save`).
   - `gp_history.json` (list of `GenerationStats` dicts).
   - `config.yaml` (the parsed config dumped back, for
     reproducibility).
6. Returns `RunResult(run_id, output_dir, pool, history)`.

### `__main__` (CLI entry)

```python
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Factor Mining GP search")
    parser.add_argument("config", help="path to YAML config")
    args = parser.parse_args()
    result = run_mining(load_config(Path(args.config)))
    print(f"Run complete: {result.run_id} | pool size: {len(result.pool)}")
```

Invocation:

```
python -m src.factor_mining.miner config/factor_mining/smoke.yaml
```

### Same-seed-identical-output acceptance

The acceptance test:
1. Invokes `run_mining(config)` twice with identical config (and
   identical seed).
2. Compares the resulting `FactorPool`s: same set of expression
   hashes, same fitness values per expression, same `GenerationStats`
   per generation.

If both runs land identical, the determinism contract holds.

## Smoke config (`smoke.yaml`)

```yaml
run_id: null                          # autogen if null
output_dir: "research/mined_factors"

data:
  mode: synthetic
  synthetic_n_tickers: 30
  synthetic_n_dates: 100
  synthetic_seed: 1234

gp:
  population_size: 20
  n_generations: 3
  tournament_size: 3
  elite_frac: 0.05
  p_crossover: 0.7
  p_mutate_subtree: 0.15
  p_mutate_point: 0.10
  p_mutate_const: 0.05
  max_depth: 4
  min_depth: 2
  target_kind: "CSF"
  target_taint: "PURE"
  seed: 42

fitness:
  w_ic: 1.0
  w_ir: 0.5
  w_rankic: 0.5
  w_turnover: 0.2
  w_corr: 0.8
  w_complexity: 0.01
  cost_rate: 0.003
  coverage_min: 0.8
  variance_days_frac_min: 0.7
  variance_min: 1.0e-6
  extreme_outlier_frac_max: 0.05
  extreme_outlier_magnitude: 1.0e8
```

A smoke run with this config completes in well under 30 seconds on
CPU and produces a factor pool of 1–20 unique factors.

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Crossover produces ill-typed expressions | Catch GrammarError, treat as no-op; the type-checker at `__post_init__` blocks all illegal exchanges |
| Mutation introduces ADJ_TAINTED root via `cs_*` slot | The mutation operators only replace subtrees with same-type subtrees; the cs_* gate is enforced at every replacement |
| Non-determinism from dict iteration / pandas ops | Single `random.Random(seed)` for all GP randomness; dict insertion order is preserved (Python 3.7+); pandas arithmetic is deterministic |
| Same-seed runs differ across OS | Phase 3 only commits to determinism on the same machine; cross-OS determinism is a separate concern (and not promised by `factor_mining_claude_code_design.md`) |
| Population collapses (dedup leaves nothing) | After dedup, top up with fresh `random_expression` calls (not part of select → cross → mutate; just to keep population size constant) |
| Checkpoint resume fails because factor_values cache is rebuilt | Acceptance test reloads and re-runs the rest of the generations; if the recomputation produces the same factor hash and (within 1e-12) the same fitness, the resume is correct |
| Synthetic data IC is too noisy / too clean | The synthetic forward-return is constructed as a noisy linear combination of $close, $volume, $money; the GP can find a factor with finite positive fitness but not "obvious" overfitting |
| miner CLI lives in src/ but is "scripts-like" | Per AGENTS.md and the design doc §3.1, the CLI sits next to the engine in `src/factor_mining/` (importable from the training pipeline if needed); a `scripts/` thin wrapper is not required |
