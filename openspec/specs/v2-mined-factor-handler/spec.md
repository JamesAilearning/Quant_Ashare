# v2-mined-factor-handler Specification

## Purpose
TBD - created by archiving change add-mined-factor-handler. Update Purpose after archive.
## Requirements
### Requirement: `MinedFactorBundle` SHALL validate its pool directory

`MinedFactorBundle` SHALL be a frozen dataclass with fields `pool_dir: Path`, `pit_provider_uri: str = ""`, `delisted_registry_path: str = ""`, and `universe_name_override: str | None = None`. The `__post_init__` (or equivalent constructor-time validation) SHALL verify that `pool_dir` exists and contains both `factor_pool.parquet` and `factor_expressions.json`. Empty `pit_provider_uri` / `delisted_registry_path` indicate "synthetic mode" (tests supply the panel directly to `make_mined_factor_features`); the constructor SHALL NOT reject empty PIT fields at bundle-creation time, but the PIT-mode factory branch SHALL raise a clear error pointing at `inventory.md` §F.3 if invoked with empty PIT fields.

#### Scenario: bundle pointing at a valid pool directory
- **WHEN** `MinedFactorBundle(pool_dir=path_with_pool)` is constructed where the directory contains both pool files
- **THEN** no exception is raised
- **AND** the bundle's attributes match the inputs

#### Scenario: bundle pointing at a missing directory
- **WHEN** `MinedFactorBundle(pool_dir=nonexistent_path)` is constructed
- **THEN** `MinedFactorHandlerError` is raised with a message naming the missing path

### Requirement: `make_mined_factor_features` SHALL produce one column per pool entry

`make_mined_factor_features(bundle, config, *, panel=None, forward_return=None)` SHALL load the factor pool via `FactorPool.load(bundle.pool_dir)`, evaluate each `PoolEntry`'s expression against the supplied or PIT-loaded panel, and return a `pd.DataFrame` indexed by a `(datetime, instrument)` MultiIndex with one column per entry. Column names SHALL follow the pattern `mf_<hex_hash>` where `<hex_hash>` is the 64-bit `hash(entry.expr)` rendered as 16 lowercase hex characters. Columns SHALL be ordered by descending `fitness`, then ascending `expr_hash` for ties — stable across loads given Phase 1's deterministic structural hash. The MultiIndex level order is `(datetime, instrument)` (NOT `(instrument, datetime)`) because qlib's `StaticDataLoader.load(instruments, start_time, end_time)` runs `df.loc(axis=0)[start_time:end_time, instruments]`, which treats level 0 as the datetime axis and level 1 as the instrument filter; supplying the reverse order made pandas try to look up ticker codes against the datetime level and raised `KeyError: '<ticker>'` deep inside the first fold of a real walk-forward run. The returned DataFrame's index SHALL be monotonically increasing for the same `StaticDataLoader` reason (it assumes a sorted index).

#### Scenario: three-entry pool produces a three-column feature DataFrame
- **WHEN** `make_mined_factor_features` is called on a pool with three entries (synthetic panel)
- **THEN** the returned DataFrame has exactly three columns, each named `mf_<hex_hash>`
- **AND** the column order is consistent with fitness desc, expr_hash asc

#### Scenario: the returned DataFrame's index shape
- **WHEN** `make_mined_factor_features` is called on a non-empty pool
- **THEN** the result is indexed by `(datetime, instrument)` MultiIndex
- **AND** the index level names are exactly `("datetime", "instrument")`
- **AND** the level-0 values are `pd.Timestamp` instances
- **AND** the level-1 values are ticker-code strings
- **AND** the index is monotonically increasing

### Requirement: Importing `src/data/mined_factor_handler.py` SHALL NOT pull qlib

The module SHALL be importable without qlib being available; the qlib import SHALL happen lazily inside the registered factory body (i.e. only when `FeatureDatasetBuilder.build` calls the factory). The module's top-level body SHALL NOT contain `from qlib …` or `import qlib …`.

