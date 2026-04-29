# v2-benchmark-artifact-publisher Specification

## Purpose
Define the qlib-backed benchmark artifact publisher that writes canonical
benchmark CSV and manifest files with explicit provenance.

## Requirements

### Requirement: V2 SHALL provide a benchmark artifact publisher backed by the pinned qlib provider

The system SHALL provide a publisher that produces benchmark artifacts in the canonical csv + manifest shape consumable by `BenchmarkDataContract` without modification, using the pinned qlib provider as the sole data source.

#### Scenario: canonical round trip from publisher to contract
- **WHEN** canonical qlib runtime has been initialized via the canonical entry point
- **AND** the publisher is invoked with an explicit `benchmark_code`, `start_time`, `end_time`, and target csv/manifest paths
- **THEN** a csv with header `date,close` and at least one data row is written
- **AND** a sidecar manifest json containing all required benchmark provenance fields is written
- **AND** the returned profile yields `contract_health == "ok"` when fed into `BenchmarkDataContract.validate_and_build_status`

### Requirement: Publisher SHALL require canonical qlib initialization

The publisher SHALL NOT call `qlib.init` directly. It SHALL verify that canonical qlib initialization has already happened via `src.core.qlib_runtime.init_qlib_canonical`, and SHALL raise a typed error otherwise.

#### Scenario: publisher is called without canonical init
- **WHEN** the publisher is invoked before `init_qlib_canonical` has been called
- **THEN** a `BenchmarkArtifactPublisherError` is raised
- **AND** no csv or manifest file is written

#### Scenario: canonical init boundary is scanned
- **WHEN** governance regression tests scan `src/data/`
- **THEN** no file under `src/data/` references `qlib.init(` directly
- **AND** the publisher imports `is_canonical_qlib_initialized` from `src.core.qlib_runtime`

### Requirement: Publisher SHALL refuse to emit empty artifacts

The publisher SHALL NOT write a csv with zero data rows. An empty qlib query result SHALL be treated as an explicit error, not as a silent healthy output.

#### Scenario: qlib provider returns empty result
- **WHEN** the publisher queries the qlib provider and receives zero rows
- **THEN** a `BenchmarkArtifactPublisherError` is raised with the offending inputs echoed back
- **AND** neither the csv nor the manifest file exists on disk after the failed call

### Requirement: Publisher and loader SHALL share one profile construction path

The publisher SHALL delegate construction of the returned `BenchmarkArtifactProfile` to the canonical `BenchmarkArtifactLoader`, so that there is exactly one code path that interprets csv + manifest into a contract-consumable profile.

#### Scenario: producer / consumer symmetry is audited
- **WHEN** maintainers review the publisher source
- **THEN** the publisher calls `BenchmarkArtifactLoader.load` on its own output and returns the result
- **AND** the publisher does not construct `BenchmarkArtifactProfile` directly

### Requirement: Publisher SHALL derive `snapshot_at` from the actual max row date

`BenchmarkArtifactPublisher.publish` SHALL set the manifest
`snapshot_at` field to the maximum row date present in the csv it just
wrote, NOT to the requested `end_time`. When the caller explicitly
supplies `snapshot_at`, the publisher SHALL validate strict equality
between the supplied value and the actual max row date and SHALL raise
`BenchmarkArtifactPublisherError` at the publisher boundary if they
differ.

#### Scenario: end_time falls on a non-trading day
- **WHEN** `BenchmarkArtifactPublisher.publish` is called with
  `end_time` set to a Saturday
- **AND** the qlib provider returns rows up to the preceding Friday
- **THEN** the manifest `snapshot_at` equals the Friday date
- **AND** the round-trip profile yields `contract_health == "ok"`

#### Scenario: caller passes a snapshot_at that mismatches actual data
- **WHEN** the caller supplies `snapshot_at="2026-02-25"`
- **AND** the actual maximum row date in the qlib data is `2026-02-27`
- **THEN** the publisher raises `BenchmarkArtifactPublisherError`
- **AND** the error message includes both `2026-02-25` and `2026-02-27`
- **AND** no csv or manifest file is left behind in a partially-written state

