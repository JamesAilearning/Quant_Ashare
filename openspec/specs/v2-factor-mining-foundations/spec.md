# v2-factor-mining-foundations Specification

## Purpose
TBD - created by archiving change add-factor-mining-operators. Update Purpose after archive.
## Requirements
### Requirement: Factor-mining foundations SHALL live under `src/factor_mining/`

The factor-mining subsystem's foundation code (operator library, expression tree, grammar / type system, random expression generator) SHALL live under `src/factor_mining/` as a production-layer Python subpackage. The Phase 1 surface SHALL consist of exactly four source modules — `__init__.py`, `operators.py`, `expression.py`, `grammar.py` — and SHALL NOT introduce code at `research/factor_lab/` (which remains a research-only placeholder per `v2-project-skeleton-boundaries`).

#### Scenario: maintainers inspect the Phase 1 source surface
- **WHEN** maintainers inspect `src/factor_mining/`
- **THEN** the subpackage contains `__init__.py`, `operators.py`, `expression.py`, and `grammar.py`
- **AND** no Phase 2+ files (`pit_adapter.py`, `evaluator.py`, `fitness.py`, `factor_pool.py`, `gp_engine.py`, `miner.py`, `gpu_compute.py`, `validator.py`, `promote.py`) exist yet

#### Scenario: a researcher attempts to place factor code under research/factor_lab/
- **WHEN** a contributor introduces operator, expression, or grammar code under `research/factor_lab/`
- **THEN** the change is rejected by review
- **AND** the contributor is directed to `src/factor_mining/` per Phase 0 outcome O1 in `docs/factor_mining/decisions.md`

### Requirement: Phase 1 SHALL NOT access qlib, PIT data, or any data source

