## ADDED Requirements

### Requirement: Delisted registry SHALL list one row per delisted ticker

The delisted registry SHALL contain exactly one row per ticker that has
ever delisted, sourced from Tushare `stock_basic(list_status='D')`. The
registry SHALL NOT contain currently-active tickers, and SHALL NOT model
entities, ticker reuse, or `entity_id` (those concepts do not apply to
A-share).

#### Scenario: registry is built from Tushare delisted bucket

- **WHEN** the registry builder consumes `stock_basic(list_status='D')`
- **THEN** every returned `ts_code` appears exactly once in the registry
- **AND** every row has a non-NULL `delist_date`
- **AND** no row's `ticker` appears in `stock_basic(list_status='L')`

#### Scenario: a currently-active stock is queried against the registry

- **WHEN** a ticker with `list_status='L'` (e.g. `SH600519` 贵州茅台) is
  looked up
- **THEN** the ticker is NOT present in the registry
- **AND** the lookup returns "active, no delist date"

### Requirement: Local bin SHALL contain NaN after a delisted ticker's delist_date

For any ticker in the delisted registry, the qlib bin storage SHALL
contain NaN values for OHLCV and derived fields on every trading date
strictly after `delist_date`. This prevents the "stale local bin"
failure mode where queries on delisted tickers return non-NaN values
from a forward-filled or mis-merged snapshot.

#### Scenario: query the last trading day of a delisted ticker

- **WHEN** a caller queries `D.features([ticker], ['$close'], …)` on the
  ticker's `delist_date`
- **THEN** the returned value is valid (non-NaN)
- **AND** matches the close price on that day

#### Scenario: query a date strictly after delist

- **WHEN** a caller queries `D.features([ticker], ['$close'], …)` on any
  date strictly greater than `delist_date`
- **THEN** the returned value is NaN
- **AND** the bin contains no forward-filled continuation past delisting

### Requirement: PIT contract SHALL forbid absolute adjusted prices as features

The capability SHALL restrict feature expressions to within-ticker ratios
and returns; absolute adjusted prices SHALL NOT be used as features. This
follows from Tushare's `adj_factor` endpoint returning today's snapshot
(non-PIT); within-ticker ratios cancel the as-of-date `adj_factor` in
numerator and denominator and are therefore safe.

#### Scenario: feature expression uses absolute adjusted price

- **WHEN** a feature expression evaluates to an absolute adjusted price
  level (e.g. raw `$close` consumed directly as a model input)
- **THEN** the feature is rejected by the contract
- **AND** the rejection message names within-ticker-ratio / return
  alternatives

#### Scenario: feature expression spans a ticker's delist boundary

- **WHEN** a time-series operator's window would otherwise consume rows
  from before and strictly-after a ticker's `delist_date`
- **THEN** the NaN-after-delist invariant causes the operator's result
  to be NaN at every position whose window crosses the boundary
- **AND** the contract treats any non-NaN result at such a position as
  a validation failure

### Requirement: qlib operator min_periods SHALL be validated against delist boundary

The Stage 6.D validation SHALL exercise real qlib operators
(`Mean($close, N)`, `Ref($close, N)`, `Corr(...)`) — not pandas
`rolling` — against a delisted ticker on days strictly after
`delist_date`. The operator MUST return NaN. Any qlib operator that
silently honours `min_periods < N` is either wrapped with explicit
`min_periods=N` in the expression layer or banned from feature
expressions.

#### Scenario: qlib Mean operator is tested after delisting

- **WHEN** `D.features([ticker], ['Mean($close, 20)'],
  start=delist_date + 1, end=delist_date + 10)` is executed on a
  delisted ticker
- **THEN** every returned value is NaN
- **AND** the test cites design §4.3.2 in the failure message if any
  value is non-NaN

### Requirement: PIT query layer SHALL expose universe-aware queries

`PITDataProvider` at `src/pit/query.py` SHALL expose
`get_universe(date, universe_name)`, `get_universe_range(...)`, and
`get_features(fields, start, end, universe_name, align)`. Queries
SHALL be PIT-correct: no future-listed ticker appears in
`get_universe(date)`; no past-delisted ticker appears; time-series
operations respect the delist boundary via the NaN-after-delist
invariant. The query layer SHALL NOT expose a `resolve_entity` method
(no entity model).

#### Scenario: universe at a past date excludes future listings

- **WHEN** `get_universe(date_X, universe_name)` is called
- **THEN** every returned ticker has `list_date <= date_X`
- **AND** no returned ticker has a populated `delist_date <= date_X`

#### Scenario: a delisted ticker is queried after its delist_date

- **WHEN** `get_universe(date_X, "all")` is called and `date_X` is
  strictly after a ticker's `delist_date`
- **THEN** that ticker is NOT in the returned set

#### Scenario: query layer surface is inspected for resolve_entity

- **WHEN** a maintainer inspects the public API of `PITDataProvider`
- **THEN** there is NO `resolve_entity` method
- **AND** ticker is the stable identifier across the API surface

### Requirement: PIT query layer SHALL use a bounded LRU cache

The PIT query layer SHALL cache feature query results with an LRU
policy and a bounded `cache_max_entries` parameter (default 256).
Unbounded dict caches SHALL NOT be used.

