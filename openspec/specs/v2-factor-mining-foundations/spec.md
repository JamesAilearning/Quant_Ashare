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

Code under `src/factor_mining/` SHALL NOT import from `qlib`, SHALL NOT call `qlib.init`, and SHALL NOT reference `qlib.data.D`. A repository-wide grep for `qlib\.data`, `qlib\.init`, or `from qlib` under `src/factor_mining/` MUST return zero matches. The PIT layer SHALL be reached only through `src/factor_mining/pit_adapter.py`, which is the designated data door; only `pit_adapter.py` MAY import `PITDataProvider` from `src.pit.query`. Other modules under `src/factor_mining/` (including Phase 3's `gp_engine.py` / `miner.py` and Phase 6's `validator.py` / `promote.py`) SHALL NOT import `src.pit` directly. `miner.py` and `promote.py` MAY consume PIT data only via `FactorMiningDataView` instances they construct around `PITDataProvider` (i.e. the PIT entry point is still `pit_adapter.py`). The qlib direct-import ban remains absolute for the entire subpackage.

#### Scenario: a developer runs the strict data-gate grep
- **WHEN** a developer runs `grep -rn "qlib\.data\|qlib\.init\|from qlib" src/factor_mining/`
- **THEN** the output is empty (zero matches)
- **AND** any non-empty output is treated as a scope violation, not an acceptable exception

#### Scenario: validator.py or promote.py attempts to import src.pit directly
- **WHEN** a module under `src/factor_mining/` other than `pit_adapter.py` adds `from src.pit.query import …` or `import src.pit`
- **THEN** the change is rejected at review
- **AND** the reviewer directs the contributor to route the call through `FactorMiningDataView` in `pit_adapter.py`

#### Scenario: promote.py constructs a panel in PIT mode
- **WHEN** a reviewer inspects `src/factor_mining/promote.py`'s PIT-mode branch
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

The terminal feature registry SHALL expose exactly twelve features partitioned into two groups:

**Group A — six OHLCV PIT bin fields (existing per D3):**
- `$open`, `$high`, `$low`, `$close` — daily OHLC closing prices, **`taint = ADJ_TAINTED`** (qlib adjusts via `adj_factor`).
- `$volume`, `$money` — daily traded volume and yuan amount, **`taint = PURE`**.

**Group B — six daily_basic fundamental / microstructure fields (new):**
- `$pe`, `$pb`, `$ps` — value ratios (price/earnings, price/book, price/sales). **`taint = PURE`**. Ratios of two same-ticker quantities cancel the `adj_factor` ladder identically (per `scale_invariance.md` §4 same-ticker-ratio rule).
- `$turnover_rate` — daily volume / float_share. **`taint = PURE`**. Already a ratio, scale-free.
- `$circ_mv`, `$total_mv` — circulating and total market capitalisation in yuan. **`taint = PURE`**. Tushare publishes the cap by recomputing `shares × current_price` each day, NOT by scaling a static reference through the adjustment ladder. (Operators applying this proposal MUST verify this against a sample of historical split events; if the cap ladders, downgrade to `ADJ_TAINTED` and update this requirement.)

The following terminals SHALL NOT be added in this iteration:
- `$vwap` — expressible as `div_safe($money, $volume)` (existing decision D3).
- `$turn` (Tushare turnover absolute) — `$turnover_rate` is the per-cent normalisation we want.
- `$amount` — duplicates `$money` (kept under the PIT bin name `money`).
- `$pe_ttm`, `$ps_ttm`, `$float_share`, `$total_share` — held back for a future iteration; the six chosen above are the highest-impact categories per the v1 empirical follow-up.
- `$pe`, `$pb`, `$ps` use the same-tier (non-TTM) Tushare daily_basic columns by default.

#### Scenario: a developer enumerates the feature registry
- **WHEN** a developer iterates `FeatureRegistry.V1`
- **THEN** exactly the set `{"$open", "$high", "$low", "$close", "$volume", "$money", "$pe", "$pb", "$ps", "$turnover_rate", "$circ_mv", "$total_mv"}` is returned
- **AND** `$vwap`, `$turn`, `$amount`, `$pe_ttm`, `$float_share`, `$total_share` are absent

#### Scenario: a developer queries the taint of each terminal
- **WHEN** the taint of each terminal in `FeatureRegistry.V1` is read
- **THEN** `$open`, `$high`, `$low`, `$close` return `ADJ_TAINTED`
- **AND** `$volume`, `$money`, `$pe`, `$pb`, `$ps`, `$turnover_rate`, `$circ_mv`, `$total_mv` return `PURE`

