# Tasks: Factor Mining Evaluator (Phase 2)

## OpenSpec (propose stage)

- [x] Draft `proposal.md` (Why / What Changes / Non-Goals)
- [x] Draft `design.md` (PIT adapter contract; evaluator metric contract;
      fitness formula + D1 cost; pool persistence schema)
- [x] Draft `tasks.md` (this file)
- [x] Draft `specs/v2-factor-mining-foundations/spec.md` deltas
      (ADDED requirements for adapter, evaluator, fitness, pool;
      MODIFIED Phase-1 no-data requirement to allow pit_adapter.py)
- [x] `openspec validate add-factor-mining-evaluator --strict` — green

## Phase 2 Implementation

### 2.1 `src/factor_mining/pit_adapter.py`
- [x] `FactorMiningDataView` class with constructor (provider, start, end,
      universe_name, instruments, fields)
- [x] `load_panel() -> dict[str, pd.DataFrame]` — calls
      `PITDataProvider.get_features` once, swaplevels & unstacks per
      field into date × ticker DataFrames
- [x] `forward_return(horizon: int = 1) -> pd.DataFrame` —
      `Ref($open, -h-1) / Ref($open, -1) - 1` via `get_features`
      (qlib expression string), then swaplevels & unstacks
- [x] `universe_mask() -> pd.DataFrame` — boolean date × ticker, True
      where ticker is in the universe set on that date
- [x] No `from qlib import …`, no `qlib.data`, no `qlib.init`.
      Only `from src.pit.query import PITDataProvider`.

### 2.2 `src/factor_mining/evaluator.py`
- [x] `evaluate_expression(expr, panel) -> DataFrame | int` — recursive
      walker; Terminal → `panel[name]` or `int(name)`; OperatorCall →
      `REGISTRY.get(op).compute_fn(*children)`
- [x] `EvaluationResult` frozen dataclass (factor_values, ic_mean,
      ic_std, ir, rank_ic_mean, rank_ic_std, rank_ir, turnover_daily,
      coverage, n_obs_per_day_min)
- [x] `evaluate_factor(expr, panel, forward_return, *, method="rank")`
      — orchestrates: walk → align → stack → groupby date → IC per
      day → IR; computes turnover and coverage
- [x] Uses `src.core._ic_utils.compute_ic_for_group` per inventory §B.3
- [x] IR is NaN when std < 1e-9 (per inventory §B.4 convention)
- [x] No `from qlib import …`, no `src.pit` import

### 2.3 `src/factor_mining/fitness.py`
- [x] `FitnessConfig` frozen dataclass with six weights + cost_rate
      (0.003 from D1) + three validity thresholds + extreme_outlier_frac_max
- [x] `passes_validity(result, config) -> bool` — coverage, variance,
      sanity (Phase 1 grammar already enforces no-leakage)
- [x] `compute_fitness(result, expr_size, novelty_penalty, config) -> float`
      — v1 §5.1 formula with D1 annualised cost; returns `-inf` for
      invalid factors
- [x] No `from qlib import …`, no `src.pit` import

### 2.4 `src/factor_mining/factor_pool.py`
- [x] `PoolEntry` frozen dataclass (expr + 9 metric scalars +
      expr_hash + expr_size)
- [x] `FactorPool` class: `add(entry) -> bool` (dedup by hash),
      `__len__`, `__contains__(expr_hash)`, `all_entries()`,
      `top_k(k, by)`, `correlation_with(factor_values, panel) -> float`
- [x] `save(dir_path)` writes `factor_pool.parquet` (metrics) +
      `factor_expressions.json` (AST as dict per expr_hash)
- [x] `FactorPool.load(dir_path)` round-trips structurally
- [x] No `from qlib import …`, no `src.pit` import

### 2.5 `config/factor_mining/default.yaml`
- [x] `data.*` (pit_provider_uri / delisted_registry_path placeholders;
      universe, dates, features, forward_horizon)
- [x] `fitness.*` (weights + cost_rate=0.003)
- [x] `compute.*` (backend=cpu)

## Tests

### 2.t1 `tests/logic/factor_mining/test_pit_adapter.py`
- [x] `_StubPITProvider` fixture with same `get_features` signature
      as the real class
- [x] `test_load_panel_returns_dict_per_field` — verifies shape +
      keys
- [x] `test_load_panel_pivots_from_multiindex` — verifies swaplevel +
      unstack to date × ticker
- [x] `test_forward_return_uses_open_open_formula` — verifies the
      expression passed to `get_features` matches D1
- [x] `test_universe_mask_boolean_shape`
- [x] `test_no_qlib_import` — import smoke + grep
- [x] `test_stub_matches_real_PITDataProvider_signature` — guards
      against drift via `inspect.signature`

### 2.t2 `tests/logic/factor_mining/test_evaluator.py`
- [x] `test_evaluate_expression_terminal_window` — Terminal("20")
      returns int 20
