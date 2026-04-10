## ADDED Requirements

### Requirement: V2 SHALL provide a universe artifact publisher that writes canonical csv + manifest from caller-supplied rows

The system SHALL provide a `UniverseArtifactPublisher.publish`
class method that takes caller-supplied rows plus provenance metadata
and writes a canonical universe artifact (csv + sidecar manifest
json) consumable by `UniverseArtifactLoader` and ultimately by
`UniverseDataContract.validate_and_build_status`. The publisher
SHALL NOT accept pandas DataFrames or other opaque frame types; it
SHALL accept an explicit sequence of per-row tuples whose arity is
determined by `temporal_mode`:

- `static`:   `(instrument: str, in_universe: bool)`
- `trade_date`: `(instrument: str, in_universe: bool, trade_date: str)`
- `range`:   `(instrument: str, in_universe: bool, effective_start: str, effective_end: str)`

#### Scenario: canonical round trip for static mode
- **WHEN** `UniverseArtifactPublisher.publish` is called with
  `temporal_mode="static"`, two `(instrument, in_universe)` rows,
  explicit `snapshot_at="2026-02-27"`, and target csv/manifest paths
- **THEN** a csv with header `instrument,in_universe` and two data
  rows is written
- **AND** a sidecar manifest json containing all required universe
  provenance fields (including `temporal_mode=static` and the
  supplied `snapshot_at`) is written
- **AND** the returned profile yields
  `contract_health == "ok"` when fed into
  `UniverseDataContract.validate_and_build_status`

#### Scenario: canonical round trip for trade_date mode
- **WHEN** the publisher is called with `temporal_mode="trade_date"`
  and rows spanning `2026-02-02` to `2026-02-27`
- **THEN** the manifest `snapshot_at` equals `2026-02-27`
- **AND** the returned profile yields `contract_health == "ok"`

#### Scenario: canonical round trip for range mode
- **WHEN** the publisher is called with `temporal_mode="range"` and
  rows with explicit `effective_start`/`effective_end` values
- **THEN** the csv is written with four data columns
- **AND** the returned profile yields `contract_health == "ok"`

### Requirement: Universe publisher SHALL refuse to emit empty artifacts

The publisher SHALL NOT write a csv with zero data rows. An empty
`rows` argument SHALL be treated as an explicit error, not as a
silent healthy output.

#### Scenario: empty rows argument is rejected
- **WHEN** the publisher is called with `rows=[]`
- **THEN** a `UniverseArtifactPublisherError` is raised with the
  offending inputs echoed back
- **AND** neither the csv nor the manifest file exists on disk after
  the failed call

### Requirement: Universe publisher SHALL validate row arity per temporal_mode before any IO

The publisher SHALL reject rows whose tuple arity does not match the
declared `temporal_mode` and SHALL raise
`UniverseArtifactPublisherError` with the offending mode and
observed arity. Validation SHALL run BEFORE any file is written so
no partial artifact is left behind.

#### Scenario: static mode with 3-tuple rows is rejected
- **WHEN** the publisher is called with `temporal_mode="static"` and
  a row `("AAPL", True, "2026-02-27")`
- **THEN** `UniverseArtifactPublisherError` is raised
- **AND** no csv or manifest file exists on disk after the failed call

#### Scenario: trade_date mode with 2-tuple rows is rejected
- **WHEN** the publisher is called with
  `temporal_mode="trade_date"` and a row `("AAPL", True)`
- **THEN** `UniverseArtifactPublisherError` is raised
- **AND** no csv or manifest file exists on disk after the failed call

### Requirement: Universe publisher SHALL validate ISO date fields before any IO

The publisher SHALL parse every date-valued field present in
`rows` and in the `snapshot_at` argument as a strict ISO
`YYYY-MM-DD` string BEFORE any file is written. Parse failures
SHALL raise `UniverseArtifactPublisherError` with the offending
field and value, and SHALL leave no csv or manifest file on disk.

#### Scenario: malformed trade_date in a row is rejected
- **WHEN** the publisher is called with `temporal_mode="trade_date"`
  and a row whose `trade_date="2026/02/27"`
- **THEN** `UniverseArtifactPublisherError` is raised
- **AND** the error message contains the offending string
  `2026/02/27`
- **AND** no csv or manifest file exists on disk after the failed call

#### Scenario: malformed explicit snapshot_at is rejected
- **WHEN** the publisher is called with
  `temporal_mode="static"` and `snapshot_at="banana"`
- **THEN** `UniverseArtifactPublisherError` is raised
- **AND** the error message contains the offending string `banana`

### Requirement: Universe publisher SHALL derive `snapshot_at` from actual max trade_date in trade_date mode

In `trade_date` mode the publisher SHALL set the manifest
`snapshot_at` field to `max(row.trade_date)`. When the caller
explicitly supplies `snapshot_at`, the publisher SHALL validate
strict equality between the supplied value and
`max(row.trade_date)` and SHALL raise
`UniverseArtifactPublisherError` at the publisher boundary if they
differ.

#### Scenario: explicit snapshot_at matches max trade_date
- **WHEN** the publisher is called in `trade_date` mode with
  `snapshot_at="2026-02-27"` and rows whose max `trade_date` is
  also `2026-02-27`
- **THEN** the publisher accepts the call and writes that value
  into the manifest unchanged

#### Scenario: explicit snapshot_at mismatches max trade_date
- **WHEN** the publisher is called in `trade_date` mode with
  `snapshot_at="2026-02-25"` and rows whose max `trade_date` is
  `2026-02-27`
- **THEN** `UniverseArtifactPublisherError` is raised
- **AND** the error message includes both `2026-02-25` and
  `2026-02-27`
- **AND** no csv or manifest file is left behind

### Requirement: Universe publisher SHALL require explicit snapshot_at in static and range modes

In `static` and `range` modes there is no natural max-row-date to
derive `snapshot_at` from; the publisher SHALL require an explicit
`snapshot_at` ISO date argument and SHALL raise
`UniverseArtifactPublisherError` when it is missing.

#### Scenario: static mode without snapshot_at is rejected
- **WHEN** the publisher is called with `temporal_mode="static"`
  and `snapshot_at=None`
- **THEN** `UniverseArtifactPublisherError` is raised
- **AND** the error message names `snapshot_at`

#### Scenario: range mode without snapshot_at is rejected
- **WHEN** the publisher is called with `temporal_mode="range"`
  and `snapshot_at=None`
- **THEN** `UniverseArtifactPublisherError` is raised

### Requirement: Universe publisher and loader SHALL share one profile construction path

The publisher SHALL delegate construction of the returned
`UniverseArtifactProfile` to `UniverseArtifactLoader.load` on its
own output, so that there is exactly one code path that interprets
csv + manifest into a contract-consumable profile.

#### Scenario: producer / consumer symmetry is audited
- **WHEN** maintainers review the publisher source
- **THEN** the publisher calls `UniverseArtifactLoader.load` on its
  own output and returns the result
- **AND** the publisher does not construct `UniverseArtifactProfile`
  directly
