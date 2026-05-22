# Add Mined Factor Handler — pool → qlib feature handler bridge

## Why

Phase 3 (`add-factor-mining-gp-engine`, archived) ships the GP miner
that writes a factor pool to disk. Phase 5 bridges that pool into the
existing training pipeline by providing a qlib-compatible feature
handler that materialises mined-factor values as a feature panel.

Per `docs/factor_mining/factor_mining_claude_code_design.md` §6
Phase 5:

- 5.1 `MinedFactorHandler` — reads pool from disk, plugs into the
  existing `register_feature_handler` registry boundary
  (`inventory.md` §C.2), produces a qlib-compatible handler whose
  features come from the mined-factor library parquet rather than
  from `Alpha158`.
- 5.2 Config wiring — a training run can select
  `feature_handler: "MinedFactor"` in its `PipelineConfig` and the
  builder dispatches to the new factory.

It also adds `research/mined_factors/README.md` to document the
output-layout contract (referenced by `decisions.md` D4).

### Why this stays a separate phase

Phase 5's risk surface is the **integration boundary with the qlib
runtime**, not the factor-mining loop itself. Bundling Phase 5 with
the GP engine would couple "search loop" concerns with
"qlib-handler" concerns; keeping them separate respects the "one
phase = one OpenSpec change" rule and lets the qlib boundary be
reviewed in isolation.

### Why the handler lives under `src/data/`, not `src/factor_mining/`

`decisions.md` D5 strict gate forbids any `qlib.data`, `qlib.init`,
or `from qlib` match anywhere under `src/factor_mining/`. The
MinedFactor handler MUST import qlib (it returns a `qlib.data.dataset.handler`
instance to fit the `FeatureHandlerFactory` callable contract from
`inventory.md` §C.2). Therefore the handler module lives under
`src/data/mined_factor_handler.py` — the existing data-layer location
for feature-handler factories — and `src/factor_mining/` continues to
hold the operator / expression / GP code that is data-pure.

The dependency direction is one-way: `src/data/mined_factor_handler.py`
imports from `src/factor_mining/` (Expression / FactorPool /
evaluator), but `src/factor_mining/` does NOT import from
`src/data/mined_factor_handler.py`.

### Why an explicit `bind_mined_factor_pool` step

A `FeatureHandlerFactory` callable's only argument is a
`FeatureDatasetConfig`, which carries `instruments` and date splits
but no pool location or PIT bundle path. Adding mined-factor-specific
fields to `FeatureDatasetConfig` would pollute the contract every
existing handler shares. Instead, Phase 5 introduces an explicit
"bind" step: the application calls
`register_mined_factor_handler(bundle)` at startup, where the
`MinedFactorBundle` carries the pool directory plus optional PIT
parameters. The closure captures the bundle; the registered factory
is then a closed-over callable that satisfies the registry's
`FeatureHandlerFactory` signature.

### Why end-to-end pipeline acceptance is operator-gated

The design doc's Phase 5 acceptance criterion ("Full pipeline run
with mined factors completes and produces a backtest") requires a
real qlib + PIT bundle on disk. `inventory.md` §F.3 documents that
the PIT bundle is not yet on this machine. Phase 5 ships the handler
API and its synthetic-data tests; the real-pipeline run is a
follow-up operator task (also recorded in
`add-factor-mining-evaluator` archived tasks.md) that gates Phase
6's validator.

## What Changes

- **Add `src/data/mined_factor_handler.py`** — new module providing:
  - `MinedFactorBundle` frozen dataclass (pool_dir, optional
    pit_provider_uri + delisted_registry_path, optional
    universe_name override). The bundle binds a registered handler
    to a specific factor pool + data source.
  - `make_mined_factor_features(bundle, config) -> pd.DataFrame` —
    the data-pure core: loads the pool (`FactorPool.load`), evaluates
    each expression against a PIT-loaded panel (or synthetic for
    tests), returns a `(instrument, datetime)` MultiIndex DataFrame
    with one column per mined factor (column name
    `mf_<hex_hash>`).
  - `register_mined_factor_handler(bundle, *, name="MinedFactor",
    replace=False)` — at app startup, this builds a closure-style
    `FeatureHandlerFactory` over `bundle` and registers it via
    `src.data.feature_dataset_builder.register_feature_handler`.
    Calling the factory returns a `qlib.data.dataset.handler.DataHandlerLP`
    wrapping the materialised features via
    `qlib.data.dataset.loader.StaticDataLoader`.
  - `MinedFactorHandlerError` exception for malformed bundles /
    unloadable pools.

- **Add `research/mined_factors/README.md`** — documents the v1
  output-layout contract per `decisions.md` D4 (runs / candidates /
  production directories). Read by operators promoting a run; no
  runtime code reads this README.

- **MODIFY `v2-feature-handler-registry`** — extend the registry
  spec to recognise the MinedFactor handler family. Existing
  requirements (registry-resolves-by-name, registration-is-explicit,
  unknown-handler-raises) are preserved unchanged; a new requirement
  is added stating that MinedFactor is registered only via the
  explicit `register_mined_factor_handler(bundle)` call (no
  registration happens at module import time).

- **ADD new capability `v2-mined-factor-handler`** covering:
  - `MinedFactorBundle` shape and validation
  - `make_mined_factor_features` contract (returns
    `(instrument, datetime)` MultiIndex, columns per mined factor)
  - `register_mined_factor_handler` registration flow
  - Pool-source-of-truth invariant: factor expressions are loaded
    from `factor_expressions.json`, NOT recomputed from
    `factor_pool.parquet` metric values (the parquet is metrics
    only).
  - Lazy qlib import: importing
    `src/data/mined_factor_handler` SHALL NOT pull qlib (qlib is
    imported only inside the factory body, when called by the
    builder).

## Non-Goals

- **No edit to `src/factor_mining/`** — Phase 1-3 modules are
  upstream; Phase 5 imports from them as a stable layer.
- **No edit to `src/data/feature_dataset_builder.py`** — the
  registry boundary is the only contact point; the existing
  builder behaviour is preserved.
- **No edit to `src/pit/`, `src/data/pit/`, `src/core/_ic_utils.py`,
  `src/data/feature_dataset_builder.py`.** Phase 5 is purely
  additive in `src/data/mined_factor_handler.py` plus
  `tests/logic/test_mined_factor_handler.py` plus a README.
- **No real-data pipeline run.** The PIT bundle is not on this
  machine (`inventory.md` §F.3). Phase 5 ships the handler API
  verified by synthetic-data unit tests; the real-pipeline
  end-to-end run is an operator follow-up that gates Phase 6.
- **No GPU code.** Phase 4 was skipped (per Phase 3's tasks.md).
- **No promotion CLI, no validator.** Phase 6.
- **No edit to `decisions.md` or `inventory.md`.** D4
  (mined-factor directory layout) is the existing source of truth;
  Phase 5 only ADDS the README that D4 mentions.
- **No automatic registration at module load.** Importing
  `src/data/mined_factor_handler` SHALL NOT register a default
  MinedFactor binding. The application must call
  `register_mined_factor_handler(bundle)` explicitly. This avoids
  pulling qlib into any import path that doesn't actually need it.
- **No multi-pool handler.** v1 binds one bundle per registered
  name. Multi-run / multi-pool setups can re-register under
  different names (e.g. `MinedFactor:prod` and `MinedFactor:dev`).
