## ADDED Requirements

### Requirement: V2 SHALL provide a taxonomy artifact loader that produces contract-consumable profiles

The system SHALL provide a `TaxonomyArtifactLoader` that reads a
taxonomy artifact csv and its sidecar manifest json and produces a
`TaxonomyArtifactProfile` consumable by
`TaxonomyDataContract.validate_and_build_status` without modification.
The loader SHALL accept `temporal_mode` as a required keyword
argument and SHALL read a mode-dependent column set:

- `static`: base columns `(instrument, industry_code)` only.
- `trade_date`: base columns plus `trade_date`.
- `range`: base columns plus `effective_start` and `effective_end`.

#### Scenario: healthy static taxonomy is loaded
- **WHEN** the loader reads a csv with `instrument,industry_code`
  columns and a valid manifest whose `temporal_mode` is `static`
- **THEN** the produced profile has `artifact_present=True`,
  `manifest_present=True`, and `columns_present` contains both base
  columns
- **AND** feeding the profile into `TaxonomyDataContract` with
  `temporal_mode="static"` yields `contract_health == "ok"`

#### Scenario: healthy trade_date taxonomy is loaded
- **WHEN** the loader reads a csv with
  `instrument,industry_code,trade_date` columns
- **THEN** `snapshot_start`/`snapshot_end` are set from min/max
  `trade_date`
- **AND** feeding the profile into `TaxonomyDataContract` with
  `temporal_mode="trade_date"` yields `contract_health == "ok"`

#### Scenario: healthy range taxonomy is loaded
- **WHEN** the loader reads a csv with
  `instrument,industry_code,effective_start,effective_end` columns
- **THEN** `snapshot_start == min(effective_start)` and
  `snapshot_end == max(effective_end)`
- **AND** feeding the profile into `TaxonomyDataContract` with
  `temporal_mode="range"` yields `contract_health == "ok"`

#### Scenario: taxonomy artifact file is missing
- **WHEN** the loader is given a non-existent artifact path
- **THEN** the profile has `artifact_present=False`
- **AND** feeding it into `TaxonomyDataContract` yields
  `contract_health == "error"` with `missing_artifact_file`

### Requirement: Taxonomy artifact loader SHALL NOT implement industry runtime semantics

The loader SHALL only materialize profile data from explicit paths.
It SHALL NOT resolve industry identifiers via implicit fallback,
environment lookup, or registry defaults.

#### Scenario: loader boundary is inspected
- **WHEN** maintainers read the loader source
- **THEN** the loader exposes only explicit path-based load functions
- **AND** no implicit industry-resolution or default-mapping logic
  is present

### Requirement: Taxonomy loader SHALL detect snapshot_at vs max-trade-date mismatch in trade_date mode

Same semantics as the universe loader: the check only fires in
`trade_date` mode when both the manifest `snapshot_at` and the csv's
max `trade_date` are present and parseable.

#### Scenario: trade_date mode manifest snapshot_at greater than max trade_date
- **WHEN** the manifest declares `snapshot_at = 2026-02-27` and the
  csv's maximum `trade_date` is `2026-02-20`
- **THEN** `has_snapshot_at_mismatch=True`

#### Scenario: static mode never triggers snapshot_at mismatch
- **WHEN** the loader reads a static-mode csv
- **THEN** `has_snapshot_at_mismatch=False` regardless of manifest
  `snapshot_at` value

### Requirement: Taxonomy loader SHALL detect future-dated rows against reference_date

Same semantics as the universe loader:

- `trade_date` mode: any `trade_date > reference_date` sets
  `has_future_effective_data=True`.
- `range` mode: any `effective_end > reference_date` sets
  `has_future_effective_data=True`.
- `static` mode: never.

#### Scenario: trade_date mode future row is flagged
- **WHEN** the csv contains a row with `trade_date = 2026-03-10` and
  `reference_date = 2026-02-27` is supplied
- **THEN** `has_future_effective_data=True`

### Requirement: Taxonomy loader SHALL accept an optional TradingCalendar for coverage accounting in trade_date mode

Same semantics as the universe loader: coverage is computed only
in `trade_date` mode AND when a `TradingCalendar` is injected. No
calendar-free fallback approximation SHALL be used.

#### Scenario: trade_date mode with calendar yields coverage
- **WHEN** the loader reads a trade_date mode csv with five
  distinct trade dates and a matching `StaticTradingCalendar`
- **THEN** `coverage_ratio == 1.0`

#### Scenario: static mode leaves coverage_ratio as None
- **WHEN** the loader reads a static mode csv
- **THEN** `coverage_ratio is None` regardless of whether a calendar
  is supplied