#### Scenario: `cs_rank` directly accepts the new PURE terminals
- **WHEN** a caller constructs `cs_rank($pe)`, `cs_rank($pb)`, `cs_rank($turnover_rate)`, or `cs_rank($circ_mv)`
- **THEN** construction succeeds with `output_type == ExprType("CSF", "PURE")`
- **AND** no `GrammarError` is raised

#### Scenario: PURE ÷ PURE fundamental composites stay PURE
- **WHEN** a caller constructs `div_safe($pe, $pb)` (both PURE — value-ratio composite)
- **THEN** the result's `output_type` is `ExprType("FLOAT", "PURE")` per `_rule_div_safe`'s "same-taint → PURE" branch (scale_invariance.md §4)
- **AND** wrapping it in `cs_rank(...)` constructs cleanly as `ExprType("CSF", "PURE")`

#### Scenario: PURE-cap divided by ADJ_TAINTED adjusted close does NOT cancel taint
- **WHEN** a caller constructs `div_safe($total_mv, $close)` (mixed taints: cap is PURE because Tushare publishes it as a daily-recomputed product, NOT through the adjustment ladder; `$close` is ADJ_TAINTED because the qlib bundle stores adjusted closes)
- **THEN** the result's `output_type` is `ExprType("FLOAT", "ADJ_TAINTED")` per `_rule_div_safe`'s "different-taint → ADJ_TAINTED" branch — the ratio inherits `1/adj_factor` because the cap does NOT ride the same adjustment ladder as the close
- **AND** wrapping it in `cs_rank(...)` SHALL raise `GrammarError` (the cs_* gate rejects ADJ_TAINTED input). This pinned example documents an intuitive trap — "both are same-ticker daily quantities, surely adj cancels" is false unless BOTH sides ride the same adjustment ladder

#### Scenario: mixing PURE fundamentals with ADJ_TAINTED price is rejected at the inner additive op
- **WHEN** a caller constructs `cs_rank(add($pe, $close))` (one PURE input, one ADJ_TAINTED input to `add`)
- **THEN** `GrammarError` is raised at construction time
- **AND** the message names the taint mismatch on `add` (the inner failure surfaces before the `cs_rank` gate)

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

The generator MAY encounter operator-argument combinations that pass static type-checking but are rejected by additional `OperatorCall.__post_init__` invariants (e.g. the `ts_corr` trivial-form rule). In such cases the generator SHALL retry with freshly-sampled subtrees up to a bounded retry budget (currently 10), and SHALL fall back to a leaf when the retry budget is exhausted and a leaf is available for the requested target type. The retry budget SHALL never be the binding cause of generator failure under normal operation: any output-type that has at least one non-trivial operator candidate in the registry SHALL succeed.

#### Scenario: the 1000-sample generator test is executed
- **WHEN** `pytest tests/logic/factor_mining/test_grammar.py::test_random_generator_1000_samples` runs
- **THEN** all 1000 generated expressions have `output_type.kind == "CSF"` AND `output_type.taint == "PURE"`
- **AND** every expression has depth ≥ 2
- **AND** no generated `cs_*` operator carries `group_by != None`

#### Scenario: the generator is asked for a `FLOAT, ADJ_TAINTED` target deep in a subtree
- **WHEN** the recursive generator is called under a `cs_*` parent with `target_type = ExprType("FLOAT", "PURE")`
- **THEN** every candidate operator and leaf considered for that subtree has output `taint = PURE`
- **AND** `ADJ_TAINTED` leaves like `$close` are filtered out unless they reach `PURE` via a `div_safe` ratio in the subtree

#### Scenario: the generator samples a trivial ts_corr form internally
- **WHEN** the recursive generator picks `ts_corr` and the sampled children happen to be `(f(X), X, N)` for `f ∈ {"neg", "log_safe", "sqrt_safe"}`
- **THEN** the `OperatorCall` constructor raises `GrammarError`
- **AND** the generator catches the error and retries with new children up to 10 times
- **AND** in practice no random expression test (`test_random_generator_avoids_trivial_ts_corr`, 500 samples) exhausts the retry budget

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