#### Scenario: caller passes a snapshot_at that matches actual data
- **WHEN** the caller supplies `snapshot_at="2026-02-27"` and the
  actual maximum row date is also `2026-02-27`
- **THEN** the publisher accepts the call and writes that value into
  the manifest unchanged

### Requirement: Publisher SHALL inject a TradingCalendar into its loader call

`BenchmarkArtifactPublisher.publish` SHALL pass a `TradingCalendar`
instance to the internal `BenchmarkArtifactLoader.load` call so that
the round-trip `BenchmarkArtifactProfile`'s `coverage_ratio` is
computed against the real qlib trading calendar, not the calendar-free
fallback approximation. The default implementation SHALL be
`QlibTradingCalendar`, which lazily fetches the calendar from the
already-initialized canonical qlib runtime.

#### Scenario: round-trip profile uses real trading days
- **WHEN** `BenchmarkArtifactPublisher.publish` is called and the
  internal loader call returns
- **THEN** the loader call received a non-`None` `calendar` keyword argument
- **AND** the calendar argument is an instance of `QlibTradingCalendar`

### Requirement: Publisher SHALL NOT swallow unexpected exceptions inside frame flattening

`BenchmarkArtifactPublisher._flatten_close_frame` SHALL only catch
narrow, expected pandas/qlib API exception types
(`AttributeError`, `TypeError`, `ValueError`) when applying its
shape-tolerance fallbacks. It SHALL NOT use bare `except Exception:`,
which would mask programmer bugs (e.g. ImportError, NameError) and
turn them into a misleading "qlib provider returned no rows" error
later in the publish flow.

#### Scenario: minimal duck-typed frame is parsed correctly
- **WHEN** `_flatten_close_frame` receives an object that exposes
  `columns`, `iterrows()`, `reset_index()`, and the expected
  `datetime` / `$close` columns
- **THEN** it returns a list of `(iso_date, close_value)` tuples
  sorted ascending by date

#### Scenario: None input is treated as empty
- **WHEN** `_flatten_close_frame(None)` is called
- **THEN** the result is an empty list

#### Scenario: input without `reset_index` is treated as empty
- **WHEN** the frame argument is an object that lacks `reset_index`
- **THEN** the result is an empty list (an `AttributeError` is caught)
- **AND** no other exception types are silently swallowed

### Requirement: Publisher SHALL validate `start_time` / `end_time` as ISO dates with start <= end before calling qlib

`BenchmarkArtifactPublisher.publish` SHALL parse `start_time` and
`end_time` as strict ISO `YYYY-MM-DD` dates AFTER the existing
non-empty check and BEFORE any call to the qlib provider
(`D.features`). Parse failures SHALL raise
`BenchmarkArtifactPublisherError` with the offending field name and
the raw value. The publisher SHALL also verify that the parsed
`start_time` is less than or equal to the parsed `end_time` and
SHALL raise `BenchmarkArtifactPublisherError` otherwise. A
single-day window (`start_time == end_time`) SHALL be accepted.

#### Scenario: start_time is not a valid ISO date
- **WHEN** the publisher is called with `start_time="banana"`
- **THEN** `BenchmarkArtifactPublisherError` is raised
- **AND** the error message contains the field name `start_time` and
  the offending string `banana`
- **AND** neither the csv nor the manifest file is left behind on disk

#### Scenario: end_time uses a non-ISO separator
- **WHEN** the publisher is called with `end_time="2026/02/27"`
- **THEN** `BenchmarkArtifactPublisherError` is raised
- **AND** the error message contains the field name `end_time` and
  the offending string `2026/02/27`
- **AND** neither the csv nor the manifest file is left behind on disk

#### Scenario: start_time is after end_time
- **WHEN** the publisher is called with `start_time="2026-02-27"`
  and `end_time="2026-02-01"`
- **THEN** `BenchmarkArtifactPublisherError` is raised
- **AND** the error message contains both raw values
- **AND** neither the csv nor the manifest file is left behind on disk

#### Scenario: ISO validation runs before any qlib call
- **WHEN** the publisher is called with a malformed `start_time` and
  qlib has not been queried yet
- **THEN** the publisher raises `BenchmarkArtifactPublisherError`
  without ever invoking `qlib.data.D.features`
