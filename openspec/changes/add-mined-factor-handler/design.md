# Design: MinedFactor Handler (Phase 5)

> Long-form design at
> `docs/factor_mining/factor_mining_claude_code_design.md` §6 Phase 5
> and `inventory.md` §C.2-C.4 (feature-handler registry). Contract
> decisions are below.

## Module additions

```
src/data/
└── mined_factor_handler.py     # NEW — handler + bundle + registration

research/mined_factors/
└── README.md                   # NEW — D4 output-layout doc

tests/logic/
└── test_mined_factor_handler.py
```

No edits to existing source. The new handler lives under `src/data/`
because (a) D5 forbids any qlib import under `src/factor_mining/` and
(b) `src/data/` is the existing data-layer location for feature
handlers (`feature_dataset_builder.py` is its neighbour).

## Module responsibilities

### `MinedFactorBundle` (frozen dataclass)

```python
@dataclass(frozen=True)
class MinedFactorBundle:
    pool_dir: Path
    # PIT integration (real-run mode)
    pit_provider_uri: str = ""
    delisted_registry_path: str = ""
    # Per-run override (optional; otherwise inherit from FeatureDatasetConfig)
    universe_name_override: str | None = None
```

A bundle captures everything needed to materialise mined factors:
the pool location and (in PIT-mode) the data source bindings. The
empty-string defaults for PIT paths mean "synthetic mode" — tests
inject a synthetic panel directly.

### `make_mined_factor_features(bundle, config) -> pd.DataFrame`

The data-pure core. Pipeline:

1. `pool = FactorPool.load(bundle.pool_dir)` — Phase 2 round-trip.
2. Load the OHLCV panel. Two branches:
   - PIT mode: construct `PITDataProvider(bundle.pit_provider_uri,
     bundle.delisted_registry_path)`, wrap in
     `FactorMiningDataView` over `[config.train_start,
     config.test_end]`, call `view.load_panel()`.
   - Synthetic mode (tests): the caller supplies a pre-built panel
     via the `panel=` kwarg (a Mapping[str, DataFrame]); the bundle's
     PIT fields are ignored.
3. For each `PoolEntry`, evaluate its expression against the panel
   via `evaluator.evaluate_expression(entry.expr, panel)`. Result is
   a date × ticker DataFrame.
4. Stack each result as `(instrument, datetime) → factor_value` and
   concatenate horizontally; column name is `mf_<hex_hash>` where
   `<hex_hash>` is the 64-bit structural hash in hex (8 bytes →
   16 hex chars, lowercase).
5. Return the merged DataFrame with `(instrument, datetime)`
   MultiIndex.

Column-ordering: pool entries are sorted by descending fitness, then
by ascending expr_hash for stability. This matches the
`FactorPool.top_k(by="fitness")` ordering and keeps the column order
deterministic across loads.

