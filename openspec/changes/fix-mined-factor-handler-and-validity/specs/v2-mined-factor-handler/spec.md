## MODIFIED Requirements

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

## ADDED Requirements

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
