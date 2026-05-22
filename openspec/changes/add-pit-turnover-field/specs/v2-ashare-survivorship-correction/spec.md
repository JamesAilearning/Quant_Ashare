## ADDED Requirements

### Requirement: PIT qlib bins SHALL include a turnover-rate field

The PIT-corrected qlib bin storage produced by `QlibBinBuilder` SHALL
include a `turn` field representing daily turnover rate (Tushare
`daily_basic.turnover_rate`, in percent of total shares), written as
a per-ticker `turn.day.bin` aligned to the global calendar alongside
the existing `open`, `high`, `low`, `close`, `volume`, and `money`
fields.

#### Scenario: turnover-rate is populated where Tushare provided a row

- **WHEN** the bin builder consumes a `daily_basic/{year}/{ticker}.parquet`
  containing a `turnover_rate` value for a (ticker, trade_date) cell
  within the ticker's listing window
- **THEN** the corresponding position in `turn.day.bin` SHALL equal
  Tushare's reported `turnover_rate` value (no unit conversion;
  percent preserved)
- **AND** the bin position SHALL be a non-NaN finite float

#### Scenario: turnover-rate is NaN past delist_date

- **WHEN** the bin builder writes `turn.day.bin` for a delisted
  ticker
- **THEN** every position whose calendar date is strictly greater
  than the registry's `delist_date` for that ticker SHALL be NaN
- **AND** the NaN-after-delist contract SHALL apply identically to
  `turn` and to the existing OHLCV+money fields

#### Scenario: turnover-rate is NaN where Tushare returned no row inside the listing window

- **WHEN** the bin builder writes `turn.day.bin` for a ticker on a
  calendar date inside the ticker's listing window for which the
  `daily_basic/` parquet contained no row (e.g. a suspended-trading
  day with no reported turnover)
- **THEN** the bin position SHALL be NaN
- **AND** the bin position SHALL NOT be zero

#### Scenario: turnover-rate gracefully degrades when daily_basic is absent

- **WHEN** the bin builder runs against a Tushare staging directory
  that has no `daily_basic/` subtree (e.g. an ingest predating this
  capability)
- **THEN** the bin builder SHALL still complete successfully and
  produce all 7 `.day.bin` files per ticker
- **AND** the produced `turn.day.bin` SHALL contain NaN for every
  position
- **AND** the other 6 fields SHALL be unaffected

#### Scenario: BIN_FEATURE_FIELDS lists turn as the seventh field

- **WHEN** a caller inspects
  `src.data.pit.qlib_bin_builder.BIN_FEATURE_FIELDS`
- **THEN** the tuple SHALL contain exactly
  `("open", "high", "low", "close", "volume", "money", "turn")`
- **AND** the order MAY be relied upon by downstream consumers that
  enumerate field positions

### Requirement: Phase A.1 Tushare ingest SHALL pull daily_basic alongside daily and adj_factor

The `TushareFetcher` orchestrator SHALL expose `daily_basic` as a
seventh endpoint name in `ENDPOINTS`, and SHALL stage per-ticker /
per-year parquets at
`<output_dir>/daily_basic/{year}/{ticker}.parquet` carrying at minimum
the columns `ts_code`, `trade_date`, and `turnover_rate`.

#### Scenario: default endpoint set includes daily_basic

- **WHEN** an operator runs
  `scripts/data_pipeline/01_fetch_tushare.py` without an explicit
  `--endpoints` override
- **THEN** the fetcher SHALL pull from all 7 endpoints (including
  `daily_basic`)
- **AND** the output directory SHALL contain a populated
  `daily_basic/` subtree on completion

#### Scenario: explicit endpoint subset excludes daily_basic

- **WHEN** an operator passes
  `--endpoints stock_basic,daily,adj_factor` to the Phase A.1 script
- **THEN** the fetcher SHALL NOT call the `daily_basic` API
- **AND** the output directory SHALL NOT contain a `daily_basic/`
  subtree

#### Scenario: re-running Phase A.1 with daily_basic is resumable

- **WHEN** an operator re-runs the Phase A.1 script after a
  previous partial pull
- **THEN** the fetcher SHALL skip per-(year, ticker) parquets that
  already exist on disk
- **AND** SHALL only call the Tushare API for the missing (year,
  ticker) pairs
- **AND** SHALL respect the same `rate_limit_sleep_ms` and retry /
  back-off policy used for `daily` and `adj_factor`
