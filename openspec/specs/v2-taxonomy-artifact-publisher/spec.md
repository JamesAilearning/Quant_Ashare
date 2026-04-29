# v2-taxonomy-artifact-publisher Specification

## Purpose
Define the taxonomy artifact publisher that writes canonical taxonomy CSV and
manifest files from caller-supplied rows with explicit provenance.

## Requirements

### Requirement: V2 SHALL provide a taxonomy artifact publisher that writes canonical csv + manifest from caller-supplied rows

The system SHALL provide a `TaxonomyArtifactPublisher.publish`
class method that takes caller-supplied rows plus provenance
metadata and writes a canonical taxonomy artifact (csv + sidecar
manifest json) consumable by `TaxonomyArtifactLoader` and ultimately
by `TaxonomyDataContract.validate_and_build_status`. The publisher
SHALL accept an explicit sequence of per-row tuples whose arity is
determined by `temporal_mode`:

- `static`:   `(instrument: str, industry_code: str)`
- `trade_date`: `(instrument: str, industry_code: str, trade_date: str)`
- `range`:   `(instrument: str, industry_code: str, effective_start: str, effective_end: str)`

#### Scenario: canonical round trip for static mode
- **WHEN** the publisher is called with `temporal_mode="static"`,
  two `(instrument, industry_code)` rows, explicit
  `snapshot_at="2026-02-27"`, and target csv/manifest paths
- **THEN** a csv with header `instrument,industry_code` and two
  data rows is written
- **AND** a sidecar manifest json containing all required taxonomy
  provenance fields is written
- **AND** the returned profile yields `contract_health == "ok"`
  when fed into `TaxonomyDataContract.validate_and_build_status`

#### Scenario: canonical round trip for trade_date mode
- **WHEN** the publisher is called with `temporal_mode="trade_date"`
  and rows spanning `2026-02-02` to `2026-02-27`
- **THEN** the manifest `snapshot_at` equals `2026-02-27`
- **AND** the returned profile yields `contract_health == "ok"`

#### Scenario: canonical round trip for range mode
- **WHEN** the publisher is called with `temporal_mode="range"` and
  rows with explicit `effective_start`/`effective_end` values
- **THEN** the returned profile yields `contract_health == "ok"`

### Requirement: Taxonomy publisher SHALL refuse to emit empty artifacts

The publisher SHALL NOT write a csv with zero data rows. An empty
`rows` argument SHALL be treated as an explicit error.

#### Scenario: empty rows argument is rejected
- **WHEN** the publisher is called with `rows=[]`
- **THEN** `TaxonomyArtifactPublisherError` is raised
- **AND** neither the csv nor the manifest file exists on disk

### Requirement: Taxonomy publisher SHALL validate row arity per temporal_mode before any IO

The taxonomy publisher SHALL validate row arity with the same semantics as the
universe publisher: arity must match the declared `temporal_mode`, validation
runs before any file is written, and failures raise
`TaxonomyArtifactPublisherError` with no partial artifact left on disk.

#### Scenario: static mode with 3-tuple rows is rejected
- **WHEN** the publisher is called with `temporal_mode="static"`
  and a row `("AAPL", "101010", "2026-02-27")`
- **THEN** `TaxonomyArtifactPublisherError` is raised
- **AND** no csv or manifest file exists on disk

#### Scenario: trade_date mode with 2-tuple rows is rejected
- **WHEN** the publisher is called with
  `temporal_mode="trade_date"` and a row `("AAPL", "101010")`
- **THEN** `TaxonomyArtifactPublisherError` is raised

### Requirement: Taxonomy publisher SHALL validate ISO date fields before any IO

The taxonomy publisher SHALL validate ISO date fields with the same semantics
as the universe publisher: every date-valued field in `rows` and in
`snapshot_at` is ISO-parsed before any file is written. Parse failures raise
`TaxonomyArtifactPublisherError` with the offending field and value, and leave
no csv or manifest file on disk.

#### Scenario: malformed trade_date in a row is rejected
- **WHEN** the publisher is called with `temporal_mode="trade_date"`
  and a row whose `trade_date="2026/02/27"`
- **THEN** `TaxonomyArtifactPublisherError` is raised
- **AND** the error message contains the offending string
  `2026/02/27`
- **AND** no csv or manifest file exists on disk

#### Scenario: malformed explicit snapshot_at is rejected
- **WHEN** the publisher is called with `snapshot_at="banana"`
- **THEN** `TaxonomyArtifactPublisherError` is raised
- **AND** the error message contains `banana`

### Requirement: Taxonomy publisher SHALL derive `snapshot_at` from actual max trade_date in trade_date mode

The taxonomy publisher SHALL derive `snapshot_at` with the same semantics as
the universe publisher: in `trade_date` mode, default
`snapshot_at = max(row.trade_date)`; explicit `snapshot_at` must strictly equal
the computed value.

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
- **THEN** `TaxonomyArtifactPublisherError` is raised
- **AND** the error message includes both dates
- **AND** no csv or manifest file is left behind

### Requirement: Taxonomy publisher SHALL require explicit snapshot_at in static and range modes

The taxonomy publisher SHALL require explicit `snapshot_at` in `static` and
`range` modes because there is no natural max-row-date to derive
`snapshot_at` from. The caller SHALL provide a non-empty ISO `snapshot_at`
argument.

#### Scenario: static mode without snapshot_at is rejected
- **WHEN** the publisher is called with `temporal_mode="static"`
  and `snapshot_at=None`
- **THEN** `TaxonomyArtifactPublisherError` is raised
- **AND** the error message names `snapshot_at`

#### Scenario: range mode without snapshot_at is rejected
- **WHEN** the publisher is called with `temporal_mode="range"`
  and `snapshot_at=None`
- **THEN** `TaxonomyArtifactPublisherError` is raised

### Requirement: Taxonomy publisher and loader SHALL share one profile construction path

The publisher SHALL delegate construction of the returned
`TaxonomyArtifactProfile` to `TaxonomyArtifactLoader.load` on its
own output, so that there is exactly one code path that interprets
csv + manifest into a contract-consumable profile.

#### Scenario: producer / consumer symmetry is audited
- **WHEN** maintainers review the publisher source
- **THEN** the publisher calls `TaxonomyArtifactLoader.load` on its
  own output and returns the result
- **AND** the publisher does not construct `TaxonomyArtifactProfile`
  directly