Code under `src/factor_mining/` SHALL NOT import from `qlib`, SHALL NOT call `qlib.init`, and SHALL NOT reference `qlib.data.D`. A repository-wide grep for `qlib\.data`, `qlib\.init`, or `from qlib` under `src/factor_mining/` MUST return zero matches. The PIT layer SHALL be reached only through `src/factor_mining/pit_adapter.py`, which is the designated data door; only `pit_adapter.py` MAY import `PITDataProvider` from `src.pit.query`. Other modules under `src/factor_mining/` (including Phase 3's `gp_engine.py` and `miner.py`) SHALL NOT import `src.pit` directly. `miner.py` MAY consume PIT data only via `FactorMiningDataView` instances it constructs around `PITDataProvider` (i.e. the PIT entry point is still `pit_adapter.py`). This requirement extends the original Phase 1 strict gate D5 (from `docs/factor_mining/decisions.md`) to cover Phase 2-3 modules; the qlib direct-import ban remains absolute for the entire subpackage.

#### Scenario: a developer runs the strict data-gate grep
- **WHEN** a developer runs `grep -rn "qlib\.data\|qlib\.init\|from qlib" src/factor_mining/`
- **THEN** the output is empty (zero matches)
- **AND** any non-empty output is treated as a scope violation, not an acceptable exception

#### Scenario: gp_engine.py or miner.py attempts to import src.pit directly
- **WHEN** a module under `src/factor_mining/` other than `pit_adapter.py` adds `from src.pit.query import …` or `import src.pit`
- **THEN** the change is rejected at review
- **AND** the reviewer directs the contributor to route the call through `FactorMiningDataView` in `pit_adapter.py`

#### Scenario: miner.py wraps a PITDataProvider
- **WHEN** a reviewer inspects `src/factor_mining/miner.py`'s PIT-mode branch
- **THEN** the file constructs `FactorMiningDataView` and consumes its `load_panel()` / `forward_return()` outputs
- **AND** the file does NOT import or call `PITDataProvider.get_features` directly

### Requirement: Operator library SHALL provide 28 CPU operators excluding `ts_cov`

The operator library SHALL implement exactly the 28 operators enumerated in `docs/factor_mining/scale_invariance.md` §4: four arithmetic (`add`, `sub`, `mul`, `div_safe`); five unary (`neg`, `abs`, `sign`, `log_safe`, `sqrt_safe`); fourteen time-series (`ts_mean`, `ts_std`, `ts_max`, `ts_min`, `ts_sum`, `ts_delta`, `ts_pctchange`, `ts_rank`, `ts_argmax`, `ts_argmin`, `ts_corr`, `ts_skew`, `ts_kurt`, `ts_decay_linear`); four cross-sectional (`cs_rank`, `cs_zscore`, `cs_demean`, `cs_winsorize`); one conditional (`where`). `ts_cov` SHALL NOT be implemented in v1 (per `scale_invariance.md` §4 — `cov(a·x, y) = a · cov(x, y)` re-introduces `adj_factor` taint; redundant with `ts_corr` for v1). All operators SHALL have a CPU reference implementation; no GPU code SHALL be introduced in Phase 1.

#### Scenario: a developer enumerates the operator registry
- **WHEN** a developer iterates `OperatorRegistry.all_operators()`
- **THEN** exactly 28 operators are returned
- **AND** the set matches the §4 catalogue verbatim (no `ts_cov`, no GPU variants)

#### Scenario: a contributor proposes adding ts_cov
- **WHEN** a contributor proposes to add `ts_cov` to the v1 registry
- **THEN** the proposal is rejected at review
- **AND** the reviewer cites `scale_invariance.md` §4 (`cov(a·x, y) = a · cov(x, y)` taint pollution)

### Requirement: Every operator SHALL handle NaN, zero, negative, constant, empty, and PIT-gap inputs without bridging

Each operator's CPU implementation SHALL defensively handle inputs containing NaN values, zero values, negative values (for `log_safe` / `sqrt_safe`), constant series, single-row series, empty series, and a series containing a NaN gap (PIT-gap input). For PIT-gap inputs the operator MUST NOT bridge the hole — i.e. a window operator whose window would span the hole MUST emit NaN across the hole AND for `window - 1` positions after the hole. Specific per-operator behaviours: `div_safe` returns NaN for zero / near-zero denominators (never ±Inf); `log_safe` / `sqrt_safe` return NaN for inputs ≤ 0 (no raise); `ts_rank` returns 0.5 (mid rank) for constant windows; `cs_zscore` returns 0 (not NaN) for per-day std ≈ 0; `ts_corr` replaces ±Inf with NaN.

#### Scenario: ts_mean receives a series with a NaN gap
- **WHEN** `ts_mean` is called with `window=3` on the series `[1.0, 2.0, NaN, NaN, NaN, 6.0, 7.0, 8.0]`
- **THEN** the output positions covering the hole are NaN
- **AND** the first `window - 1 = 2` positions strictly after the hole are also NaN (no partial-window leak)
- **AND** subsequent positions resume normal `window=3` mean values

#### Scenario: div_safe receives a zero denominator
- **WHEN** `div_safe(x, y)` is called with `y` containing zero values
- **THEN** the output at those positions is NaN
- **AND** the output never contains ±Inf

#### Scenario: cs_zscore receives a constant per-day cross-section
- **WHEN** `cs_zscore(x)` is called on a date where every ticker's value is identical
- **THEN** the output at that date is 0 (not NaN)
- **AND** the contagion of NaN to other ticker-dates is prevented

### Requirement: Time-series operators SHALL use `min_periods = window`

Every time-series operator (every `ts_*` in the registry) SHALL use pandas rolling with `min_periods = window`. Partial-window evaluation (`min_periods < window`) SHALL NOT be used. This is the unit-level guarantee that mined factors will not bridge PIT NaN gaps when Phase 2 wires real PIT data, because the qlib post-delist mask relies on partial-window operators producing NaN across the boundary.

#### Scenario: a reviewer inspects a ts_* operator's rolling call
- **WHEN** a reviewer reads the body of any `ts_*` operator in `operators.py`
- **THEN** every `pandas.rolling(...)` call has `min_periods = window` (or an equivalent explicit guard)
- **AND** no `ts_*` operator uses `min_periods=1` or the pandas default

### Requirement: Commutative operators SHALL hash identically regardless of argument order

The expression tree's structural hash SHALL be invariant to argument order for commutative operators. The Phase 1 commutative set is `{add, mul}`. For these operators the hash function SHALL sort child hashes before incorporating them, so `add(A, B)` and `add(B, A)` produce the same hash. Structural equality (`__eq__`) SHALL be consistent with the hash.

#### Scenario: a developer constructs add($close, $volume) and add($volume, $close)
- **WHEN** two expressions are built with swapped arguments to `add`
- **THEN** their hashes are equal
- **AND** they are structurally equal under `__eq__`

#### Scenario: a developer constructs mul with three different orderings of the same operand pair
- **WHEN** the developer builds `mul($volume, $money)` and `mul($money, $volume)`
- **THEN** both expressions hash to the same value
- **AND** future GP de-duplication treats them as a single canonical form

### Requirement: Expression tree SHALL provide round-trip serialisation and stable hashes

The `Expression` AST SHALL provide `to_dict()` / `from_dict()` (or equivalent JSON-serialisable form) round-trip that preserves structural identity; for any expression `e`, `Expression.from_dict(e.to_dict())` SHALL be structurally equal to `e` and SHALL hash to the same value. The AST SHALL also provide a human-readable `to_qlib_string()` pretty-printer whose output is stable across round-trip. Construction of an ill-typed expression SHALL raise `GrammarError` at construction time (no "build now, validate later" mode per `scale_invariance.md` §6.3).

#### Scenario: an expression is serialised and rebuilt from the dict
- **WHEN** `Expression.from_dict(expr.to_dict())` is called
- **THEN** the rebuilt expression is structurally equal to the original
- **AND** the rebuilt expression hashes to the same value
- **AND** the rebuilt expression's `to_qlib_string()` output is identical to the original's

#### Scenario: a caller constructs add($close, $volume) at the API level
- **WHEN** the constructor is invoked with `add` and the arguments `($close, $volume)` (types `ADJ_TAINTED, PURE`)
- **THEN** `GrammarError` is raised at construction time
- **AND** no partially-constructed expression is returned

### Requirement: Type system SHALL track `(OutputKind, ScaleTaint)` per `scale_invariance.md`

The grammar SHALL implement the two-tier type system from `docs/factor_mining/scale_invariance.md` (cited as a normative reference). Every expression SHALL carry an `ExprType` with two attributes: `kind` (one of `FEATURE`, `FLOAT`, `INT_WINDOW`, `SCALAR`, `CSF`) and `taint` (one of `PURE`, `ADJ_TAINTED`). Operator output types SHALL follow the propagation rules in `scale_invariance.md` §4. The `OperatorRegistry` SHALL expose `Operator.output_type(*input_types) -> ExprType` for each registered operator, returning the resolved type or raising `GrammarError` on illegal combinations.

#### Scenario: a developer queries the output type of div_safe(adj, adj)
- **WHEN** `div_safe.output_type(ExprType("FLOAT", "ADJ_TAINTED"), ExprType("FLOAT", "ADJ_TAINTED"))` is called
- **THEN** the returned `ExprType` has `kind == "FLOAT"` and `taint == "PURE"`
- **AND** this matches `scale_invariance.md` §4 "same-ticker ratio cancels `adj_factor`"

#### Scenario: a developer queries the output type of add(pure, adj_tainted)
- **WHEN** `add.output_type(ExprType("FLOAT", "PURE"), ExprType("FLOAT", "ADJ_TAINTED"))` is called
- **THEN** `GrammarError` is raised with a message naming the units-error rule
- **AND** no `ExprType` is returned

### Requirement: Feature universe SHALL be exactly the six PIT bin fields per D3

The terminal feature registry SHALL expose exactly six features: `$open`, `$high`, `$low`, `$close`, `$volume`, `$money`. `$open`, `$high`, `$low`, `$close` SHALL carry `taint = ADJ_TAINTED`; `$volume` and `$money` SHALL carry `taint = PURE`. `$vwap` SHALL NOT be a terminal — it is expressible as `div_safe($money, $volume)` (per `decisions.md` D3 and `scale_invariance.md` §5 example 6). `$turn` SHALL NOT be a terminal in v1 — deferred per `decisions.md` D3 pending separate Tushare ingest. `$amount` SHALL NOT be an alias for `$money` — only `$money` is exposed to match the PIT bin name.

#### Scenario: a developer enumerates the feature registry
- **WHEN** a developer iterates `FeatureRegistry.V1`
- **THEN** exactly the set `{"$open", "$high", "$low", "$close", "$volume", "$money"}` is returned
- **AND** `$vwap`, `$turn`, `$amount`, `$pe`, `$pb`, `$ps`, `$mktcap` are absent

#### Scenario: a developer queries the taint of each terminal
- **WHEN** the taint of each terminal in `FeatureRegistry.V1` is read
- **THEN** `$open`, `$high`, `$low`, `$close` return `ADJ_TAINTED`
- **AND** `$volume`, `$money` return `PURE`

### Requirement: Expression root SHALL satisfy `(kind=CSF, taint=PURE)` and cross-sectional operators SHALL reject `ADJ_TAINTED` inputs

The root of every Phase 1 expression SHALL have `output_type.kind == "CSF"` AND `output_type.taint == "PURE"`. Cross-sectional operators (`cs_rank`, `cs_zscore`, `cs_demean`, `cs_winsorize`) SHALL reject any input whose `taint != PURE` by raising `GrammarError` at construction time. The eight pinned pass/fail examples from `scale_invariance.md` §5 SHALL be enforced by the type-checker exactly as tabulated there: examples 1, 4, 5, 6, 8 PASS; examples 2, 3, 7 FAIL. The additional rejected forms enumerated below §5 (`cs_rank(add($close, $open))`, `cs_rank(mul($close, $volume))`, `cs_rank(ts_mean($close, 20))`) SHALL also fail.

#### Scenario: a caller constructs cs_rank($close)
- **WHEN** the constructor is invoked with `cs_rank` and the argument `$close` (type `ADJ_TAINTED`)
- **THEN** `GrammarError` is raised at construction time
- **AND** the message names the scale-invariance rule and points to `scale_invariance.md` §4 "cs_* gate"

#### Scenario: a caller constructs the pinned PASS example cs_rank(ts_pctchange($close, 20))
- **WHEN** the expression `cs_rank(ts_pctchange($close, 20))` is constructed
- **THEN** no exception is raised
- **AND** the resulting `output_type` is `ExprType("CSF", "PURE")`

#### Scenario: a developer constructs the pinned FAIL example cs_rank(ts_delta($close, 20))
- **WHEN** the expression `cs_rank(ts_delta($close, 20))` is constructed
- **THEN** `GrammarError` is raised
- **AND** the message names `ts_delta` taint-preservation as the cause (the inner `ts_delta($close, 20)` is `ADJ_TAINTED`, so `cs_rank` rejects it)

### Requirement: Random expression generator SHALL produce 100% type-valid scale-pure expressions with min_depth ≥ 2

The random expression generator SHALL accept a `target_type: ExprType` argument and a `min_depth` argument with default 2. For any call with `target_type = ExprType("CSF", "PURE")`, every sample produced SHALL have `output_type == ExprType("CSF", "PURE")` AND a tree depth ≥ `min_depth`. A test SHALL exercise the generator with 1000 samples (fixed RNG seed, `max_depth=6`, `min_depth=2`, `target_type=ExprType("CSF", "PURE")`) and assert 100% type-valid and 100% scale-pure roots. The generator SHALL sample only `group_by=None` for `cs_*` operators in v1 (per `decisions.md` D2).

#### Scenario: the 1000-sample generator test is executed
- **WHEN** `pytest tests/logic/factor_mining/test_grammar.py::test_random_generator_1000_samples` runs
- **THEN** all 1000 generated expressions have `output_type.kind == "CSF"` AND `output_type.taint == "PURE"`
- **AND** every expression has depth ≥ 2
- **AND** no generated `cs_*` operator carries `group_by != None`

#### Scenario: the generator is asked for a `FLOAT, ADJ_TAINTED` target deep in a subtree
- **WHEN** the recursive generator is called under a `cs_*` parent with `target_type = ExprType("FLOAT", "PURE")`
- **THEN** every candidate operator and leaf considered for that subtree has output `taint = PURE`
- **AND** `ADJ_TAINTED` leaves like `$close` are filtered out unless they reach `PURE` via a `div_safe` ratio in the subtree

### Requirement: `FactorMiningDataView` SHALL be the sole bridge to PIT data

`src/factor_mining/pit_adapter.py` SHALL export a `FactorMiningDataView` class whose constructor accepts a `PITDataProvider` instance plus a date range and universe specification. The view SHALL expose `load_panel()`, `forward_return(horizon: int)`, and `universe_mask()` methods. All Phase 2+ factor-mining code (evaluator, fitness, factor pool, GP engine, miner, validator, handler) SHALL consume PIT data only via this view; no other file under `src/factor_mining/` SHALL construct or call `PITDataProvider` methods directly.

#### Scenario: a developer constructs FactorMiningDataView
- **WHEN** `FactorMiningDataView(pit_provider, start, end, universe_name)` is instantiated
- **THEN** the view holds the provided `PITDataProvider` without making any data calls yet
- **AND** subsequent calls to `load_panel()` and `forward_return()` go through the provider's public API only

#### Scenario: evaluator.py attempts to instantiate PITDataProvider
- **WHEN** a reviewer inspects `src/factor_mining/evaluator.py`
- **THEN** the file does NOT contain `PITDataProvider(…)` constructor calls
- **AND** all panel and label data flows in via parameters originating from `FactorMiningDataView`

### Requirement: `load_panel` SHALL return one date × ticker DataFrame per feature

`FactorMiningDataView.load_panel()` SHALL call `PITDataProvider.get_features` once with the six PIT bin fields (`$open`, `$high`, `$low`, `$close`, `$volume`, `$money`) and SHALL return a dict mapping each field name to a `pd.DataFrame` whose index is the trading date and whose columns are the ticker symbols. The DataFrames SHALL preserve the post-delist NaN mask from PIT (i.e. no forward-fill, no bridging).

#### Scenario: load_panel returns the six-field dict
- **WHEN** `view.load_panel()` is called
- **THEN** the returned dict has keys exactly `{"$open", "$high", "$low", "$close", "$volume", "$money"}`
- **AND** each value is a `pd.DataFrame` with a `DatetimeIndex` and ticker columns
- **AND** post-delist cells are NaN in the per-field DataFrame (the PIT mask propagates through the swaplevel/unstack)

### Requirement: `forward_return` SHALL use the T+1 → T+1+h open-to-open formula

`FactorMiningDataView.forward_return(horizon: int)` SHALL call `PITDataProvider.get_features` with the qlib expression string `Ref($open, -horizon-1) / Ref($open, -1) - 1` (T+1 buy at open, T+1+horizon sell at open) per `decisions.md` D1 and `factor_mining_design.md` §5.3. The returned DataFrame SHALL be date × ticker.

#### Scenario: forward_return(1) issues the documented qlib expression
- **WHEN** `view.forward_return(1)` is called
- **THEN** the qlib expression passed to `get_features` is `Ref($open, -2) / Ref($open, -1) - 1`
- **AND** the result is a date × ticker DataFrame
- **AND** post-delist cells are NaN

#### Scenario: forward_return(5) for a multi-day horizon
- **WHEN** `view.forward_return(5)` is called
- **THEN** the qlib expression passed is `Ref($open, -6) / Ref($open, -1) - 1`

### Requirement: Evaluator SHALL produce IC, IR, RankIC, turnover, and coverage from one factor

`src/factor_mining/evaluator.py` SHALL expose `evaluate_factor(expr, panel, forward_return, *, method)` returning an `EvaluationResult` frozen dataclass with at minimum: `factor_values` (date × ticker), `ic_mean`, `ic_std`, `ir`, `rank_ic_mean`, `rank_ic_std`, `rank_ir`, `turnover_daily`, `coverage`, `n_obs_per_day_min`. The IC computation SHALL reuse `src.core._ic_utils.compute_ic_for_group` (per `inventory.md` §B.3 recommendation) and SHALL set IR to NaN when the corresponding IC std is below 1e-9 (per `inventory.md` §B.4).

#### Scenario: a factor perfectly correlated with the label
- **WHEN** `evaluate_factor` is called on a synthetic factor that equals forward_return on every (date, ticker) cell
- **THEN** the returned `rank_ic_mean` is approximately 1.0
- **AND** `rank_ir` is finite and large

#### Scenario: a constant factor across dates
- **WHEN** `evaluate_factor` is called on a factor whose value does not change day-to-day for any ticker
- **THEN** the returned `turnover_daily` is 0.0
- **AND** the cross-sectional IC is well-defined for the date dimension (no NaN injection from turnover alone)

#### Scenario: IR convention on zero IC variance
- **WHEN** `evaluate_factor` produces an `ic_std` strictly less than 1e-9
- **THEN** the returned `ir` is NaN (not 0.0)
- **AND** this matches `signal_analyzer.py` / `factor_analyzer.py` IR convention from `inventory.md` §B.4

### Requirement: Fitness SHALL implement the v1 §5.1 composite formula with D1 cost rate

`src/factor_mining/fitness.py` SHALL expose `compute_fitness(result, expr_size, novelty_penalty, config)` whose default `FitnessConfig` carries `cost_rate=0.003` per `decisions.md` D1. The formula SHALL be `w_ic * |ic_mean| + w_ir * ir + w_rankic * |rank_ic_mean| - w_turnover * (turnover_daily * 252 * cost_rate) - w_corr * novelty_penalty - w_complexity * expr_size`. Invalid factors (failing the §5.2 hard constraints) SHALL receive fitness `-inf`. The validity check is exposed as a separate `passes_validity(result, config)` predicate.

#### Scenario: a factor with coverage 0.5 fails validity
- **WHEN** `compute_fitness` is called with `result.coverage = 0.5` and the default `coverage_min = 0.8`
- **THEN** the returned fitness is `-inf`

#### Scenario: the cost term is annualised
- **WHEN** `compute_fitness` is called on a result with `turnover_daily = 0.1` and default config (`w_turnover=0.2`, `cost_rate=0.003`)
- **THEN** the turnover penalty contribution to fitness is exactly `-0.2 * (0.1 * 252 * 0.003)` = `-0.01512`

#### Scenario: FitnessConfig default cost_rate matches D1
- **WHEN** a developer instantiates `FitnessConfig()` with no arguments
- **THEN** `config.cost_rate` is exactly `0.003` (D1 locked value)

### Requirement: Validity filters SHALL enforce coverage, variance, and sanity constraints

`passes_validity(result, config)` SHALL return False if any of: (a) `result.coverage < config.coverage_min` (default 0.8), (b) the fraction of dates with cross-sectional std above `config.variance_min` (default 1e-6) is below `config.variance_days_frac_min` (default 0.7), or (c) the fraction of cells in `result.factor_values` that are non-finite or have absolute value above a sanity bound exceeds `config.extreme_outlier_frac_max` (default 0.05). Otherwise it returns True. (The data-leakage filter from `factor_mining_design.md` §5.2 item 3 is enforced by the Phase 1 grammar's scale-invariance gate and does not need a runtime check.)

#### Scenario: a near-constant factor fails the variance check
- **WHEN** `passes_validity` is called on a factor whose cross-sectional std is below 1e-6 on 50 % of dates with default config
- **THEN** the function returns False
- **AND** `compute_fitness` on the same result returns `-inf`

### Requirement: Factor pool SHALL dedup by structural hash and round-trip through parquet+JSON

`src/factor_mining/factor_pool.py` SHALL expose `PoolEntry` (frozen dataclass holding an `Expression`, metric scalars, and `expr_hash`) and `FactorPool` with `add(entry) -> bool` (returns True if not previously added, using `expr_hash` as the dedup key), `__len__`, `__contains__(expr_hash: int)`, `all_entries()`, `top_k(k, by)`, `correlation_with(factor_values, panel) -> float` (max absolute Pearson correlation against existing pool members, or 0.0 for an empty pool), and `save(dir_path)` / `load(dir_path)` that write / read `factor_pool.parquet` (metric scalars + `expr_hash`) plus `factor_expressions.json` (mapping `expr_hash` → serialised expression dict) per `factor_mining_design.md` §6.2.

#### Scenario: adding the same expression twice
- **WHEN** `pool.add(PoolEntry(expr, …))` is called twice with the same `expr`
- **THEN** the first call returns True
- **AND** the second call returns False
- **AND** `len(pool)` after both calls is 1

#### Scenario: commutative-equivalent expressions dedup
- **WHEN** two `PoolEntry` instances are constructed with expressions `add($volume, $money)` and `add($money, $volume)`
- **THEN** the second `add` call returns False
- **AND** the pool contains a single entry

#### Scenario: save + load round-trips
- **WHEN** `FactorPool.load(pool.save(tmp_dir))` is called after `pool` has been populated with N entries
- **THEN** the loaded pool has `len() == N`
- **AND** every entry's structural hash matches the corresponding pre-save entry
- **AND** the on-disk artefacts are exactly `factor_pool.parquet` and `factor_expressions.json`

#### Scenario: novelty correlation against empty pool
- **WHEN** `pool.correlation_with(factor_values, panel)` is called on an empty pool
- **THEN** the returned value is `0.0`

### Requirement: Phase 2 SHALL ship the default fitness config locking `cost_rate = 0.003`

`config/factor_mining/default.yaml` SHALL exist after Phase 2 with a `fitness.cost_rate` value of exactly `0.003` (per `decisions.md` D1) and the six fitness weights from `factor_mining_design.md` §5.1 (`w_ic=1.0`, `w_ir=0.5`, `w_rankic=0.5`, `w_turnover=0.2`, `w_corr=0.8`, `w_complexity=0.01`) and the three validity thresholds (`coverage_min=0.8`, `variance_days_frac_min=0.7`, `variance_min=1e-6`). `data.pit_provider_uri` and `data.delisted_registry_path` MAY be empty-string placeholders that the operator fills in once the PIT bundle is built (per `inventory.md` §F.3); code that consumes the config MUST raise an explicit error when these fields are empty.

#### Scenario: a developer reads the default YAML
- **WHEN** a developer loads `config/factor_mining/default.yaml`
- **THEN** `fitness.cost_rate` is exactly `0.003`
- **AND** the six fitness weights match the v1 §5.1 defaults
- **AND** `data.pit_provider_uri` is either a real path or an empty-string placeholder

### Requirement: `GPEngine` SHALL implement tournament selection, elitism, and per-generation hash dedup

`src/factor_mining/gp_engine.py` SHALL export a `GPEngine` class whose constructor accepts a `GPConfig` and a `FitnessConfig`. The engine SHALL perform tournament selection (default `tournament_size=3`) where the highest-fitness candidate among the sampled set is chosen as a parent, SHALL preserve the top `max(1, int(elite_frac * population_size))` individuals into the next generation unchanged ("elitism"), and SHALL deduplicate each generation by the Phase 1 structural hash. If post-dedup the new generation is smaller than `population_size`, the engine SHALL top up by generating fresh random expressions (via `grammar.random_expression`) to maintain a constant population size.

#### Scenario: tournament selection picks the best of the sampled set
- **WHEN** `select(evaluated)` is called with a fixed RNG seed and `tournament_size=3`
- **THEN** the returned expression has the highest fitness among the three sampled
- **AND** ties are broken by insertion order (deterministic)

#### Scenario: elitism preserves top-K
- **WHEN** `next_generation(evaluated)` is called on a sorted-by-fitness `evaluated` list
- **THEN** the top `max(1, int(elite_frac * population_size))` entries appear in the new generation unchanged

#### Scenario: per-generation dedup
- **WHEN** the new generation contains duplicate expression hashes
- **THEN** duplicates are dropped (one representative kept) before evaluation
- **AND** fresh `random_expression` calls top up the population back to `population_size`

### Requirement: Crossover SHALL be type-preserving via subtree exchange

`GPEngine.crossover(parent_a, parent_b)` SHALL perform a subtree exchange: enumerate `parent_a`'s subtree positions, pick one at random, find a subtree in `parent_b` with the same `ExprType` (kind + taint), and produce a new expression by replacing the picked subtree in `parent_a` with the matching subtree from `parent_b`. If `parent_b` contains no subtree of matching type, or if the resulting expression fails the Phase 1 type-check at construction (raising `GrammarError`), `parent_a` SHALL be returned unchanged. Crossover SHALL never produce an expression whose root fails the `(CSF, PURE)` constraint.

#### Scenario: matching-type subtree swap succeeds
- **WHEN** two parents share a `(FLOAT, PURE)` subtree shape and `crossover` is called
- **THEN** the offspring is structurally valid (passes Phase 1 type-check at construction)
- **AND** the offspring's root retains `(CSF, PURE)` type

#### Scenario: no matching-type subtree
- **WHEN** the chosen `parent_a` subtree's type is `(FLOAT, ADJ_TAINTED)` and `parent_b` has no `(FLOAT, ADJ_TAINTED)` subtree
- **THEN** `crossover` returns `parent_a` unchanged

### Requirement: Mutation SHALL provide subtree, point, and constant operators

`GPEngine` SHALL expose three mutation operators: `mutate_subtree(expr)` replaces a random subtree with a fresh `random_expression` of matching type; `mutate_point(expr)` replaces a random `Terminal` with a different `Terminal` of identical `(kind, taint)` (ADJ_TAINTED → another ADJ_TAINTED price feature; PURE → another PURE feature; INT_WINDOW → another window literal); `mutate_const(expr)` replaces a random INT_WINDOW literal with a different element of `WINDOW_LITERALS`. All three mutations SHALL preserve the Phase 1 type contract; any attempt that would produce an ill-typed expression is treated as a no-op (return the original).

#### Scenario: subtree mutation replaces a random node
- **WHEN** `mutate_subtree(expr)` is called and a feasible matching-type subtree is generated
- **THEN** the returned expression differs structurally from `expr` (different hash) OR is the original when the new subtree happens to be identical
- **AND** the returned expression's root type matches `expr`'s root type

#### Scenario: point mutation respects taint
- **WHEN** `mutate_point(expr)` is called on an expression containing `$close` (ADJ_TAINTED)
- **THEN** if `$close` is selected, the replacement is one of `{$open, $high, $low}` (other ADJ_TAINTED features), not `$volume` or `$money`

#### Scenario: const mutation swaps window literals
- **WHEN** `mutate_const(expr)` selects a window literal `Terminal("20")`
- **THEN** the replacement is another window literal from `{5, 10, 40, 60}`

### Requirement: `GPEngine.run` SHALL be deterministic given identical config and seed

For an identical `GPConfig` (including `seed`), `FitnessConfig`, panel, and forward-return inputs, two independent `GPEngine.run` invocations SHALL produce identical results: the same set of `(expr_hash, fitness)` entries across all generations and an identical final `FactorPool`. The engine SHALL flow all randomness through a single seeded `random.Random(config.seed)`; no `numpy.random` global state, no time-based seeds, no dict-iteration assumptions beyond Python 3.7+ insertion-order semantics.

#### Scenario: two identical runs produce identical pools
- **WHEN** `GPEngine(config, fitness).run(panel, fwd)` is called twice with the same inputs
- **THEN** the two final `FactorPool` objects have the same `len()`, the same set of expression hashes, and the same per-hash fitness values (within `1e-12` for floating-point)

### Requirement: Checkpoint save / load SHALL round-trip a GP run

`GPEngine.save_checkpoint(path)` SHALL write a JSON-serialisable representation of the engine state (config, current generation index, RNG state, fitness cache by hash, population, history). `GPEngine.load_checkpoint(path, *, fitness_config)` SHALL reconstruct an engine instance from that file. Running `engine.run(...)` after a load that occurred at generation `k` SHALL produce a final pool entry-identical to the pool produced by a single continuous run, modulo floating-point tolerance (`abs(continuous_fitness - resumed_fitness) < 1e-12` for every shared expression). The factor-values cache (used by the novelty term) does not need to survive serialisation; it is recomputed lazily on demand after resume.

#### Scenario: kill-resume produces the same pool as a continuous run
- **WHEN** an engine is run for `k` generations, checkpointed, reloaded, and run for the remaining `n - k` generations
- **THEN** the final pool is entry-identical (same hash set, same per-hash fitness within `1e-12`) to a single continuous run of `n` generations with the same config and seed

### Requirement: Miner CLI SHALL run end-to-end from a YAML config

`src/factor_mining/miner.py` SHALL be invokable as `python -m src.factor_mining.miner <config.yaml>` and SHALL produce, under `<output_dir>/runs/{run_id}/`: `factor_pool.parquet` and `factor_expressions.json` (the Phase 2 pool format), `gp_history.json` (the per-generation stats), and `config.yaml` (the parsed config dumped back). The `run_id` SHALL be either taken from the config or autogenerated. The CLI SHALL accept both `data.mode: synthetic` (in-process deterministic panel) and `data.mode: pit` (real PIT bundle via `FactorMiningDataView`); the PIT-mode SHALL raise an explicit error when `pit_provider_uri` or `delisted_registry_path` is an empty string per `config/factor_mining/default.yaml`'s placeholder convention.

#### Scenario: running the smoke config end-to-end
- **WHEN** a developer runs `python -m src.factor_mining.miner config/factor_mining/smoke.yaml`
- **THEN** the process exits 0
- **AND** the four output files exist under `<output_dir>/runs/{run_id}/`
- **AND** the same invocation with an unchanged config produces a pool with the same entries (determinism)

#### Scenario: pit-mode rejects empty URI
- **WHEN** the miner is invoked with `data.mode: pit` and `data.pit_provider_uri: ""`
- **THEN** an explicit error is raised naming `inventory.md` §F.3 (the "build the PIT bundle" follow-up task)
- **AND** no factor-pool files are written

### Requirement: Smoke config SHALL exist and complete quickly on CPU

`config/factor_mining/smoke.yaml` SHALL exist after Phase 3 and SHALL configure a small synthetic-data run (target wall-clock < 30 s on a modest CPU). Running `python -m src.factor_mining.miner config/factor_mining/smoke.yaml` is the canonical smoke test of the Phase 3 surface.

#### Scenario: a developer wants to verify the GP engine works
- **WHEN** a developer runs the smoke command
- **THEN** the run completes in under 30 s on a typical developer laptop
- **AND** the produced pool contains at least one entry (some expression passes validity)

