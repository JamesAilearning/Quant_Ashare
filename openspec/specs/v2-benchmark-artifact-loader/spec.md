# v2-benchmark-artifact-loader Specification

## Purpose
Define the explicit file-based benchmark artifact loader that turns canonical
benchmark CSV and manifest files into contract-consumable profiles without
runtime benchmark selection.

## Requirements

### Requirement: V2 SHALL provide a benchmark artifact loader that produces contract-consumable profiles

The system SHALL provide a loader that reads a benchmark artifact csv and its sidecar manifest and produces a `BenchmarkArtifactProfile` consumable by `BenchmarkDataContract.validate_and_build_status` without modification.

#### Scenario: healthy benchmark artifact is loaded
- **WHEN** the loader is given an existing csv with `date,close` columns and a valid manifest
- **THEN** the produced profile has `artifact_present=True` and `manifest_present=True`
- **AND** the profile includes non-empty `snapshot_start`, `snapshot_end`, `rows`, and `columns_present`
- **AND** feeding the profile into `BenchmarkDataContract` yields `contract_health == "ok"`

#### Scenario: benchmark artifact is missing
- **WHEN** the loader is given a non-existent artifact path
- **THEN** the produced profile has `artifact_present=False`
- **AND** feeding it into `BenchmarkDataContract` yields `contract_health == "error"` with `missing_artifact_file`

#### Scenario: benchmark close column contains NaN
- **WHEN** the loader encounters a csv with NaN in the `close` column
- **THEN** the produced profile reports `columns_present` consistent with schema mismatch so the contract yields `schema_mismatch`

#### Scenario: benchmark artifact is future-dated
- **WHEN** the loader reads a csv whose last date is after the supplied reference date
- **THEN** the produced profile sets `has_future_data=True`
- **AND** feeding it into `BenchmarkDataContract` yields an error with `temporal_issue`

### Requirement: Benchmark artifact loader SHALL NOT implement benchmark selection semantics

The loader SHALL only materialize profile data from explicit paths. It SHALL NOT resolve benchmark identifiers via implicit fallback, environment lookup, or registry defaults.

#### Scenario: loader boundary is inspected
- **WHEN** maintainers read the loader source
- **THEN** the loader exposes only explicit path-based load functions
- **AND** no implicit benchmark selection or default-resolution logic is present

### Requirement: Benchmark loader SHALL detect snapshot_at vs csv max-row-date mismatch

The benchmark artifact loader SHALL compute a strict equality comparison between
the manifest's `snapshot_at` field and the maximum row date observed in the csv,
and SHALL expose the result as `BenchmarkArtifactProfile.has_snapshot_at_mismatch`.
The comparison only fires when both values are present and parseable; missing
values are handled by independent schema/missing-file error codes.

#### Scenario: manifest snapshot_at strictly greater than csv max date
- **WHEN** a manifest declares `snapshot_at = 2026-02-27` and the csv's last row date is `2026-02-20`
- **THEN** `BenchmarkArtifactLoader.load` returns a profile with `has_snapshot_at_mismatch=True`

#### Scenario: manifest snapshot_at strictly less than csv max date
- **WHEN** a manifest declares `snapshot_at = 2026-02-20` and the csv's last row date is `2026-02-27`
- **THEN** `BenchmarkArtifactLoader.load` returns a profile with `has_snapshot_at_mismatch=True`

#### Scenario: manifest snapshot_at equals csv max date
- **WHEN** manifest `snapshot_at` exactly equals csv max row date
- **THEN** `BenchmarkArtifactLoader.load` returns a profile with `has_snapshot_at_mismatch=False`

#### Scenario: manifest is missing snapshot_at field
- **WHEN** the manifest does not contain `snapshot_at`
- **THEN** `has_snapshot_at_mismatch` remains `False`
- **AND** the missing field is reported by the contract via `schema_mismatch`, not by this check

### Requirement: Benchmark loader SHALL accept an optional TradingCalendar for accurate coverage accounting

`BenchmarkArtifactLoader.load` SHALL accept an optional
`calendar: TradingCalendar` keyword argument. When supplied, the loader
SHALL compute `coverage_ratio` using
`calendar.count_trading_days(snapshot_start, snapshot_end)` as the
denominator. When `calendar` is `None`, the loader SHALL fall back to
the existing calendar-free approximation
(`expected_rows ≈ span_days × 0.63`) and SHALL preserve the prior
behavior so that all existing callers and tests remain valid without
modification.

#### Scenario: calendar injection drives accurate coverage
- **WHEN** `BenchmarkArtifactLoader.load(..., calendar=cal)` is called
  with a `StaticTradingCalendar` whose dates exactly match the csv rows
- **THEN** the produced profile has `coverage_ratio == 1.0`
- **AND** feeding the profile into `BenchmarkDataContract` yields
  `contract_health == "ok"` for an otherwise healthy artifact

#### Scenario: calendar with extra trading days surfaces incomplete coverage
- **WHEN** the injected `StaticTradingCalendar` reports more trading
  days inside `[snapshot_start, snapshot_end]` than the csv contains
- **THEN** `coverage_ratio` is strictly less than `1.0`
- **AND** when the ratio falls below `min_coverage_ratio`, the contract
  surfaces `incomplete_coverage`

#### Scenario: omitting calendar preserves legacy fallback
- **WHEN** `BenchmarkArtifactLoader.load(...)` is called without a
  `calendar` argument
- **THEN** `coverage_ratio` is computed using the legacy
  `span_days × 0.63` approximation
- **AND** existing tests continue to pass unchanged
