# Design: Factor Mining Evaluator (Phase 2)

> The long-form design lives at
> `docs/factor_mining/factor_mining_claude_code_design.md` §6 (Phase
> 2 row) and `factor_mining_design.md` §5 (fitness + filters) / §6
> (pool). Decisions are pinned in `decisions.md` (D1 cost rate, D5
> data gate). The contract-level decisions are below.

## Module additions (Phase 2 only)

```
src/factor_mining/
├── pit_adapter.py     # FactorMiningDataView — sole data door
├── evaluator.py       # walker + IC/IR/RankIC/turnover/coverage
├── fitness.py         # composite fitness per v1 §5.1 + D1 cost
└── factor_pool.py     # dedup, novelty, persistence per v1 §6.2

config/factor_mining/
└── default.yaml       # fitness weights + cost_rate (D1)

tests/logic/factor_mining/
├── test_pit_adapter.py
├── test_evaluator.py
├── test_fitness.py
└── test_factor_pool.py
```

No edits to Phase 1 source modules (`__init__.py`, `operators.py`,
`expression.py`, `grammar.py`) are required. Phase 2 imports them as
a stable upstream layer.

## Data flow

```
PIT bundle on disk
       │
       ▼
PITDataProvider(provider_uri, delisted_registry_path)
       │
       ▼  get_features([fields], start, end, universe_name=..., instruments=...)
       │  returns pd.DataFrame with (instrument, datetime) MultiIndex
       ▼
FactorMiningDataView      ← THE ONLY data door
   │   .load_panel()      → dict[str, DataFrame]  (date × ticker)
   │   .forward_return(h) → DataFrame             (date × ticker)
   │   .universe_mask()   → DataFrame             (boolean, date × ticker)
   ▼
evaluator.evaluate_expression(expr, panel)         → factor values (date × ticker)
evaluator.evaluate_factor(expr, panel, fwd_ret)    → EvaluationResult
       │
       ▼
fitness.compute_fitness(result, ...)               → float
       │
       ▼
factor_pool.FactorPool.add(PoolEntry(...))         → bool (dedup by hash)
       │
       ▼
factor_pool.save(dir)                              → factor_pool.parquet
                                                     factor_expressions.json
```

## PIT adapter (`pit_adapter.py`)

### Responsibilities

`FactorMiningDataView` is the sole bridge between `src/factor_mining/`
and the PIT layer. It:

1. Holds the PITDataProvider instance (constructed by the caller).
2. Calls `get_features` once per (field-set, universe, date-range) to
   load the OHLCV panel.
3. Swaplevels and unstacks PIT's `(instrument, datetime)` MultiIndex
   DataFrame into a dict of `(datetime, ticker)` DataFrames per
   field — the layout `operators.py` operates on.
4. Constructs the forward-return label panel through the same PIT
   path (qlib expression `"Ref($open, -h-1) / Ref($open, -1) - 1"`).
5. Exposes a boolean universe mask (per-date tradable membership)
   for downstream coverage / validity checks.

### Why this is the only file that imports PIT

Per the design doc §1 and decisions D5 strict gate: every later
phase's evaluator, GP engine, miner, validator, and handler imports
the panel from this view — never PIT directly. If PIT integration is
wrong, it is wrong in exactly one file (this one), which is easy to
test and fix. The grep-D5 rule remains satisfied because qlib lives
behind `PITDataProvider`; the adapter never types `from qlib …` or
`qlib.init`.

### Forward-return formula

Per `decisions.md` D1 and `factor_mining_design.md` §5.3:

```python
forward_return_h = "Ref($open, -h-1) / Ref($open, -1) - 1"
# h = horizon in trading days; T+1 buy / T+1+h sell
```

The h-prefix offset `-1` accounts for the T+1 settlement gap (buy at
T+1's open, sell at T+1+h's open). This is passed directly to
`PITDataProvider.get_features` as a qlib expression string per
`inventory.md` §A.4.

