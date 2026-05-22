# Design: Factor Mining Validation (Phase 6)

> Long-form design at
> `docs/factor_mining/factor_mining_claude_code_design.md` §6 Phase 6
> and `factor_mining_design.md` §6.3 (promotion criteria). Contract
> decisions are below.

## Module additions

```
src/factor_mining/
├── validator.py            # IS/OOS validation, no qlib
└── promote.py              # CLI: validate + filter + copy + report

docs/factor_mining/
└── user_guide.md           # short user-facing quickstart

tests/logic/factor_mining/
├── test_validator.py
└── test_promote.py
```

No edits to Phase 1-5 source modules.

## Validator (`validator.py`)

### `ValidationCriteria` (frozen dataclass)

```python
@dataclass(frozen=True)
class ValidationCriteria:
    is_oos_split_date: str           # ISO; dates < split are IS, >= are OOS
    min_oos_ir: float = 0.3          # decisions.md D4 default
    min_oos_rank_ic_mean: float = 0.02
    max_pool_correlation: float = 0.6
    min_obs_per_segment: int = 30    # minimum dates per IS / OOS segment
```

### `FactorValidationResult` (frozen dataclass)

```python
@dataclass(frozen=True)
class FactorValidationResult:
    expr_hash: int
    expr_str: str
    fitness: float                   # carried from the input PoolEntry
    passes: bool
    reasons: tuple[str, ...]         # per-failure messages
    # IS metrics
    is_n_obs: int
    is_ir: float
    is_rank_ic_mean: float
    # OOS metrics
    oos_n_obs: int
    oos_ir: float
    oos_rank_ic_mean: float
```

### `validate_pool(pool, panel, forward_return, criteria) -> list[FactorValidationResult]`

Walks the pool serially. For each entry:

1. Split the panel into IS and OOS halves on
   `criteria.is_oos_split_date`. Each field's DataFrame is sliced
   along its `DatetimeIndex`.
2. Slice the forward-return panel the same way.
3. Run `evaluator.evaluate_factor(expr, is_panel, is_fwd)` to get IS
   metrics.
4. Run `evaluator.evaluate_factor(expr, oos_panel, oos_fwd)` to get
   OOS metrics.
5. Check criteria:
   - `oos_n_obs >= min_obs_per_segment` (else reject — too short)
   - `abs(oos_ir) >= min_oos_ir` (NaN treated as 0)
   - `abs(oos_rank_ic_mean) >= min_oos_rank_ic_mean`
6. Build `FactorValidationResult` with `passes` and the list of
   failure reasons (or empty tuple on pass).

### `filter_correlated(results, panel, criteria) -> list[FactorValidationResult]`

Pool-level pairwise filter (run **after** per-factor validation):

1. Sort results by `fitness` desc.
2. Maintain a running list of kept results and their factor-value
   DataFrames.
3. For each result, compute max abs Pearson correlation against
   every already-kept result's factor values (computed against the
   full panel, not just OOS).
4. Drop the result if `max_corr > max_pool_correlation`; otherwise
   keep it and append a `correlated_with_higher_fitness` reason to
   the dropped result.

The output preserves both pass/fail and pre-existing reasons; the
correlation filter ADDS reasons rather than replacing them.

### Overfit-rejection demonstration (acceptance test)

The Phase 6 acceptance criterion ("Catches synthetic overfit factor:
high IS IR, ~0 OOS IR") is implemented as a unit test:

1. Construct a synthetic panel where `$volume` is exactly the
   forward-return on IS dates and pure noise on OOS dates.
2. Build a single-factor pool `cs_rank($volume)`. On IS this factor
   has rank-IC ≈ 1.0 every day → IS IR is very large (or NaN due to
   zero std — handled by `_ir` returning NaN). On OOS the factor is
   noise → OOS IR ≈ 0.
3. `validate_pool(pool, panel, fwd, criteria)` returns the result
   with `passes=False` and reason listing `oos_ir_below_threshold`.

### `validate_run(run_dir, panel, forward_return, criteria)`

Convenience wrapper: `FactorPool.load(run_dir)` then `validate_pool`.

## Promotion CLI (`promote.py`)

### `PromotionConfig` (frozen dataclass)

```python
@dataclass(frozen=True)
class PromotionConfig:
    run_dir: Path                                    # source run
    production_dir: Path                             # research/mined_factors/production
    version: str                                     # e.g. "v1" → production/v1/
    criteria: ValidationCriteria
    data: PromotionDataConfig                        # mirrors miner's DataConfig
```

### `PromotionDataConfig` (frozen dataclass)

Mirrors `miner.DataConfig` so the promote CLI loads the same kind of
panel that the miner used:

```python
@dataclass(frozen=True)
class PromotionDataConfig:
    mode: str                       # "synthetic" | "pit"
    synthetic_n_tickers: int = 30
    synthetic_n_dates: int = 200    # bigger than miner's smoke so IS/OOS split works
    synthetic_seed: int = 7
    pit_provider_uri: str = ""
    delisted_registry_path: str = ""
    universe_name: str = "csi300"
    start_date: str = "2018-01-01"
    end_date: str = "2025-12-31"
    forward_horizon: int = 1
```

### `PromotionReport` (frozen dataclass)

```python
@dataclass(frozen=True)
class PromotionReport:
    run_dir: Path
    output_dir: Path | None         # None on dry-run
    version: str
    n_pool: int
    n_passed_individual: int        # per-factor validation
    n_kept_after_correlation: int   # final survivors
    results: tuple[FactorValidationResult, ...]
```

### `promote_run(config, *, dry_run=False) -> PromotionReport`

Orchestrates:

1. Load pool from `config.run_dir`.
2. Build panel + forward return via `_build_panel(config.data)` —
   re-uses the miner's synthetic / PIT branch logic (copied into
   promote.py to avoid coupling).