- [x] `test_evaluate_expression_terminal_feature` — Terminal("$close")
      returns the panel's `$close` DataFrame
- [x] `test_evaluate_expression_nested_op` — hand-built expression
      evaluates to expected DataFrame
- [x] `test_evaluate_factor_ic_on_synthetic` — synthetic factor
      perfectly correlated with synthetic forward-return yields
      IC ≈ 1.0
- [x] `test_evaluate_factor_ir_nan_on_constant_ic` — std=0 → IR=NaN
- [x] `test_evaluate_factor_turnover_static_factor_is_zero` — a
      factor that never changes day-to-day has turnover 0
- [x] `test_evaluate_factor_coverage_excludes_nan_cells`

### 2.t3 `tests/logic/factor_mining/test_fitness.py`
- [x] `test_fitness_invalid_coverage_is_neg_inf`
- [x] `test_fitness_invalid_variance_is_neg_inf`
- [x] `test_fitness_invalid_sanity_is_neg_inf`
- [x] `test_fitness_passing_factor_has_finite_score`
- [x] `test_fitness_cost_term_uses_annualised_rate` — turnover term
      = `w_turnover * (turnover_daily * 252 * cost_rate)`
- [x] `test_fitness_novelty_penalty_subtracts_w_corr`
- [x] `test_fitness_complexity_subtracts_w_complexity_times_size`
- [x] `test_fitness_config_default_cost_rate_matches_d1` — 0.003

### 2.t4 `tests/logic/factor_mining/test_factor_pool.py`
- [x] `test_pool_add_returns_true_for_new_entry`
- [x] `test_pool_add_returns_false_for_duplicate_hash`
- [x] `test_pool_dedup_is_commutative_aware` — `add($volume, $money)`
      vs `add($money, $volume)` are dedup'd (Phase 1 commutative hash)
- [x] `test_pool_top_k_orders_by_fitness`
- [x] `test_pool_correlation_with_empty_pool_returns_zero`
- [x] `test_pool_correlation_with_populated_pool_returns_max_abs`
- [x] `test_pool_save_load_round_trip` — write to tmp_path, read,
      assert structural equality
- [x] `test_pool_persistence_files` — verifies `factor_pool.parquet`
      and `factor_expressions.json` are written

## Validation

- [x] `pytest tests/logic/factor_mining/ -v` — all green (Phase 1
      + Phase 2 tests)
- [x] `ruff check src/factor_mining/ tests/logic/factor_mining/` — green
- [x] `python -c "import src.factor_mining.pit_adapter; import
      src.factor_mining.evaluator; import src.factor_mining.fitness;
      import src.factor_mining.factor_pool"` — succeeds
- [x] `grep -rn "qlib\.data\|qlib\.init\|from qlib" src/factor_mining/`
      — zero matches (D5 strict gate, unchanged from Phase 1)
- [x] `openspec validate add-factor-mining-evaluator --strict` — green

## Follow-up (operator task, GATES Phase 3 smoke)

- [ ] Operator builds the PIT bundle via
      `src/data/pit/qlib_bin_builder.py` (per `inventory.md` §F.3) and
      records the output path.
- [ ] Operator updates `config/factor_mining/default.yaml` with the
      real `pit_provider_uri` and `delisted_registry_path`.
- [ ] Operator runs the 20-day reversal hand-built factor
      (`cs_rank(div_safe(ts_delta($close, 20), $close))`) end-to-end
      through `FactorMiningDataView` + `evaluate_factor` on a small
      universe (e.g. csi300, 2023-01-01..2024-12-31).
- [ ] Operator documents the IC mean / IR / coverage in a comment on
      the Phase 3 smoke PR.
- [ ] Operator (optional, per inventory §F.5) wires the D5 grep
      guard into `.githooks/pre-commit` so the strict gate is enforced
      on commit (not just in CI).

## Phase Gate

Phase 3 (`add-factor-mining-gp-engine`) does NOT begin until:

- This change is archived to `openspec/specs/v2-factor-mining-foundations/spec.md`.
- All Phase 2 tests in `tests/logic/factor_mining/` are green on `main`.
- The operator confirms the real-data IC plausibility check has been
  done (synthetic-data tests in this PR establish the code contract;
  real-data validation is the Phase-2-to-Phase-3 gate).

## Deferred (NOT this proposal)

- Phase 3: `gp_engine.py`, `miner.py`, mutation, crossover, tournament,
  CLI orchestrator, smoke config.
- Phase 4: GPU kernels.
- Phase 5: `MinedFactorHandler`, registry registration into
  `v2-feature-handler-registry`.
- Phase 6: IS/OOS validator, promotion CLI, walk-forward integration,
  user-facing docs.
- `research/mined_factors/{runs,candidates,production}/` directory
  scaffold (Phase 5 task per D4).
- D5 grep guard wired into `.githooks/pre-commit` (operator task above).
- `SignalAnalyzer.pit_provider` opt-in patch (separate concern; not
  factor mining per `inventory.md` §F.5).