`src/factor_mining/evaluator.py` SHALL expose `evaluate_factor(expr, panel, forward_return, *, method, universe_mask=None)` returning an `EvaluationResult` frozen dataclass with at minimum: `factor_values` (date × ticker), `ic_mean`, `ic_std`, `ir`, `rank_ic_mean`, `rank_ic_std`, `rank_ir`, `turnover_daily`, `coverage`, `n_obs_per_day_min`. The IC computation SHALL reuse `src.core._ic_utils.compute_ic_for_group` (per `inventory.md` §B.3 recommendation) and SHALL set IR to NaN when the corresponding IC std is below 1e-9 (per `inventory.md` §B.4).

`coverage` SHALL be computed **relative to universe membership** when a `universe_mask` (boolean date × ticker frame) is supplied: the denominator is the count of (date, ticker) cells where the ticker is a universe member on that day, and the numerator is the count of those member cells that also carry a finite factor value. This is required for survivorship-corrected PIT panels, whose union matrix is ~40 % NaN purely because most union tickers are non-members on any given day; an all-cells denominator makes `coverage_min` unsatisfiable (a perfect factor scores ~0.62 and is rejected, so a full GP run returns an empty pool). When `universe_mask` is None, `coverage` SHALL fall back to the all-cells non-NaN fraction (the legacy behaviour for synthetic / dense panels). The `universe_mask` parameter SHALL be optional and SHALL NOT introduce any `qlib` or `src.pit` import into `evaluator.py` (the mask is produced by `FactorMiningDataView.universe_mask`, the pit_adapter door, and passed in as a DataFrame), preserving the D5 strict gate.

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

#### Scenario: coverage is members-relative when a universe mask is supplied
- **WHEN** `evaluate_factor` is called with a `universe_mask` whose non-member cells (mask False) are NaN in the factor and whose member cells (mask True) are all finite
- **THEN** the returned `coverage` is ~1.0 (non-member NaN cells are excluded from the denominator)
- **AND** the same call without `universe_mask` returns the lower all-cells fraction

#### Scenario: a NaN on a member cell still reduces members-relative coverage
- **WHEN** `evaluate_factor` is called with a `universe_mask` and the factor is NaN on a cell where the ticker IS a member that day
- **THEN** that cell counts against `coverage` (numerator excludes it, denominator includes it), so the validity gate still rejects genuinely-undefined factors

#### Scenario: a member cell absent from the factor counts as uncovered
- **WHEN** `evaluate_factor` is called with a `universe_mask` that marks a (date, ticker) as a member but that cell is absent from `factor_values` entirely (e.g. the PIT provider omits an all-missing member ticker/row)
- **THEN** the denominator is computed over the mask's own domain and the factor is aligned onto the mask, so the absent member cell is counted as uncovered (in the denominator, not in the numerator) rather than dropped — coverage is not inflated

#### Scenario: no universe mask reproduces legacy all-cells coverage
- **WHEN** `evaluate_factor` is called with `universe_mask=None` (the default) on a panel with some NaN factor cells
- **THEN** `coverage` equals the count of non-NaN cells divided by the total cell count (the pre-change behaviour, preserved for synthetic / dense panels)

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