### Memory budget (informational)

Per `inventory.md` §E.2: csi300 × ~2000 days × 6 fields × float32 ≈
~30 MB per field panel — fine. `all` × ~2000 days × 6 fields ≈ ~250
MB — comfortable for the 256-entry LRU cache. The adapter does not
add any caching beyond what `PITDataProvider` already provides.

## Evaluator (`evaluator.py`)

### `evaluate_expression(expr, panel) -> DataFrame | int`

Recursive walker:

- `Terminal("$close")` → `panel["$close"]` (DataFrame)
- `Terminal("20")` → `20` (int, used by `ts_*` rolling)
- `OperatorCall("cs_rank", (child,))` → `REGISTRY.get("cs_rank").compute_fn(evaluate_expression(child, panel))`

No state, no caching at the Phase 2 layer (the GP engine in Phase 3
will add subtree-caching). The walker is single-pass and stateless.

### `EvaluationResult` (frozen dataclass)

```python
@dataclass(frozen=True)
class EvaluationResult:
    factor_values: pd.DataFrame      # date × ticker
    ic_mean: float                   # mean Pearson IC over dates
    ic_std: float
    ir: float                        # ic_mean / ic_std or nan
    rank_ic_mean: float              # mean Spearman IC
    rank_ic_std: float
    rank_ir: float
    turnover_daily: float            # mean abs(factor_t - factor_{t-1}) over time
    coverage: float                  # fraction of (date, ticker) cells non-NaN
    n_obs_per_day_min: int
```

### IC reuse

Per `inventory.md` §B.3, the evaluator calls
`src.core._ic_utils.compute_ic_for_group(group, method)` directly,
where `group` is one date's `DataFrame[["factor", "ret"]]` with ≥3
rows. The caller (`evaluator.evaluate_factor`) does the
`stack().dropna().groupby(level="datetime").apply(...)` itself; this
is the minimal-friction path that respects the `inventory.md` §B.3
recommendation ("option 2 — just call the underlying primitive").
SignalAnalyzer is NOT used (it bypasses PIT for its own forward-return
fetch per `inventory.md` §B.1).

### IR / RankIR convention

Per `inventory.md` §B.4: when `std_IC > 1e-9`, IR = `ic_mean /
ic_std`; otherwise IR is **NaN**, not 0. This matches both existing
analyzers (`signal_analyzer.py` and `factor_analyzer.py`) so fitness
numbers stay comparable across the codebase.

### Turnover

Mean absolute day-over-day change in the factor value, per ticker,
averaged over date and ticker:

```python
turnover_daily = (factor_values.diff().abs()).mean().mean()
```

(Or `.stack().mean()` for the panel-wide expectation. The exact
estimator is documented in the spec.)

### Coverage

Fraction of (date, ticker) cells in `factor_values` that are
non-NaN, restricted to dates where the universe mask is true.

## Fitness (`fitness.py`)

### Formula (v1 §5.1, adapted)

```
fitness = w_ic       * |ic_mean|
        + w_ir       * ir
        + w_rankic   * |rank_ic_mean|
        - w_turnover * (turnover_daily × 252 × cost_rate)   # D1 annualised
        - w_corr     * novelty_penalty
        - w_complexity * expr_size
```

Where:
- `novelty_penalty` is the max absolute Pearson correlation between
  this factor's `factor_values` and every existing pool factor's
  values (range [0, 1]).
- `expr_size` is the count of AST nodes (terminals + operator calls).
- `cost_rate = 0.003` per D1.
- The IR term is NOT taken absolute (a negative IR means the factor's
  IC is consistently against the label — also useful, fixed by the GP
  via sign-flip if needed, but raw IR carries information about
  stability that abs() would erase).

### `FitnessConfig` defaults (per v1 §5.1)