### `register_mined_factor_handler(bundle, *, name="MinedFactor",
replace=False)`

Wraps a closure-style factory and calls
`register_feature_handler(name, factory, replace=replace)`. The
factory captures `bundle`; when called by `FeatureDatasetBuilder.build`,
it:

1. Calls `make_mined_factor_features(bundle, config)` to materialise
   the feature panel.
2. Imports qlib lazily inside the function body (`from
   qlib.data.dataset.handler import DataHandlerLP` and `from
   qlib.data.dataset.loader import StaticDataLoader`). The
   top-level import path of `mined_factor_handler` SHALL NOT pull
   qlib.
3. Wraps the panel in a `StaticDataLoader({"feature": feature_df,
   "label": label_df})` and returns a `DataHandlerLP(instruments,
   ..., data_loader=loader)` instance.
4. The label DataFrame is the forward-return panel
   (`view.forward_return(horizon=1)` from the bundle's PIT, or
   synthesised in test mode).

### `MinedFactorHandlerError`

Raised when:
- `bundle.pool_dir` does not exist or is missing
  `factor_pool.parquet` / `factor_expressions.json`.
- PIT mode requested with empty `pit_provider_uri` or
  `delisted_registry_path` — error message points at
  `inventory.md` §F.3.
- The factor pool is empty (no entries; loading an empty pool is
  legal at the FactorPool layer but a handler with zero features is
  rejected here).

### `research/mined_factors/README.md`

A short document covering:
- The runs/, candidates/, production/ directory contract per
  `decisions.md` D4.
- That contents of these directories are NOT checked into git
  (covered by the existing `.gitignore`).
- How to bind a specific pool into the training pipeline:

  ```python
  from src.data.mined_factor_handler import (
      MinedFactorBundle,
      register_mined_factor_handler,
  )

  register_mined_factor_handler(
      MinedFactorBundle(
          pool_dir=Path("research/mined_factors/production/v1"),
          pit_provider_uri="D:/qlib_data/my_cn_data_pit",
          delisted_registry_path="...",
      ),
  )
  # Now PipelineConfig(feature_handler="MinedFactor", ...) works.
  ```

## Spec deltas

### `v2-feature-handler-registry` (MODIFIED)

The existing requirement "Feature handler registration SHALL remain
explicit" remains unchanged in spirit. A NEW requirement is added:

> **The MinedFactor handler SHALL be registered only via an explicit
> `register_mined_factor_handler(bundle)` call.** Importing
> `src.data.mined_factor_handler` SHALL NOT register a default
> MinedFactor handler at module-import time. This preserves the
> existing pattern where the registry seeds Alpha158 at import but
> any custom handler is application-driven.

### `v2-mined-factor-handler` (NEW capability)

Five requirements:

1. **`MinedFactorBundle` SHALL validate its fields**: `pool_dir` is
   a `Path` pointing at a directory containing both
   `factor_pool.parquet` and `factor_expressions.json`; empty
   `pit_provider_uri` or `delisted_registry_path` means "synthetic
   mode" (tests provide the panel directly).
2. **`make_mined_factor_features` SHALL produce one column per pool
   entry**, with column name `mf_<hex_hash>` and the result indexed
   by `(instrument, datetime)` MultiIndex. Column order is fitness
   desc, expr_hash asc.
3. **The handler SHALL lazy-import qlib**: importing
   `src.data.mined_factor_handler` SHALL NOT pull qlib (verified by
   a test that imports the module and asserts `qlib` is not in
   `sys.modules` after the import).
4. **`register_mined_factor_handler` SHALL register a closure-style
   factory** that captures the bundle; the factory is what the
   `FeatureDatasetBuilder` consumes per
   `v2-feature-handler-registry`.
5. **An empty pool SHALL be rejected at handler-construction time**
   with `MinedFactorHandlerError`.

## Testing strategy

All tests use synthetic data — no PIT bundle, no live qlib:

- `MinedFactorBundle.__post_init__` defaults / validation.
- `make_mined_factor_features(bundle, config, panel=synthetic_panel)`
  returns the expected DataFrame shape + column naming.
- Column ordering is fitness-desc + expr_hash-asc (deterministic).
- `register_mined_factor_handler` registers a callable that, when
  called, returns SOMETHING (we don't import qlib in tests, so we
  monkey-patch the qlib imports OR use a separate "no-qlib" path
  that returns the materialised DataFrame instead of a handler —
  the test verifies the factory captures the bundle correctly).
- Empty pool raises `MinedFactorHandlerError`.
- Lazy import: `import src.data.mined_factor_handler` does NOT add
  `qlib` to `sys.modules`.

A `RUN_E2E=1` operator test (not part of CI) would exercise the
qlib branch end-to-end; documented in tasks.md, not committed in
this PR.

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Importing the handler module pulls qlib | Lazy import inside the factory; test asserts `qlib` is not in `sys.modules` post-import |
| Hash collisions between mined factors | The Phase 1 structural hash is 64-bit; collisions are improbable on a v1 pool size (≤ tens of thousands). If a collision occurs, the second add to FactorPool is a no-op (Phase 2 contract); the handler then sees only one entry for that hash. Documented; Phase 6 may switch to content-addressed sha256 |
| qlib StaticDataLoader API drift across qlib versions | The lazy import + thin wrapper localises the surface area; a future qlib upgrade only touches this file, not Phase 1-3 |
| MinedFactor handler used before bind | The factory raises `MinedFactorHandlerError` if called without `register_mined_factor_handler` first |
| Pool path empty or malformed | `MinedFactorBundle` post-init checks existence; FactorPool.load raises FileNotFoundError on missing files |
| Test mode "synthetic panel" leaks into production | The `panel=` kwarg of `make_mined_factor_features` is keyword-only and not part of the registered-factory call path; production runs always go through the bundle's PIT settings |
| Column names with non-identifier characters break qlib | `mf_<hex_hash>` only contains `[a-f0-9_]`, valid as both pandas column names and qlib feature names |
| `make_mined_factor_features` is slow with a large pool | The Phase 3 GP engine's per-generation cache is not used here; the handler is a one-shot materialisation at pipeline start. Acceptable for v1; Phase 6 may add streaming or batched evaluation if needed |