#### Scenario: importing the module
- **WHEN** an application runs `import src.data.mined_factor_handler`
- **THEN** the import succeeds without raising
- **AND** `"qlib"` and any submodule (e.g. `"qlib.data"`, `"qlib.contrib"`) is not present in `sys.modules`

### Requirement: `register_mined_factor_handler` SHALL register a closure-style factory

`register_mined_factor_handler(bundle, *, name="MinedFactor", replace=False)` SHALL build a factory closure that captures `bundle` and SHALL register it under `name` via `src.data.feature_dataset_builder.register_feature_handler`. The registered factory's signature SHALL satisfy the existing `FeatureHandlerFactory = Callable[[FeatureDatasetConfig], Any]` contract from `v2-feature-handler-registry`.

#### Scenario: registering a bundle
- **WHEN** `register_mined_factor_handler(MinedFactorBundle(pool_dir=...))` is called
- **THEN** `"MinedFactor"` appears in `list_supported_feature_handlers()`
- **AND** the factory captures the bundle by closure (a second call with a different bundle and `replace=True` SHALL change the bound bundle without raising)

#### Scenario: re-registering without replace
- **WHEN** `register_mined_factor_handler(bundle_b)` is called after a successful prior registration and `replace=False`
- **THEN** `FeatureDatasetBuilderError` is raised
- **AND** the original bundle's factory remains active

### Requirement: An empty pool SHALL be rejected at handler-construction time

`make_mined_factor_features` (and therefore the factory closure that calls it) SHALL raise `MinedFactorHandlerError` when the loaded `FactorPool` has zero entries. The error message SHALL guide the operator toward running the Phase 3 miner first.

#### Scenario: pool with zero entries
- **WHEN** `make_mined_factor_features(bundle, config, panel=panel)` is called on a freshly-saved empty pool
- **THEN** `MinedFactorHandlerError` is raised
- **AND** the message references the Phase 3 miner CLI (`python -m src.factor_mining.miner …`)

### Requirement: Handler factory SHALL resolve universe-name strings to ticker lists before passing to qlib

`_make_qlib_handler(features, label, config)` (the closure body invoked by the registered factory) SHALL inspect `config.instruments`. If it is a `str` (a qlib universe name such as `"csi300"`, `"csi500"`, `"csi800"`, `"all"`), the handler SHALL call `qlib.data.D.list_instruments(qlib.data.D.instruments(name), start_time=config.train_start, end_time=config.test_end, as_list=True)` and pass the resolved ticker list to `DataHandlerLP(instruments=…, …)`. If `config.instruments` is already a non-string sequence (a list or tuple of ticker codes), the handler SHALL pass it through unchanged WITHOUT calling `D.list_instruments`. This mirrors qlib's built-in handler convention: `StaticDataLoader.load(instruments, …)` runs `df.loc(axis=0)[…, instruments]` and treats `instruments` as a level-1 filter on the MultiIndex (not a universe name); passing a raw string like `"csi300"` makes pandas look up a row whose level-1 value is exactly `"csi300"`, which doesn't exist, raising `KeyError: 'csi300'`.

#### Scenario: handler resolves "csi300" via D.list_instruments
- **WHEN** `_make_qlib_handler` is called with a `FeatureDatasetConfig` whose `instruments == "csi300"` and a stubbed `qlib.data.D` whose `list_instruments` returns `["SH600000", "SH600519", "SZ000001"]`
- **THEN** the constructed `DataHandlerLP` receives `instruments=["SH600000", "SH600519", "SZ000001"]`
- **AND** `D.list_instruments` was called with `start_time=config.train_start` and `end_time=config.test_end`
- **AND** the raw string `"csi300"` is NOT passed to `DataHandlerLP`

#### Scenario: explicit ticker list passes through unchanged
- **WHEN** `_make_qlib_handler` is called with a config whose `instruments == ["SH600519", "SH600036"]`
- **THEN** the constructed `DataHandlerLP` receives `instruments=["SH600519", "SH600036"]`
- **AND** `D.list_instruments` is NOT called (no re-resolution)