```python
@dataclass(frozen=True)
class FitnessConfig:
    w_ic: float = 1.0
    w_ir: float = 0.5
    w_rankic: float = 0.5
    w_turnover: float = 0.2
    w_corr: float = 0.8
    w_complexity: float = 0.01
    cost_rate: float = 0.003       # D1 — annualised round-trip
    coverage_min: float = 0.8      # §5.2 hard constraint
    variance_days_frac_min: float = 0.7
    variance_min: float = 1e-6
    extreme_outlier_frac_max: float = 0.05
```

### Validity filters (v1 §5.2 hard constraints)

`passes_validity(result, config)` returns False if any of:

1. **Coverage**: `result.coverage < coverage_min` (default 0.8).
2. **Variance**: `< variance_days_frac_min` fraction of dates have
   cross-sectional std > `variance_min` (i.e. factor is nearly
   constant most of the time).
3. **Sanity**: > `extreme_outlier_frac_max` fraction of cells are
   ±Inf or absolute outliers (|x| > 1e8 say). Operators are
   defensive against Inf already (Phase 1); this is the
   belt-and-suspenders check.

When `passes_validity` is False, `compute_fitness` returns `-inf` so
GP selection never picks it.

(Data-leakage filter (v1 §5.2 item 3, "no operator uses $close at t
to predict return at t") is enforced by the grammar's `Ref` usage,
not at fitness time. Phase 1 already enforces this via the
scale-invariance gate; Phase 2 does not need a separate check.)

## Factor pool (`factor_pool.py`)

### `PoolEntry` (frozen dataclass)

```python
@dataclass(frozen=True)
class PoolEntry:
    expr: Expression
    fitness: float
    ic_mean: float
    ic_std: float
    ir: float
    rank_ic_mean: float
    rank_ir: float
    turnover_daily: float
    coverage: float
    n_obs_per_day_min: int
    expr_size: int
    expr_hash: int                  # convenience, == hash(expr)
```

### `FactorPool` operations

- `add(entry) -> bool` — dedup by `expr_hash`. Returns True if added
  (i.e. not already in pool).
- `__len__()`, `__contains__(expr_hash: int)`.
- `top_k(k, by="fitness")` — sorted top-K entries.
- `correlation_with(factor_values, panel) -> float` — used by fitness's
  novelty term during GP search; returns max abs Pearson correlation
  against existing pool members. Returns 0.0 if pool is empty.
- `save(dir_path)` / `load(dir_path)` — writes
  `factor_pool.parquet` (one row per entry, columns = metric scalars
  + expr_hash) and `factor_expressions.json` (mapping expr_hash →
  serialised expression dict). Round-trips structurally.

### Why parquet + JSON, not a single format

Per `factor_mining_design.md` §6.2:
- `factor_pool.parquet` — tabular metrics, fast to query with
  pandas / pyarrow; the file pandas / qlib pipelines naturally
  consume.
- `factor_expressions.json` — the AST representation; JSON because
  the AST is small and human-inspectable.

The two files share the `expr_hash` join key. Load reads both and
reconstructs `PoolEntry` instances.

### Round-trip guarantee

For any `FactorPool` `pool`, `FactorPool.load(pool.save(d))` is
structurally equal to `pool`. The test asserts this end-to-end.

## Config (`config/factor_mining/default.yaml`)

```yaml
data:
  pit_provider_uri: ""                # operator fills in (inventory.md §F.3)
  delisted_registry_path: ""          # operator fills in
  universe_name: "csi300"
  start_date: "2018-01-01"
  end_date: "2025-12-31"
  features: ["$open", "$high", "$low", "$close", "$volume", "$money"]
  forward_horizon: 1                  # T+1 buy → T+2 sell per D1

fitness:
  w_ic: 1.0
  w_ir: 0.5
  w_rankic: 0.5
  w_turnover: 0.2
  w_corr: 0.8
  w_complexity: 0.01
  cost_rate: 0.003                    # D1 — locked
  coverage_min: 0.8
  variance_days_frac_min: 0.7
  variance_min: 1.0e-6
  extreme_outlier_frac_max: 0.05

compute:
  backend: "cpu"                      # CPU until Phase 4 proves GPU equivalence
```

`pit_provider_uri` and `delisted_registry_path` are empty strings to
make the file usable as a template; the spec requires that any code
that consumes this config rejects empty strings with a clear error
message naming `inventory.md` §F.3 (the "build the PIT bundle"
follow-up task).

## Testing strategy (synthetic data)

Phase 2 tests use a synthetic mini-panel — no PIT bundle required:

- A `_StubPITProvider` class with the same `get_features` signature
  as the real `PITDataProvider`. It returns a hand-built DataFrame
  with `(instrument, datetime)` MultiIndex.
- A small panel: 3-10 tickers × 50-200 dates × 6 fields. Synthetic
  values constructed so the 20-day reversal factor has a known sign
  and approximately known magnitude.

Specific test plans per module are in `tasks.md`.

### Why not real PIT data

`inventory.md` §F.3 documents that the PIT-corrected qlib bin bundle
is **not on disk** on this machine. The operator must build it via
`src/data/pit/qlib_bin_builder.py` before any real-data check can
run. Phase 2 implements correctness-against-contract; the real-data
IC-delta validation is a follow-up task that gates Phase 3 smoke
runs (documented in `tasks.md`).

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| `pit_adapter.py` accidentally imports qlib directly | D5 grep gate runs in `tasks.md`; CI hook addition is its own follow-up task |
| Synthetic-data tests pass but real-data fails on a shape mismatch | The adapter's swaplevel + unstack is unit-tested against the documented `(instrument, datetime)` MultiIndex; the stub mimics that shape exactly |
| Evaluator computes IC wrongly because IR convention drifts | `inventory.md` §B.4 is cited; the test compares IR formula directly to the convention |
| Validity filters silently let a leakage factor through | Phase 1's grammar already enforces no-leakage at type-check; Phase 2's coverage / variance / sanity checks are belt-and-suspenders |
| Factor pool persistence corrupts on hash collision | The hash is a 64-bit Python `hash` of the structural canonical form; collisions are improbable. If they occur, the duplicate is dropped (the existing entry wins). A future Phase 6 follow-up may switch to a content-addressed sha256 for production use |
| `_StubPITProvider` drift from real `PITDataProvider` interface | The stub's signature is asserted against `inspect.signature(PITDataProvider.__init__)` and `.get_features` in a separate test; if `src.pit.query.PITDataProvider` evolves, the test fails and the stub is updated |
| Novelty correlation evaluation cost grows quadratically with pool size | Phase 2's `correlation_with` is a simple O(N) loop over pool members; Phase 3's GP can cache factor values to avoid recomputation; documented as a Phase 3 optimisation, not Phase 2 |

## MODIFIED requirement against `v2-factor-mining-foundations`

The Phase 1 requirement "Phase 1 SHALL NOT access qlib, PIT data, or
any data source" is MODIFIED to clarify scope: the **qlib direct-import
ban** stays absolute (zero `qlib.data` / `qlib.init` / `from qlib`
matches under `src/factor_mining/`). The PIT-import ban is relaxed
ONLY for `src/factor_mining/pit_adapter.py`, which is now the
designated data door. All other files under `src/factor_mining/`
(operators, expression, grammar, evaluator, fitness, factor_pool, and
Phase 3+ modules) continue to be forbidden from importing `src.pit`
or `qlib`.

Concretely:
- Old wording: "Phase 1 code under `src/factor_mining/` SHALL NOT import
  from `qlib`, … SHALL NOT construct or call PITDataProvider."
- New wording: extends the rule with "except `pit_adapter.py`, which
  MAY import `src.pit.query.PITDataProvider` as the designated data
  door." The qlib direct-import ban is preserved for the entire
  subpackage including `pit_adapter.py`.
