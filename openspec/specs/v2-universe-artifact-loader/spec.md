# v2-universe-artifact-loader Specification

## Purpose
Define the explicit file-based universe artifact loader that turns canonical
universe CSV and manifest files into contract-consumable profiles without
runtime universe selection.

## Requirements

### Requirement: V2 SHALL provide a universe artifact loader that produces contract-consumable profiles

The system SHALL provide a `UniverseArtifactLoader` that reads a
universe artifact csv and its sidecar manifest json and produces a
`UniverseArtifactProfile` consumable by
`UniverseDataContract.validate_and_build_status` without modification.
The loader SHALL accept `temporal_mode` as a required keyword
argument and SHALL read a mode-dependent column set:

- `static`: base columns `(instrument, in_universe)` only.
- `trade_date`: base columns plus `trade_date`.
- `range`: base columns plus `effective_start` and `effective_end`.

#### Scenario: healthy static universe is loaded
- **WHEN** the loader reads a csv with `instrument,in_universe`
  columns and a valid manifest whose `temporal_mode` is `static`
- **THEN** the produced profile has `artifact_present=True`,
  `manifest_present=True`, and `columns_present` contains both base
  columns
- **AND** feeding the profile into `UniverseDataContract` with
  `temporal_mode="static"` yields `contract_health == "ok"`

#### Scenario: healthy trade_date universe is loaded
- **WHEN** the loader reads a csv with `instrument,in_universe,trade_date`
  columns whose rows span `2026-02-02` to `2026-02-27`
- **THEN** `snapshot_start == "2026-02-02"` and
  `snapshot_end == "2026-02-27"`
- **AND** feeding the profile into `UniverseDataContract` with
  `temporal_mode="trade_date"` yields `contract_health == "ok"`

#### Scenario: healthy range universe is loaded
- **WHEN** the loader reads a csv with
  `instrument,in_universe,effective_start,effective_end` columns
- **THEN** `snapshot_start == min(effective_start)` and
  `snapshot_end == max(effective_end)`
- **AND** feeding the profile into `UniverseDataContract` with
  `temporal_mode="range"` yields `contract_health == "ok"`

#### Scenario: universe artifact file is missing
- **WHEN** the loader is given a non-existent artifact path and a
  valid manifest path
- **THEN** the profile has `artifact_present=False`
- **AND** feeding it into `UniverseDataContract` yields
  `contract_health == "error"` with `missing_artifact_file`

#### Scenario: universe manifest file is missing
- **WHEN** the loader is given a valid artifact path and a
  non-existent manifest path
- **THEN** the profile has `manifest_present=False`
- **AND** feeding it into `UniverseDataContract` yields
  `contract_health == "error"` with `missing_manifest_file`

### Requirement: Universe artifact loader SHALL NOT implement universe selection semantics

The loader SHALL only materialize profile data from explicit paths.
It SHALL NOT resolve universe identifiers via implicit fallback,
environment lookup, or registry defaults.

#### Scenario: loader boundary is inspected
- **WHEN** maintainers read the loader source
- **THEN** the loader exposes only explicit path-based load functions
- **AND** no implicit universe-selection or default-resolution logic
  is present

### Requirement: Universe loader SHALL detect snapshot_at vs max-trade-date mismatch in trade_date mode

In `trade_date` mode the loader SHALL compare the manifest's
`snapshot_at` field to the maximum `trade_date` observed in the csv
and SHALL expose the result as
`UniverseArtifactProfile.has_snapshot_at_mismatch`. The check only
fires when both values are present and parseable, and does not fire
in `static` or `range` mode.

#### Scenario: trade_date mode manifest snapshot_at greater than max trade_date
- **WHEN** the manifest declares `snapshot_at = 2026-02-27` and the
  csv's maximum `trade_date` is `2026-02-20`
- **THEN** `has_snapshot_at_mismatch=True`

#### Scenario: trade_date mode manifest snapshot_at equals max trade_date
- **WHEN** manifest `snapshot_at` equals the csv's max `trade_date`
- **THEN** `has_snapshot_at_mismatch=False`

#### Scenario: static mode never triggers snapshot_at mismatch
- **WHEN** the loader reads a static-mode csv (no date columns)
- **THEN** `has_snapshot_at_mismatch=False` regardless of manifest
  `snapshot_at` value

### Requirement: Universe loader SHALL detect future-dated rows against reference_date

The loader SHALL set `has_future_effective_data=True` when:

- `trade_date` mode: any `trade_date > reference_date`.
- `range` mode: any `effective_end > reference_date`.
- `static` mode: never.

#### Scenario: trade_date mode future row is flagged
- **WHEN** the csv contains a row with `trade_date = 2026-03-10` and
  `reference_date = 2026-02-27` is supplied
- **THEN** `has_future_effective_data=True`
- **AND** feeding the profile into `UniverseDataContract` yields
  `temporal_leakage` in `status.errors`

#### Scenario: range mode future effective_end is flagged
- **WHEN** the csv contains a row with
  `effective_end = 2030-01-01` and `reference_date = 2026-02-27`
- **THEN** `has_future_effective_data=True`

### Requirement: Universe loader SHALL accept an optional TradingCalendar for coverage accounting in trade_date mode

`UniverseArtifactLoader.load` SHALL accept an optional
`calendar: Optional[TradingCalendar]` keyword argument. The
`coverage_ratio` field SHALL be computed only when both
`temporal_mode == "trade_date"` AND a calendar is supplied, using
`calendar.count_trading_days(snapshot_start, snapshot_end)` as the
denominator. In all other combinations (static, range, or trade_date
without calendar) `coverage_ratio` SHALL remain `None`. No
calendar-free fallback approximation SHALL be used.

#### Scenario: trade_date mode with calendar yields coverage
- **WHEN** the loader reads a trade_date mode csv with five distinct
  trade dates and a `StaticTradingCalendar` whose dates match those
  five exactly
- **THEN** `coverage_ratio == 1.0`

#### Scenario: static mode leaves coverage_ratio as None
- **WHEN** the loader reads a static mode csv
- **THEN** `coverage_ratio is None` regardless of whether a calendar
  is supplied

### Requirement: Universe artifact loader SHALL surface unreadable artifact files as typed loader errors

The universe artifact loader SHALL distinguish missing artifact files from
other OS-level read failures. Missing artifacts remain data-contract status
inputs; non-missing unreadable files SHALL raise `UniverseArtifactLoaderError`
with the artifact file path and original OSError context.

#### Scenario: artifact CSV raises OSError while opening
- **WHEN** the artifact CSV path exists conceptually but opening it raises an
  OSError other than `FileNotFoundError`
- **THEN** `UniverseArtifactLoader.load(...)` raises
  `UniverseArtifactLoaderError`
- **AND** the error message includes the artifact CSV path
