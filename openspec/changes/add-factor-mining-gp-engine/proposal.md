# Add Factor Mining GP Engine — search loop + CLI orchestrator

## Why

Phase 2 (`add-factor-mining-evaluator`, archived) built the metric
infrastructure: PIT adapter, evaluator, fitness, factor pool. Phase 3
adds the **search loop** that turns those building blocks into a
factor miner.

Per `docs/factor_mining/factor_mining_claude_code_design.md` §6 Phase
3 table:

- 3.1 `gp_engine.py` — initial population, tournament selection,
  type-preserving subtree crossover, three mutation operators
  (subtree / point / constant), elitism, per-generation hash dedup.
- 3.2 Logging + checkpoint — the GP engine writes per-generation
  stats and supports save_checkpoint / resume so a long run can be
  killed mid-way and continued.
- 3.3 `miner.py` orchestrator + smoke.yaml — `python -m
  src.factor_mining.miner <config.yaml>` runs end-to-end on CPU;
  same seed → identical output.

It also adds `config/factor_mining/smoke.yaml` (small synthetic-data
run for quick verification) and scaffolds the run-output directory
contract for Phase 5's mined-factor handler:

```
research/mined_factors/
└── runs/{run_id}/
    ├── factor_pool.parquet
    ├── factor_expressions.json
    └── gp_history.json
```

### Why this stays a separate phase

Phase 3 is the first phase that **uses** the Phase 1+2 layer in a
loop. It is also the first phase to introduce GP-specific
abstractions (selection, crossover, mutation, elitism) that the
later phases (Phase 4 GPU, Phase 5 handler, Phase 6 validator)
consume but do not extend. Bundling Phase 3 with Phase 2 would
couple the loop concerns to the metric concerns; keeping them
separate lets each be reviewed in isolation per the "one phase = one
OpenSpec change = one PR" rule.

### Why CPU only

Per `factor_mining_claude_code_design.md` §0.3 ("Guardrails") and
§6 Phase 4 (OPTIONAL), GPU code is deferred to Phase 4 and only if
Phase 1-3 are green AND CPU is the measured bottleneck. Phase 3
ships a CPU-only loop; benchmarking against the GPU target is a
Phase 4 concern.

### Why synthetic-data smoke