3. Run `validate_pool(...)` → per-factor results.
4. Filter to passing → run `filter_correlated(...)` → final
   survivors.
5. If not dry-run:
   - Create `production_dir/version/`.
   - Build a new `FactorPool` from survivors and save it via the
     Phase 2 `pool.save(...)` (writes `factor_pool.parquet` +
     `factor_expressions.json`).
   - Write `promotion_report.json` next to the pool.
6. Return `PromotionReport`.

### CLI

```
python -m src.factor_mining.promote --run RUN_DIR --to VERSION [--config CONFIG] [--dry-run]
```

- `--run`: path to a Phase 3 miner run directory.
- `--to`: production version label (e.g. `v1`); becomes
  `research/mined_factors/production/v1/`.
- `--config`: optional YAML config that supplies
  `PromotionConfig.data` and `criteria`. When omitted, defaults to
  synthetic-mode + the `ValidationCriteria` defaults.
- `--dry-run`: print the report without writing.

The CLI prints a one-line summary on success
(`Promotion complete: 5/12 factors kept → production/v1/`) and
exits 0. On a per-factor validation failure it still exits 0 (the
CLI's job is to filter, not to halt). It exits non-zero only when
the run directory cannot be loaded or the configuration is invalid.

## User guide (`docs/factor_mining/user_guide.md`)

Short — under 200 lines. Sections:

1. **Quickstart** (synthetic data, no PIT bundle required):
   - `python -m src.factor_mining.miner config/factor_mining/smoke.yaml`
   - `python -m src.factor_mining.promote --run research/mined_factors/runs/<id> --to v1`
   - `register_mined_factor_handler(MinedFactorBundle(pool_dir=...))`
2. **Real-PIT path**:
   - Build the PIT bundle per `inventory.md` §F.3.
   - Fill in `config/factor_mining/default.yaml`.
   - Same miner + promote sequence, with `data.mode: pit`.
3. **What each artifact means**:
   - `factor_pool.parquet`: metric table.
   - `factor_expressions.json`: AST representation.
   - `gp_history.json`: per-generation stats.
   - `promotion_report.json`: per-factor accept/reject reasons.
4. **Cross-references**: design baseline, scale-invariance rules,
   decisions doc.

## Spec deltas

### `v2-factor-mining-foundations` MODIFIED + ADDED

MODIFIED: the existing "Phase 1 SHALL NOT access qlib" requirement
is extended to explicitly cover `validator.py` and `promote.py`.
Both modules SHALL NOT import qlib; PIT-mode data flows through
`FactorMiningDataView`.

ADDED (4 new requirements):
1. **Validator IS/OOS split contract**: the validator SHALL slice
   the panel and forward-return on
   `ValidationCriteria.is_oos_split_date` and SHALL refuse to score
   a segment with fewer than `min_obs_per_segment` observations.
2. **Overfit rejection**: a factor with `oos_ir < min_oos_ir` OR
   `|oos_rank_ic_mean| < min_oos_rank_ic_mean` SHALL fail
   validation; the failure SHALL list each violated criterion in
   `reasons`.
3. **Pool-level correlation filtering**: `filter_correlated` SHALL
   drop a factor when its correlation against any already-kept
   higher-fitness factor exceeds `max_pool_correlation`.
4. **Promotion manual-gate**: `promote_run` SHALL NEVER promote
   silently; the `--dry-run` flag SHALL produce a report without
   writing to disk; explicit invocation SHALL write
   `factor_pool.parquet` + `factor_expressions.json` +
   `promotion_report.json` under the version directory.

## Testing strategy

- Validator: synthetic panel with engineered overfit pattern;
  synthetic panel with consistent factor (passes); IS/OOS too-short
  segments; pool-level correlation filtering with two
  highly-correlated factors.
- Promote: dry-run prints report; full run writes the production
  directory; CLI exits zero on a clean run; CLI exits non-zero on a
  missing run dir; same-input runs are deterministic.

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Validator silently passes a factor with NaN IR | NaN IR is treated as 0 by `abs(...) >= min_oos_ir` (0 < 0.3 → fails) |
| Overfit test passes by chance even though the validator is broken | The synthetic overfit pattern is engineered to be deterministic with seed; rank correlation is mathematically forced to 1.0 on IS and ~0 on OOS |
| `filter_correlated` uses panel-wide correlation that masks regime changes | v1 keeps it simple; Phase 6.x may add regime-aware filtering |
| CLI accidentally over-promotes on a small pool | The CLI is manual-gated; the dry-run flag is the recommended first invocation |
| Production directory overwrite | The CLI refuses to write into an existing version directory unless `--force` is supplied (NOT in v1 scope; v1 raises) — operator chooses a new version label |