`passes_validity(result, config)` SHALL return False if any of: (a) `result.coverage < config.coverage_min` (default 0.8), (b) the fraction of dates with cross-sectional std above `config.variance_min` (default 1e-6) is below `config.variance_days_frac_min` (default 0.7), or (c) the fraction of **finite cells** in `result.factor_values` whose absolute value exceeds `config.extreme_outlier_magnitude` (default 1e8) exceeds `config.extreme_outlier_frac_max` (default 0.05). The sanity check's denominator SHALL be the count of finite cells (`np.isfinite(arr).sum()`), NOT the total cell count, and non-finite (NaN / Inf) cells SHALL NOT count as outliers. This separates the sanity check (a magnitude filter on the finite fraction) from the coverage check (a NaN-density filter); an earlier implementation used the total-cell denominator and counted non-finite cells as outliers, which made the effective binding constraint coverage ≥ `1 - extreme_outlier_frac_max` (≥ 0.95 with defaults), strictly tighter than the design doc's `coverage_min = 0.80`. An all-NaN factor SHALL return 0.0 for the sanity check (the metric is undefined; coverage_min is the binding rejection in that case). Otherwise `passes_validity` returns True. (The data-leakage filter from `factor_mining_design.md` §5.2 item 3 is enforced by the Phase 1 grammar's scale-invariance gate and does not need a runtime check.)

#### Scenario: a near-constant factor fails the variance check
- **WHEN** `passes_validity` is called on a factor whose cross-sectional std is below 1e-6 on 50 % of dates with default config
- **THEN** the function returns False
- **AND** `compute_fitness` on the same result returns `-inf`

#### Scenario: 30% NaN with bounded finite values passes the sanity check
- **WHEN** `passes_validity` is called on a factor with 30 % NaN cells whose finite values are all bounded (e.g. samples from `N(0, 1)`), with `coverage_min = 0.0` and `variance_days_frac_min = 0.0` (so sanity is the only check)
- **THEN** the function returns True
- **AND** the sanity check does NOT double-count the 30 % NaN cells as outliers

#### Scenario: extreme outliers in finite cells still fail the sanity check
- **WHEN** `passes_validity` is called on a factor with 30 % NaN cells AND 10 % of the remaining finite cells set to magnitude `1e10`, with `coverage_min = 0.0`, `variance_days_frac_min = 0.0`, and `extreme_outlier_frac_max = 0.05`
- **THEN** the function returns False
- **AND** the rejection is driven by the finite-cell magnitude check (10 % of finite cells > 5 % threshold)

#### Scenario: an all-NaN factor does not crash the sanity check
- **WHEN** `passes_validity` is called on a factor whose every cell is NaN, with `coverage_min = 0.0` and `variance_days_frac_min = 0.0`
- **THEN** the function returns True (the sanity metric is undefined; coverage_min is the binding rejection in production configs, and we disabled it here to isolate the sanity outcome)
- **AND** no exception is raised

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

The CLI MAY accept an optional top-level `pool_top_k` integer key. When set and `len(pool) > pool_top_k`, the saved pool SHALL be truncated to the top-K entries by `fitness` desc (Phase 1 hash tie-break preserves determinism) BEFORE persistence. When unset (the default), the entire post-GP pool is persisted. The truncation SHALL happen once, after the GP loop completes; the GP search loop itself (selection, novelty, mutation, crossover, elitism) SHALL NOT be modified by `pool_top_k`. The per-run `config.yaml` snapshot SHALL record `pool_top_k`, `full_pool_size_pre_truncation` (the pool size returned by `engine.run`), and `saved_pool_size` (the pool size that was actually written to disk) so operators can audit truncation after the fact. A `pool_top_k` of zero or a negative integer SHALL raise `ValueError` at config load.

#### Scenario: running the smoke config end-to-end
- **WHEN** a developer runs `python -m src.factor_mining.miner config/factor_mining/smoke.yaml`
- **THEN** the process exits 0
- **AND** the four output files exist under `<output_dir>/runs/{run_id}/`
- **AND** the same invocation with an unchanged config produces a pool with the same entries (determinism)

#### Scenario: pit-mode rejects empty URI
- **WHEN** the miner is invoked with `data.mode: pit` and `data.pit_provider_uri: ""`
- **THEN** an explicit error is raised naming `inventory.md` §F.3 (the "build the PIT bundle" follow-up task)
- **AND** no factor-pool files are written

#### Scenario: pool_top_k truncates the persisted pool to top-K by fitness
- **WHEN** the miner is invoked with a YAML that sets `pool_top_k: 50` and the GP run produces > 50 valid factors
- **THEN** the saved `factor_pool.parquet` has exactly 50 rows
- **AND** the 50 saved entries are the highest-fitness entries from the untruncated pool (rank-1 .. rank-50 by `fitness` desc)
- **AND** `config.yaml` records `pool_top_k: 50`, `full_pool_size_pre_truncation: <N>`, `saved_pool_size: 50` (where `N` is the untruncated count)

#### Scenario: pool_top_k larger than the pool is a no-op
- **WHEN** the miner is invoked with `pool_top_k: 10000` and the GP produces 91 valid factors
- **THEN** the saved pool has 91 entries (truncation does not pad)
- **AND** the saved entries are the entire post-GP pool

#### Scenario: pool_top_k of zero or negative is rejected at load
- **WHEN** the YAML sets `pool_top_k: 0` (or any negative integer)
- **THEN** `load_config` raises `ValueError` with a message naming "positive integer"
- **AND** no GP run is started, no files are written

### Requirement: Smoke config SHALL exist and complete quickly on CPU

`config/factor_mining/smoke.yaml` SHALL exist after Phase 3 and SHALL configure a small synthetic-data run (target wall-clock < 30 s on a modest CPU). Running `python -m src.factor_mining.miner config/factor_mining/smoke.yaml` is the canonical smoke test of the Phase 3 surface.

#### Scenario: a developer wants to verify the GP engine works
- **WHEN** a developer runs the smoke command
- **THEN** the run completes in under 30 s on a typical developer laptop
- **AND** the produced pool contains at least one entry (some expression passes validity)

### Requirement: Validator SHALL split a pool on a configured IS/OOS date and reject too-short segments

`src/factor_mining/validator.py` SHALL expose `validate_pool(pool, panel, forward_return, criteria) -> list[FactorValidationResult]`. The validator SHALL slice the panel and forward-return on `criteria.is_oos_split_date` so that dates strictly before the split are IS and dates on/after are OOS. If either segment yields fewer than `criteria.min_obs_per_segment` observations (joint non-NaN cells), every factor in the pool SHALL fail validation with a `segment_too_short` reason — no OOS metric is computed in this case.

#### Scenario: too few OOS dates
- **WHEN** `validate_pool` is called with a panel whose OOS segment has only 5 trading dates and `criteria.min_obs_per_segment = 30`
- **THEN** every returned `FactorValidationResult.passes` is `False`
- **AND** every result's `reasons` contains `oos_segment_too_short`

#### Scenario: standard split with adequate segments
- **WHEN** `validate_pool` is called with a panel where both segments have ≥ `min_obs_per_segment` dates
- **THEN** each factor's `is_n_obs` and `oos_n_obs` are populated with non-zero counts
- **AND** the `passes` flag depends on the OOS metric thresholds (not the segment-length check)

### Requirement: Validator SHALL reject factors whose OOS metrics fall below thresholds

For each pool entry the validator SHALL evaluate the factor against the OOS slice and SHALL set `passes = False` if `abs(oos_ir) < criteria.min_oos_ir` OR `abs(oos_rank_ic_mean) < criteria.min_oos_rank_ic_mean`. NaN values SHALL be treated as 0 for the threshold comparison. The failure reasons SHALL list each violated criterion as a distinct string so an operator can see exactly which threshold the factor missed.

#### Scenario: classic overfit pattern is rejected
- **WHEN** a factor has `is_ir = +∞ / NaN` (IS rank-IC perfect every day) but `oos_ir ≈ 0` (OOS factor uncorrelated with label) and `criteria.min_oos_ir = 0.3`
- **THEN** `validate_pool` returns a result with `passes = False`
- **AND** the `reasons` tuple contains `oos_ir_below_threshold`

#### Scenario: stable factor passes
- **WHEN** a factor has `oos_ir = 0.5` and `oos_rank_ic_mean = 0.04` against `min_oos_ir = 0.3` and `min_oos_rank_ic_mean = 0.02`
- **THEN** `validate_pool` returns a result with `passes = True`
- **AND** `reasons` is the empty tuple

### Requirement: Validator SHALL filter pairwise-correlated factors after per-factor pass

`src/factor_mining/validator.py` SHALL expose `filter_correlated(results, panel, criteria) -> list[FactorValidationResult]` that processes the per-factor results sorted by `fitness` desc. For each result, it SHALL compute the max absolute Pearson correlation against every already-kept higher-fitness result's factor values (evaluated against the full panel). When that max correlation exceeds `criteria.max_pool_correlation`, the result's `passes` SHALL be set to `False` (or kept False if already failing) and the reason `correlated_with_higher_fitness` SHALL be appended.

#### Scenario: a high-fitness factor and a near-duplicate low-fitness factor
- **WHEN** `filter_correlated` is called on two passing results whose factor-value correlation is 0.9 and `max_pool_correlation = 0.6`
- **THEN** the higher-fitness result remains `passes=True`
- **AND** the lower-fitness result becomes `passes=False` with reason `correlated_with_higher_fitness`

#### Scenario: two uncorrelated factors both pass
- **WHEN** `filter_correlated` is called on two passing results whose factor-value correlation is 0.2 and `max_pool_correlation = 0.6`
- **THEN** both retain `passes=True`

### Requirement: Promotion CLI SHALL be manual-gated with dry-run support

`src/factor_mining/promote.py` SHALL expose `promote_run(config, *, dry_run=False) -> PromotionReport` and a `python -m src.factor_mining.promote --run … --to … [--config …] [--dry-run]` CLI entry. The CLI SHALL NEVER promote automatically — every invocation is a human action per `decisions.md` D4. With `--dry-run`, the report is produced but no files are written. Without `--dry-run`, a successful promote SHALL create `<production_dir>/<version>/` and write `factor_pool.parquet`, `factor_expressions.json`, and `promotion_report.json` under it. The CLI SHALL refuse to overwrite an existing version directory (operator must choose a new label); attempting to do so SHALL raise an error before any file is written.

#### Scenario: dry-run produces a report but writes nothing
- **WHEN** `promote_run(config, dry_run=True)` is called
- **THEN** the returned `PromotionReport.output_dir` is None
- **AND** no files are written under `<production_dir>/<version>/`

#### Scenario: normal run writes three files
- **WHEN** `promote_run(config, dry_run=False)` succeeds on a fresh version label
- **THEN** `<production_dir>/<version>/factor_pool.parquet` exists
- **AND** `<production_dir>/<version>/factor_expressions.json` exists
- **AND** `<production_dir>/<version>/promotion_report.json` exists

#### Scenario: refusing to overwrite an existing version directory
- **WHEN** `promote_run` is called with a `version` whose directory already exists under `production_dir`
- **THEN** the call raises an error before any file is written
- **AND** the error message names the conflicting directory and suggests choosing a new version label

#### Scenario: CLI invocation exit codes
- **WHEN** `python -m src.factor_mining.promote --run <good_run> --to v1` is invoked with valid arguments
- **THEN** the process exits 0
- **AND** prints a one-line summary of the kept count

- **WHEN** `python -m src.factor_mining.promote --run <missing_path> --to v1` is invoked
- **THEN** the process exits non-zero
- **AND** prints a clear error message

### Requirement: `ts_corr` SHALL reject trivial `(f(X), X)` forms at construction time

`OperatorCall("ts_corr", (a, b, n))` SHALL raise `GrammarError` at construction time when the two factor-argument expressions `a` and `b` are trivially related. Trivial forms SHALL be:

1. **Structural equality**: `a == b` (same Expression AST, e.g. `ts_corr($close, $close, 20)`). The cross-sectional correlation of a series with itself is mechanically 1.0 (or NaN per the existing v1 §5.2 ±Inf → NaN rule when the per-ticker variance is zero), carrying no signal.
2. **Bijective univariate transform**: `a` is `op(b)` where `op ∈ {"neg", "log_safe", "sqrt_safe"}` (the bijective monotonic univariate operators in the v1 registry), or symmetrically `b = op(a)`. The correlation between a series and a monotonic function of itself is mechanically near ±1 over a rolling window; residual variation is numerical compression artefact (e.g. `log` near zero), not predictive content.

`abs` and `sign` SHALL NOT be in the bijective-univariate set: `abs` legitimately captures sign-asymmetry between a series and its absolute value (which can be a real factor), and `sign` is piecewise-constant such that `ts_corr` is already degenerate (zero per-ticker variance → NaN per the existing rule). The rule rejection text SHALL cite `docs/factor_mining/empirical_results_b_std.md` §"Top expressions reveal pseudo-signals" so future contributors can see the empirical motivation.

#### Scenario: ts_corr of a feature with itself
- **WHEN** `OperatorCall("ts_corr", (Terminal("$close"), Terminal("$close"), Terminal("20")))` is constructed
- **THEN** `GrammarError` is raised with a message containing "trivially related"
- **AND** the message cites the empirical doc

#### Scenario: ts_corr of a feature with `neg`/`log_safe`/`sqrt_safe` of itself
- **WHEN** any of `OperatorCall("ts_corr", (OperatorCall(op, ($close,)), $close, 20))` is constructed for `op ∈ {"neg", "log_safe", "sqrt_safe"}`
- **THEN** `GrammarError` is raised at construction time
- **AND** the same rejection holds for the symmetric form `ts_corr($close, op($close), 20)`

#### Scenario: ts_corr of two distinct features
- **WHEN** `OperatorCall("ts_corr", (Terminal("$close"), Terminal("$volume"), Terminal("20")))` is constructed
- **THEN** no exception is raised (cross-feature correlation is a legitimate factor pattern)

#### Scenario: ts_corr of `abs(X)` with `X`
- **WHEN** `OperatorCall("ts_corr", (OperatorCall("abs", (Terminal("$close"),)), Terminal("$close"), Terminal("20")))` is constructed
- **THEN** no exception is raised (`abs` is intentionally not in the bijective-univariate blocklist)

#### Scenario: ts_corr of two unrelated operator subtrees
- **WHEN** `OperatorCall("ts_corr", (ts_mean($close, 20), ts_std($volume, 20), Terminal("20")))` is constructed
- **THEN** no exception is raised