The PIT bundle is still not on disk (per `inventory.md` §F.3, and
Phase 2's tasks.md flagged this as an operator follow-up). The Phase
3 smoke configuration uses a synthetic in-memory panel so the CLI is
end-to-end runnable without the PIT bundle. The same `miner.py` CLI
can target a real PIT bundle by setting `data.mode: pit` in the
config and filling in `pit_provider_uri` / `delisted_registry_path`
per `config/factor_mining/default.yaml`.

## What Changes

- **Add `src/factor_mining/gp_engine.py`** —
  - `GPConfig` frozen dataclass (population_size, n_generations,
    tournament_size, elite_frac, p_crossover, p_mutate_subtree,
    p_mutate_point, p_mutate_const, max_depth, min_depth,
    target_type, seed).
  - `GenerationStats` frozen dataclass (gen, best_fitness,
    mean_fitness, median_fitness, n_unique, n_invalid).
  - `GPEngine` class: `initialize_population()`,
    `evaluate_individual(expr, panel, fwd_ret)` with fitness cache,
    `select(evaluated)` tournament k=3, `crossover(a, b)`
    type-preserving subtree exchange,
    `mutate_subtree(expr)`, `mutate_point(expr)`,
    `mutate_const(expr)`, `next_generation(evaluated)`,
    `run(panel, fwd_ret, n_generations=None)`,
    `save_checkpoint(path)` / `GPEngine.load_checkpoint(path,
    config?)`.
  - Subtree helpers: `_enumerate_positions(expr)`,
    `_get_subtree(expr, path)`, `_replace_subtree(expr, path,
    new_subtree)` operating on the Phase 1 immutable AST.
  - Per-generation hash dedup + correlation-based novelty term fed
    into Phase 2's `compute_fitness`.

- **Add `src/factor_mining/miner.py`** —
  - `MinerConfig` frozen dataclass (data spec, GP config, fitness
    config, output dir).
  - `load_config(path)` reads YAML and constructs the typed config
    tree.
  - `build_panel(config)` constructs the OHLCV panel + forward
    return: either synthetic (deterministic with seed) or via
    `FactorMiningDataView` (real PIT path).
  - `run_mining(config)` orchestrates: build panel → instantiate
    GP engine → run loop → save factor pool + gp history.
  - `__main__` block: argparse on `[config_path]`, invokes
    `run_mining`.

- **Add `config/factor_mining/smoke.yaml`** — synthetic-data smoke
  configuration: 30 tickers × 100 dates × float seed=1234,
  population_size=20, n_generations=3. Designed for a sub-30-second
  end-to-end run, producing a small factor pool.

- **MODIFY canonical spec `v2-factor-mining-foundations`** —
  extends the "Phase 1 SHALL NOT access qlib …" requirement (the
  current data-gate statement) so the rule explicitly covers Phase 3
  modules (`gp_engine.py` and `miner.py`): neither MAY import qlib
  directly, and only `miner.py`'s synthetic-data path or its
  PIT-mode delegation to `FactorMiningDataView` are permitted data
  sources. Note that the qlib direct-import ban remains absolute for
  the entire subpackage.

- **ADDED requirements** under `v2-factor-mining-foundations`:
  - GP engine surface (`GPEngine`, `GPConfig`, `GenerationStats`)
    and the tournament + elitism + dedup contract.
  - Type-preserving crossover (subtree exchange across the same
    `ExprType`).
  - Three mutation operators (subtree, point, constant) and their
    respective scope.
  - Determinism: identical `seed` produces identical population /
    history / final pool.
  - Checkpoint round-trip contract (kill + resume == continuous
    run, modulo within-tolerance floating-point equivalence in the
    fitness cache when reloaded).
  - Miner CLI: `python -m src.factor_mining.miner <config>` runs
    end-to-end and writes `factor_pool.parquet` +
    `factor_expressions.json` + `gp_history.json` under
    `<output_dir>/runs/{run_id}/`.

## Non-Goals

- **No GPU code.** Phase 4, conditional.
- **No `MinedFactorHandler` registration.** Phase 5.
- **No IS/OOS validator or promotion CLI.** Phase 6.
- **No edits to Phase 1-2 source modules** (`__init__`, `operators`,
  `expression`, `grammar`, `pit_adapter`, `evaluator`, `fitness`,
  `factor_pool`). Phase 3 imports them as a stable upstream layer.
- **No edits to `src.pit`, `src.data.pit`, `src.core._ic_utils`,
  `src.data.feature_dataset_builder`.** Phase 3 is additive.
- **No real-data PIT smoke run on this machine.** Per
  `inventory.md` §F.3, the PIT bundle is not on disk yet. The Phase
  3 smoke config uses synthetic data; the PIT-mode code path is
  exercised by `pit_adapter` tests and a `FactorMiningDataView`
  integration test that uses a stub provider.
- **No mutation-niche penalty beyond Phase 2's novelty correlation
  term.** v1 §4.4 mentions "diversity: hash dedup per generation +
  correlation niche penalty"; the correlation niche penalty is the
  Phase 2 novelty term inside `compute_fitness`. Phase 3 wires
  that, no new mechanism.
- **No multi-objective Pareto frontier.** v1 design §5 mentions
  Pareto as a v2 extension; v1 uses the scalar composite fitness.
- **No parallel / distributed evaluation.** A multiprocessing pool
  is a clean optimisation but adds non-determinism risk; deferred
  to Phase 4 if performance demands it.
- **No edits to `.githooks/pre-commit`.** Wiring the D5 grep guard
  into the hook stays an operator task per `decisions.md` D5 action
  items.
