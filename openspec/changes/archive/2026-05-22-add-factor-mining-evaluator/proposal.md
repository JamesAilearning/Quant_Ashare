# Add Factor Mining Evaluator — PIT data view, IC/IR, fitness, factor pool

## Why

Phase 1 (`add-factor-mining-operators`, archived) built the
pure-Python foundations: operators, expression tree, grammar. Phase 2
is the first phase that touches data — and it MUST touch data only
through `src.pit.query.PITDataProvider`, per
`docs/factor_mining/factor_mining_claude_code_design.md` §1 ("The One
Rule That Matters Most") and `decisions.md` D5 (strict data gate).

The Phase 2 deliverables are the four files §6 of the design doc
lists for `add-factor-mining-evaluator`:

- `pit_adapter.py` — a thin `FactorMiningDataView` that loads the
  OHLCV panel and the forward-return label through `PITDataProvider`.
  This is the **only** file in `src/factor_mining/` that imports the
  PIT layer.
- `evaluator.py` — the expression walker (evaluates a Phase 1
  `Expression` against the loaded panel) and the IC / IR / RankIC /
  turnover / coverage metrics. Reuses `src.core._ic_utils.compute_ic_for_group`
  per `inventory.md` §B.3 recommendation.
- `fitness.py` — the composite fitness function per the original
  `factor_mining_design.md` §5.1 formula, with the v2 cost-rate
  weighting (`turnover_daily × 252 × cost_rate`, `cost_rate = 0.003`)
  per `decisions.md` D1.
- `factor_pool.py` — pool management with dedup-by-hash, novelty
  scoring via correlation, and parquet + JSON persistence per
  `factor_mining_design.md` §6.2.

It also writes `config/factor_mining/default.yaml` with the locked-in
`cost_rate = 0.003` from `decisions.md` D1 (the action item flagged
for "Phase 2 / Phase 3 task").

### Why this stays a separate phase

The v2 design doc structures factor mining as one OpenSpec change per
phase. Phase 1 was pure-Python correctness. Phase 2 introduces:

- The first PIT dependency in `src/factor_mining/` (concentrated in
  one adapter file).
- The first interaction with `src.core._ic_utils` (an inter-module
  reuse boundary).
- The first persistent artefact format (`factor_pool.parquet` +
  `factor_expressions.json`) that Phase 5's `MinedFactorHandler` will
  read.
- The first config schema (`config/factor_mining/default.yaml`).

Bundling all of this into one PR per the "one phase = one PR" rule
keeps each contract reviewable in isolation.

### Why the strict data gate still holds

D5 mandates zero matches of `qlib.data`, `qlib.init`, or `from qlib`
under `src/factor_mining/`. Phase 2's `pit_adapter.py` imports
`PITDataProvider` from `src.pit.query` — `src.pit` is the PIT door,
not qlib. The grep gate stays satisfied because qlib lives **behind**
`PITDataProvider` (which calls `init_qlib_canonical` internally per
`inventory.md` §A.2). Phase 2's `evaluator.py`, `fitness.py`, and
`factor_pool.py` do not import `src.pit` at all — only
`pit_adapter.py` does.

### Real-data acceptance is blocked on PIT-bundle availability

The design doc's Phase 2 acceptance criterion ("Known 20-day reversal
yields plausible IC (0.01–0.05) on PIT data") requires the PIT-corrected
qlib bin bundle to exist on disk. `inventory.md` §F.3 documents that
`my_cn_data_pit/` is **not on this machine yet** — the operator must
build it via `src/data/pit/qlib_bin_builder.py` before any
real-data check can run. Phase 2's CODE is fully implementable and
testable against a synthetic-data fixture (a hand-built mini-panel
with a known-IC reversal factor). The real-data verification is
documented in `tasks.md` as a follow-up operator task that gates Phase
3's smoke run. This is the same gating posture `inventory.md` §F.3
recommends.

## What Changes

- **Add `src/factor_mining/pit_adapter.py`** — `FactorMiningDataView`
  class: loads the six-field OHLCV panel through `PITDataProvider.get_features`,
  swaplevels from PIT's `(instrument, datetime)` MultiIndex to
  evaluator-friendly date × ticker DataFrames per field, exposes
  `forward_return(horizon)` (T+1 → T+1+horizon, `Ref($open, -h-1) /
  Ref($open, -1) - 1` per `decisions.md` D1 and
  `factor_mining_design.md` §5.3), and exposes `universe_mask()`.
- **Add `src/factor_mining/evaluator.py`** — `EvaluationResult`
  frozen dataclass; `evaluate_expression(expr, panel)` recursive
  walker that uses `REGISTRY.get(op).compute_fn` for `OperatorCall`
  nodes; `evaluate_factor(expr, panel, forward_return, *, method)`
  produces the full metric bundle (IC mean/std, IR, RankIC mean/std,
  rank-IR, daily turnover, coverage, n_obs). Uses
  `src.core._ic_utils.compute_ic_for_group` per `inventory.md` §B.3
  recommendation (single-method primitive reuse, no `SignalAnalyzer`
  or `FactorAnalyzer` dependency).
- **Add `src/factor_mining/fitness.py`** — `FitnessConfig` frozen
  dataclass (six weights + `cost_rate=0.003` from D1 + three validity
  thresholds); `compute_fitness(result, expr_size, novelty_penalty,
  config)` per `factor_mining_design.md` §5.1; `passes_validity(result,
  config)` per §5.2 hard constraints. Fitness for an invalid factor
  is `-inf` so selection never picks it.
- **Add `src/factor_mining/factor_pool.py`** — `PoolEntry` frozen
  dataclass; `FactorPool` class with `add(entry)` (dedup by structural
  hash), `__len__`, `__contains__(expr_hash)`, `top_k(k, by)`,
  `correlation_penalty(factor_values, existing_values)` (max
  Pearson correlation against pool members; for the novelty term in
  fitness), and `save(dir_path)` / `load(dir_path)` that write /
  read `factor_pool.parquet` + `factor_expressions.json` per v1
  §6.2.
- **Add `config/factor_mining/default.yaml`** — sets `cost_rate:
  0.003` and other fitness defaults; placeholders for
  `pit_provider_uri` and `delisted_registry_path` (operator fills in
  once PIT bundle is built per `inventory.md` §F.3).
- **MODIFIED requirement on `v2-factor-mining-foundations`** —
  extends the "Phase 1 SHALL NOT access qlib, PIT data, or any data
  source" requirement so it now allows `src/factor_mining/pit_adapter.py`
  to import `src.pit.query.PITDataProvider` (the PIT door is now the
  approved data path); the qlib direct-import ban remains absolute.
- **ADDED requirements** under `v2-factor-mining-foundations`
  (extending the spec) covering:
  - PIT adapter as the sole data-door
  - Forward-return formula (T+1 → T+1+horizon via `$open`)
  - Evaluator metric contract (IC / IR / RankIC / turnover /
    coverage)
  - Fitness formula + cost-rate (D1)
  - Validity filters (coverage, variance, sanity)
  - Factor pool persistence schema
  - Pool dedup-by-hash invariant

## Non-Goals

- **No GP engine, mutation, crossover, tournament selection, or
  population.** Deferred to Phase 3.
- **No GPU code.** Deferred to Phase 4, conditional.
- **No `MinedFactorHandler` registration.** Deferred to Phase 5.
- **No IS/OOS validator or promotion CLI.** Deferred to Phase 6.
- **No edit to `src.pit`, `src.data.pit`, `src.core._ic_utils`, or
  any other module.** Phase 2 is purely additive in `src/factor_mining/`,
  `tests/logic/factor_mining/`, and `config/factor_mining/`.
- **No real-data end-to-end verification.** The PIT bundle is not on
  disk per `inventory.md` §F.3. Tests use synthetic panels; the
  real-data IC-vs-contaminated delta check is a follow-up operator
  task and is documented in `tasks.md`.
- **No edits to `SignalAnalyzer` or `FactorAnalyzer`.** Phase 2's
  evaluator goes through `src.core._ic_utils.compute_ic_for_group`
  directly per `inventory.md` §B.3 recommendation (option 2). Adding
  `pit_provider` opt-in to `SignalAnalyzer` is a separate concern
  that does not need to be coupled to factor mining.
- **No `qlib.data.D` or `qlib.init` import anywhere in `src/factor_mining/`.**
  D5 strict gate remains binding; `pit_adapter.py` goes through
  `PITDataProvider`, which encapsulates the qlib init itself.
- **No SignalAnalyzer-style "DatasetH" wrapping.** Mined factors are
  per-expression panels; the evaluator computes IC directly on the
  panel without the DatasetH detour (per `inventory.md` §B.2).
