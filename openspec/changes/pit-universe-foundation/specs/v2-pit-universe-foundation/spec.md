## ADDED Requirements

### Requirement: PIT capability SHALL define entity identity distinct from ticker

The PIT universe foundation SHALL treat `entity_id` as the unique company
instance identifier and `ticker` as the (potentially reusable) market code.
At any given date, a ticker maps to at most one entity; over history a ticker
MAY map to multiple entities.

#### Scenario: ticker reuse mapping is inspected

- **WHEN** a maintainer queries the entity registry for a ticker with
  `reuse_count >= 2`
- **THEN** multiple rows exist for the ticker, each with its own
  `entity_id`, `list_date`, and `delist_date`
- **AND** the rows do not have overlapping `[list_date, delist_date]` periods
- **AND** each `entity_id` value is unique across the registry

#### Scenario: registry invariants are evaluated

- **WHEN** the entity registry is validated
- **THEN** `(ticker, list_date)` is unique
- **AND** `(ticker, delist_date)` is unique with NULL treated as +∞
- **AND** no entity has `delist_date < list_date`
- **AND** the gap between consecutive entities on the same ticker is ≥ 30
  days OR the row is cited as a known-short-gap case in the reference cases
  YAML

### Requirement: PIT bin storage SHALL separate entity periods with NaN gaps

The qlib bin storage SHALL contain NaN values for OHLCV and derived fields on
every trading date between consecutive entity periods on a ticker whose
entity registry has `reuse_count >= 2`. This is the structural defence that
prevents qlib time-series operators from reading across entity boundaries.

#### Scenario: a reused-ticker gap is read from the new provider

- **WHEN** a caller queries `D.features([ticker], ['$close'], start, end)` on
  a date strictly between an entity's `delist_date` and the next entity's
  `list_date`
- **THEN** the returned value is NaN
- **AND** no value from the prior entity bleeds into the next entity's window

#### Scenario: a pure-delisting tail is read from the new provider

- **WHEN** a caller queries `$close` for a ticker whose only entity has a
  populated `delist_date` and the query date is after that `delist_date`
- **THEN** the returned value is NaN
- **AND** the bin contains no synthetic continuation past delisting

### Requirement: PIT contract SHALL forbid absolute adjusted prices as features

