## MODIFIED Requirements

### Requirement: Phase 1 SHALL NOT access qlib, PIT data, or any data source

Code under `src/factor_mining/` SHALL NOT import from `qlib`, SHALL NOT call `qlib.init`, and SHALL NOT reference `qlib.data.D`. A repository-wide grep for `qlib\.data`, `qlib\.init`, or `from qlib` under `src/factor_mining/` MUST return zero matches. The PIT layer SHALL be reached only through `src/factor_mining/pit_adapter.py`, which is the designated data door; only `pit_adapter.py` MAY import `PITDataProvider` from `src.pit.query`. All other modules under `src/factor_mining/` SHALL NOT import `src.pit` directly. This requirement extends the original Phase 1 strict gate D5 (from `docs/factor_mining/decisions.md`) to acknowledge the Phase 2 `pit_adapter.py` data door; the qlib direct-import ban remains absolute for the entire subpackage including `pit_adapter.py`.

#### Scenario: a developer runs the strict data-gate grep
- **WHEN** a developer runs `grep -rn "qlib\.data\|qlib\.init\|from qlib" src/factor_mining/`
- **THEN** the output is empty (zero matches)
- **AND** any non-empty output is treated as a scope violation, not an acceptable exception

#### Scenario: a Phase 2+ module attempts to import src.pit directly
- **WHEN** a module under `src/factor_mining/` other than `pit_adapter.py` adds `from src.pit.query import …` or `import src.pit`
- **THEN** the change is rejected at review
- **AND** the reviewer directs the contributor to route the call through `FactorMiningDataView` in `pit_adapter.py`

#### Scenario: pit_adapter.py imports PITDataProvider
- **WHEN** a reviewer inspects `src/factor_mining/pit_adapter.py`
- **THEN** the file imports `PITDataProvider` from `src.pit.query`
- **AND** the file does NOT contain `from qlib …` or `qlib.init` or `qlib.data` references

## ADDED Requirements

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
