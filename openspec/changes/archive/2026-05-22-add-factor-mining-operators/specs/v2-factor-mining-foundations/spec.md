## ADDED Requirements

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

Phase 1 code under `src/factor_mining/` SHALL NOT import from `qlib`, SHALL NOT call `qlib.init`, SHALL NOT reference `qlib.data.D`, and SHALL NOT construct or call `src.pit.query.PITDataProvider`. A repository-wide grep for `qlib\.data`, `qlib\.init`, or `from qlib` under `src/factor_mining/` MUST return zero matches. This requirement implements the strict data gate D5 from `docs/factor_mining/decisions.md`.

#### Scenario: a developer runs the strict data-gate grep
- **WHEN** a developer runs `grep -rn "qlib\.data\|qlib\.init\|from qlib" src/factor_mining/`
- **THEN** the output is empty (zero matches)
- **AND** any non-empty output is treated as a Phase 1 scope violation, not an acceptable exception

#### Scenario: a Phase 1 reviewer encounters a "small qlib call, just this once"
- **WHEN** a Phase 1 diff introduces a `from qlib.data import D` line anywhere under `src/factor_mining/`
- **THEN** the reviewer rejects the change
- **AND** the reviewer cites D5 ("a black-and-white rule survives drift better than a one-exception rule")

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