#### Scenario: cache eviction is triggered

- **WHEN** more than `cache_max_entries` distinct
  `(universe_name, start_date, end_date, frozenset(fields))` queries
  have been executed
- **THEN** the least-recently-used entry is evicted
- **AND** the cache size does not exceed `cache_max_entries`

### Requirement: Legacy provider SHALL be preserved untouched

The existing `D:/qlib_data/my_cn_data` provider SHALL NOT be deleted,
overwritten, or retroactively modified by any code under this
capability. The new corrected provider is written to a separate
directory. Both providers remain queryable indefinitely.

#### Scenario: a pipeline script attempts to modify the legacy provider

- **WHEN** any script under `scripts/data_pipeline/` writes to a path
  under the legacy provider root
- **THEN** the contract rejects the operation
- **AND** the script aborts before any byte is written

#### Scenario: a destructive finalization step is requested

- **WHEN** a future migration finalization script is invoked without
  `--confirm-destructive`
- **THEN** the script exits before any destructive action
- **AND** the script supports a `--dry-run` mode

### Requirement: Borrow-shell restructure SHALL NOT be modelled in the price layer

The capability SHALL NOT inject NaN gaps, split a ticker into multiple
"entities", or otherwise discontinue the price series at an A-share
borrow-shell restructure date. A borrow-shell restructure preserves
ticker continuity by exchange convention (reverse-merger asset injection
under the original ticker). Restructure events MAY be annotated for
attribution purposes via the existing `PURPOSE_ATTRIBUTION` enum in
`attribution_industry_loader.py`, but SHALL NOT influence price-series
PIT correctness.

#### Scenario: a borrow-shell ticker is queried across the restructure date

- **WHEN** `D.features([ticker], ['$close'], …)` is called spanning a
  date range that includes a known borrow-shell restructure event
- **THEN** the returned series is continuous (no NaN gap, no split)
- **AND** the close value before the restructure date matches the
  pre-restructure shell's last trade
- **AND** the close value after the restructure date matches the
  post-restructure (renamed, new-asset) entity's trade

#### Scenario: a feature consumer requests restructure annotation

- **WHEN** a feature consumer requests restructure event metadata
- **THEN** the metadata is available only via
  `PURPOSE_ATTRIBUTION` consumers
- **AND** `PURPOSE_TRAINING` consumers cannot access the annotation

### Requirement: Capability SHALL declare out-of-scope dimensions explicitly

The capability SHALL declare the following as Phase E+ backlog and
SHALL NOT silently extend them into Phase A-D scope: **entity model /
ticker reuse modelling** (excluded by construction — A-share has no
ticker reuse), industry classification (Shenwan L1/L2) PIT,
fundamentals (PE / PB / ROE / financial statements) PIT, outstanding
shares / market cap PIT, ST / *ST status snapshots within an active
listing, and risk-model factor exposures.

#### Scenario: a follow-up PR proposes an entity-model field

- **WHEN** any follow-up PR under Phases A-D adds an `entity_id`,
  `reuse_count`, or similar field that splits a ticker's price series
- **THEN** the reviewer rejects the PR
- **AND** cites this requirement and the A-share-no-ticker-reuse rule
  in `docs/pit/pit_universe_design.md`

#### Scenario: a follow-up PR proposes a Phase E+ dimension

- **WHEN** any follow-up PR under Phases A-D adds code dependent on
  historical industry reclassification, fundamentals publication
  dates, share-count snapshots, in-listing ST status, or risk-model
  exposures
- **THEN** the reviewer rejects the PR
- **AND** the work is moved to a dedicated PHASE-E.N ticket

### Requirement: Reference cases YAML SHALL be user-curated and cover the delisting era matrix

The seed entries of `tests/pit/reference_cases.yaml` SHALL be committed
by the user as Phase 0.2 and SHALL cover the delisting era coverage
matrix defined in `docs/pit/pit_universe_design.md`. The seed SHALL
NOT be agent-generated. The count target is a function of coverage
(~8 cases minimum, not a fixed ≥10). Agent additions in Phase A.3 or
later SHALL cite the Tushare API response (`stock_basic`,
`namechange`, or `index_weight` row) in the PR body, per row.

#### Scenario: a PR adds reference rows without per-row citation

- **WHEN** a PR adds new entries to `tests/pit/reference_cases.yaml`
- **AND** any entry lacks a cited Tushare API response in the PR body
- **THEN** the reviewer rejects the PR
- **AND** previously-cited rows on the same PR may stay

#### Scenario: the Phase 0.2 seed is missing

- **WHEN** any Phase A test that depends on `reference_cases.yaml` is
  executed before the user has committed the Phase 0.2 seed
- **THEN** the test fails with a message naming the missing seed file
- **AND** the failure message points to the Phase 0.2 task in
  `openspec/changes/add-ashare-survivorship-correction/tasks.md`

#### Scenario: the seed lacks coverage of a required era

- **WHEN** the committed seed lacks any case from a row of the
  coverage matrix (e.g. no 2024+ post-退市新规 case)
- **THEN** the reviewer of the Phase 0.2 PR rejects the seed as
  incomplete
- **AND** lists the missing era(s) by name