`adj_factor` from Tushare is non-PIT (today's snapshot). The capability SHALL
restrict feature expressions to within-entity ratios and returns, where the
as-of-date `adj_factor` cancels in numerator and denominator. Cross-entity
arithmetic SHALL be invalid even when both operands share a ticker.

#### Scenario: feature expression uses absolute adjusted price

- **WHEN** a feature expression evaluates to an absolute adjusted price level
  (e.g. raw `$close` consumed directly as a model input)
- **THEN** the feature is rejected by the contract
- **AND** the rejection message names the within-entity-ratio / return
  alternatives

#### Scenario: feature expression spans a ticker reuse boundary

- **WHEN** a time-series operator would otherwise consume rows from two
  different entities on the same ticker
- **THEN** the NaN-gap invariant causes the operator's result to be NaN at
  every position whose window crosses the gap
- **AND** the contract treats any non-NaN result at such a position as a
  validation failure

### Requirement: PIT contract SHALL validate qlib operator min_periods behavior

The capability's Stage 6.D validation SHALL exercise real qlib operators
(`Mean($close, N)`, `Ref($close, N)`, `Corr(...)`) — not pandas `rolling` —
against tickers with `reuse_count >= 2`. Day N-1 of the second entity MUST
return NaN. Any qlib operator that silently honours `min_periods < N` is
either wrapped with explicit `min_periods=N` in the expression layer or
banned from feature expressions.

#### Scenario: qlib Mean operator is tested on day 5 of a reused entity

- **WHEN** `D.features([ticker], ['Mean($close, 20)'], start=entity2_day0,
  end=entity2_day5)` is executed on a ticker with `reuse_count == 2`
- **THEN** the value at day 5 of entity 2 is NaN
- **AND** the test cites design §4.3.2 in the failure message if it does not

### Requirement: PIT query layer SHALL be entity-aware

The PIT query layer exposed at `src/pit/query.py::PITDataProvider` SHALL
expose `get_universe(date, universe_name)`, `get_universe_range(...)`,
`get_features(fields, start_date, end_date, universe_name, align)`, and
`resolve_entity(ticker, date)`. Every query SHALL be PIT-correct: no
future-listed entity appears in `get_universe(date)`; no past-delisted entity
appears; time-series operations respect entity boundaries.

#### Scenario: universe at a past date excludes future listings

- **WHEN** `get_universe(date_X, universe_name)` is called
- **THEN** every returned ticker resolves via `resolve_entity` to an entity
  with `list_date <= date_X`
- **AND** every returned ticker resolves to an entity with `delist_date` of
  NULL or `delist_date > date_X`

#### Scenario: a delisted ticker is queried after delisting

- **WHEN** `resolve_entity(ticker, date_X)` is called with `date_X` strictly
  after the last entity's `delist_date` for a ticker with no subsequent
  reuse
- **THEN** the return value is None

### Requirement: PIT query layer SHALL use a bounded LRU cache

The PIT query layer SHALL cache feature query results with an LRU policy and
a bounded `cache_max_entries` parameter (default 256). Unbounded dict caches
SHALL NOT be used — long backtests must not OOM by accumulating cached
panels.

#### Scenario: cache eviction is triggered

- **WHEN** more than `cache_max_entries` distinct
  `(universe_name, start_date, end_date, frozenset(fields))` queries have
  been executed
- **THEN** the least-recently-used entry is evicted
- **AND** the cache size does not exceed `cache_max_entries`

### Requirement: PIT capability SHALL preserve the legacy provider untouched

The existing `D:/qlib_data/my_cn_data` provider SHALL NOT be deleted,
overwritten, or retroactively modified by any code introduced under this
capability. The new PIT-correct provider is written to a separate directory.
Both providers remain queryable indefinitely as long as disk allows.

#### Scenario: a migration script attempts to modify the legacy provider

- **WHEN** any script under `scripts/data_pipeline/` writes to a path under
  the legacy provider root
- **THEN** the contract rejects the operation
- **AND** the script aborts before any byte is written

#### Scenario: a destructive finalization step is requested

- **WHEN** a future migration finalization script is invoked without
  `--confirm-destructive`
- **THEN** the script exits before any destructive action
- **AND** the script supports a `--dry-run` mode that produces the action
  log without side effects

### Requirement: PIT capability SHALL declare out-of-scope dimensions explicitly

The capability SHALL declare the following PIT dimensions as Phase E+ backlog
and SHALL NOT silently extend them into Phase A-D scope: industry
classification (Shenwan L1/L2) PIT, fundamentals (PE / PB / ROE / financial
statements) PIT, outstanding shares / market cap PIT, ST / *ST status
snapshots, and risk-model factor exposures. Each is recorded with its own
PHASE-E.N ticket in `docs/pit/pit_universe_design.md` §4.5.

#### Scenario: a Phase A-D task appears to touch a Phase E+ dimension

- **WHEN** a follow-up PR under Phases A-D adds code that depends on a
  historical Shenwan reclassification, a historical fundamentals
  publication date, a historical share-count snapshot, a historical ST
  status, or a historical risk-model exposure
- **THEN** the reviewer rejects the PR
- **AND** the work is moved to a dedicated PHASE-E.N ticket

### Requirement: PIT reference cases YAML SHALL be user-curated for the seed

The first ≥10 entries of `tests/pit/reference_cases.yaml` SHALL be committed
by the user as Phase 0.2 and SHALL NOT be agent-generated. Subsequent rows
added by the agent in Phase A.3 SHALL cite the Tushare API response
(`stock_basic` row plus relevant `namechange` rows) in the PR body, per row.

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
  `openspec/changes/pit-universe-foundation/tasks.md`
