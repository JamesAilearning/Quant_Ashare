# Tasks: Factor Mining GP Engine + Miner CLI (Phase 3)

## OpenSpec (propose stage)

- [x] Draft `proposal.md`
- [x] Draft `design.md`
- [x] Draft `tasks.md` (this file)
- [x] Draft `specs/v2-factor-mining-foundations/spec.md` deltas
      (MODIFIED data-gate scope; ADDED GP engine surface,
      tournament + elitism, type-preserving crossover, three
      mutation operators, determinism contract, checkpoint contract,
      miner CLI contract)
- [x] `openspec validate add-factor-mining-gp-engine --strict` — green

## Phase 3 Implementation

### 3.1 `src/factor_mining/gp_engine.py`
- [x] `GPConfig` and `GenerationStats` frozen dataclasses
- [x] `GPEngine.__init__(gp_config, fitness_config)` with seeded RNG
- [x] `initialize_population()` using `grammar.random_expression`
- [x] `evaluate_individual(expr, panel, fwd_ret)` with fitness cache
      (hash → score) and factor_values cache (for novelty term)
- [x] `select(evaluated)` tournament size k=3 with deterministic
      tie-breaking
- [x] `crossover(parent_a, parent_b)` — type-preserving subtree
      exchange; GrammarError → no-op (return parent_a)
- [x] `mutate_subtree(expr)` — replace random subtree with new
      random subtree of same type
- [x] `mutate_point(expr)` — Terminal swap within same (kind, taint)
- [x] `mutate_const(expr)` — INT_WINDOW literal swap
- [x] `next_generation(evaluated)` — elitism + select + cross +
      mutate + per-gen dedup + top-up to keep population size
- [x] `run(panel, fwd_ret, n_generations=None)` loop, returns final
      FactorPool of valid-fitness entries across all generations
- [x] `save_checkpoint(path)` / `GPEngine.load_checkpoint(path, *,
      fitness_config)` — JSON serialisation of config, gen index,
      RNG state, fitness cache, population, history
- [x] Subtree helpers `_enumerate_positions`, `_get_subtree`,
      `_replace_subtree` (path = tuple of child indices)

### 3.2 `src/factor_mining/miner.py`
- [x] `DataConfig` and `MinerConfig` frozen dataclasses
- [x] `load_config(path)` reads YAML and constructs MinerConfig +
      `GPConfig` + `FitnessConfig`
- [x] `build_panel(config)` branches on `data.mode`:
      - synthetic: deterministic OHLCV panel + noisy forward return
      - pit: `FactorMiningDataView` over `PITDataProvider`; rejects
        empty `pit_provider_uri` / `delisted_registry_path`
- [x] `run_mining(config) -> RunResult` orchestrates panel + engine
      + save
- [x] Output layout: `output_dir/runs/{run_id}/` containing
      `factor_pool.parquet`, `factor_expressions.json`,
      `gp_history.json`, `config.yaml`
- [x] `if __name__ == "__main__"` argparse CLI entry

### 3.3 `config/factor_mining/smoke.yaml`
- [x] Synthetic data: 30 tickers × 100 dates, seed=1234
- [x] GP: pop=20, gen=3, tournament=3, elitism=0.05, crossover=0.7
- [x] Fitness: defaults from Phase 2's `FitnessConfig`

## Tests

### 3.t1 `tests/logic/factor_mining/test_gp_engine.py`
- [x] `test_initial_population_size` — N expressions, all CSF/PURE
- [x] `test_initial_population_deterministic_with_seed` — same seed
      → identical population
- [x] `test_enumerate_positions_terminal_is_single_position`
- [x] `test_enumerate_positions_nested_yields_paths`
- [x] `test_get_subtree_and_replace_subtree_round_trip` —
      `_replace_subtree(e, p, _get_subtree(e, p)) == e`
- [x] `test_replace_subtree_at_root` — path=() replaces whole tree
- [x] `test_crossover_type_preserving_swap` — two parents, swap a
      matching-type subtree, result type-checks
- [x] `test_crossover_no_matching_type_returns_parent_unchanged`
- [x] `test_mutate_subtree_changes_some_node`
- [x] `test_mutate_point_swaps_terminal_in_same_taint`
- [x] `test_mutate_const_swaps_window_literal`
- [x] `test_tournament_selection_returns_best_of_sampled`
- [x] `test_next_generation_preserves_elites`
- [x] `test_next_generation_dedups_per_gen`
- [x] `test_run_loop_improves_or_holds_fitness` — best fitness in
      final gen >= best fitness in initial gen (loose convergence)
- [x] `test_run_loop_deterministic_with_seed` — two runs same seed
      → identical pool entries (hash + fitness)
- [x] `test_checkpoint_round_trip` — run k gens → save → load → run
      remaining gens; compare to a continuous run of (k + remaining)
      gens; final pool must be entry-identical

### 3.t2 `tests/logic/factor_mining/test_miner.py`
- [x] `test_load_config_parses_smoke_yaml`
- [x] `test_build_panel_synthetic_is_deterministic_with_seed`
- [x] `test_build_panel_pit_mode_rejects_empty_uris`
- [x] `test_run_mining_writes_expected_files` — verify
      factor_pool.parquet, factor_expressions.json, gp_history.json,
      config.yaml exist
- [x] `test_run_mining_same_seed_identical_output` — two runs of the
      smoke config produce identical pool + history
- [x] `test_miner_cli_smoke` — invoke via subprocess
      `python -m src.factor_mining.miner ...smoke.yaml`; exit 0;
      output files present

## Validation

- [x] `pytest tests/logic/factor_mining/ -v` — all green
- [x] `ruff check src/factor_mining/ tests/logic/factor_mining/` — green
- [x] `python -c "import src.factor_mining.gp_engine, src.factor_mining.miner"` — succeeds
- [x] `grep -rn "qlib\.data\|qlib\.init\|from qlib" src/factor_mining/` — zero matches (D5 strict)
- [x] `openspec validate add-factor-mining-gp-engine --strict` — green
- [x] `python -m src.factor_mining.miner config/factor_mining/smoke.yaml` runs end-to-end on CPU and writes the expected outputs

## Phase Gate

Phase 4 (GPU) does NOT begin unless:

- Phase 3 ships green on `main` and the synthetic smoke runs cleanly.
- CPU speed is the measured bottleneck (per design doc §6 Phase 4
  "OPTIONAL... only if CPU is the bottleneck"). For this project
  scope, Phase 4 is OPTIONAL and SKIPPED by the autonomous-mode
  delivery: the smoke run is fast enough on CPU, and the GPU
  contract (CPU/GPU diff < 1e-5) adds substantial complexity
  without proportional value for the v1 system. The decision is
  documented in this tasks.md and re-asserted in `decisions.md`'s
  carry-over notes if needed.

Phase 5 (`add-mined-factor-handler`) MAY begin after Phase 3 archives,
since it does not require Phase 4.

## Deferred (NOT this proposal)

- Phase 4: GPU kernels (skipped per Phase Gate above).
- Phase 5: `MinedFactorHandler` registration into
  `v2-feature-handler-registry`.
- Phase 6: IS/OOS validator, promotion CLI, walk-forward integration,
  user-facing docs in `docs/factor_mining/user_guide.md`.
- Pareto multi-objective optimisation, parallel/distributed
  evaluation, mutation-niche penalties beyond the Phase 2 novelty
  correlation term.
- Real-data PIT smoke run — operator follow-up per Phase 2
  tasks.md, blocked on PIT bundle build.
- `.githooks/pre-commit` D5 grep guard wiring — operator task.
