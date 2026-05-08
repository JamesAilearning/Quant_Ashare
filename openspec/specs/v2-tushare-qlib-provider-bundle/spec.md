# v2-tushare-qlib-provider-bundle Specification

## Purpose
TBD - created by archiving change add-tushare-qlib-provider-bundle. Update Purpose after archive.
## Requirements
### Requirement: Tushare provider publishing SHALL remain opt-in

The system SHALL provide a Tushare-to-qlib provider publishing path without
changing the default canonical training or backtest data source. Canonical
runtime components SHALL only use a Tushare-generated provider when an operator
explicitly configures the qlib `provider_uri` to point at that generated bundle.

#### Scenario: default training config is inspected
- **WHEN** maintainers inspect the default runtime config after this change
- **THEN** it does not automatically point `provider_uri` at a Tushare-generated bundle
- **AND** existing canonical qlib initialization and official metrics semantics remain unchanged

#### Scenario: operator selects generated bundle explicitly
- **WHEN** an operator configures `provider_uri` to the generated Tushare qlib bundle
- **THEN** the existing canonical qlib initialization path is used
- **AND** the configured provider adjustment mode must match the generated bundle manifest

### Requirement: Tushare OHLCV publishing SHALL use explicit source APIs and secrets boundaries

The publisher SHALL fetch A-share daily OHLCV data, adjustment factors, trading
calendar data, and instrument metadata through the Tushare client boundary.
Tushare tokens SHALL be read only from the approved environment variable path
and SHALL NOT be accepted from committed YAML, stored in manifests, or emitted
in logs.

#### Scenario: token is missing
- **WHEN** the publisher is invoked without `TUSHARE_TOKEN`
- **THEN** it raises a typed Tushare client error before any output bundle is published
- **AND** the error message explains how to configure the token without printing a secret value

#### Scenario: committed config contains token-like key
- **WHEN** the publisher config contains a `tushare_token` field
- **THEN** config loading fails before network calls are made
- **AND** no output bundle is created or modified

#### Scenario: dependency is not installed
- **WHEN** the publisher is invoked in an environment without the Tushare package
- **THEN** it raises an actionable error naming the installable `tushare` extra
- **AND** importing canonical runtime modules still succeeds without importing Tushare

### Requirement: Generated qlib bundles SHALL declare adjustment semantics explicitly

The publisher config SHALL require a supported output adjustment mode and SHALL
record that mode in the generated manifest. Adjusted output SHALL be derived
from raw daily bars and Tushare adjustment factors; the publisher SHALL reject
adjusted output when required factor coverage is missing.

#### Scenario: supported adjustment mode is configured
- **WHEN** the publisher is configured with a supported adjustment mode
- **THEN** the generated manifest records that exact mode
- **AND** the bundle can be used only with matching canonical qlib runtime adjustment metadata

#### Scenario: unsupported adjustment mode is configured
- **WHEN** the publisher is configured with an unsupported adjustment mode
- **THEN** config validation fails before any Tushare calls are made
- **AND** no output bundle is created or modified

#### Scenario: adjustment factors are incomplete
- **WHEN** adjusted output is requested and required factor rows are missing for covered instrument-date pairs
- **THEN** publishing fails with validation health `error`
- **AND** the final provider bundle is not replaced by partially adjusted data

### Requirement: Generated qlib bundles SHALL support explicitly configured benchmark indexes

The publisher SHALL support an explicit benchmark-index mapping from qlib codes
to Tushare index codes. Configured benchmark indexes SHALL be fetched through
Tushare `index_daily`, written into the generated provider feature directory,
and recorded in the manifest and validation profile. Benchmark index rows SHALL
NOT be written into the stock training universe file.

#### Scenario: benchmark index is configured
- **WHEN** the publisher config maps `SH000300` to Tushare index `000300.SH`
- **THEN** the generated qlib bundle contains feature files for `SH000300`
- **AND** `SH000300` is not added to `instruments/all.txt`
- **AND** the manifest records the configured benchmark mapping and benchmark row count

#### Scenario: benchmark index data is malformed
- **WHEN** configured `index_daily` data has duplicate index-date rows, invalid OHLCV values, missing configured index coverage, or non-calendar dates
- **THEN** publishing fails with validation health `error`
- **AND** the final provider bundle is not replaced by partially generated benchmark data

### Requirement: Publisher validation SHALL reject malformed staged market data

Before publishing a final qlib provider bundle, the system SHALL validate staged
Tushare data for required columns, parseable dates, duplicate instrument-date
rows, invalid OHLCV values, calendar alignment, instrument coverage, and date
coverage. Validation failures SHALL be explicit and auditable.

#### Scenario: staged cache is reused for the same Tushare request
- **WHEN** `reuse_staged` is enabled and a staged CSV exists with metadata
  matching the current Tushare API name and request parameters
- **THEN** the fetcher MAY reuse that staged CSV without a network call
- **AND** validation still runs before any final bundle is published

#### Scenario: staged cache parameters differ from the current request
- **WHEN** `reuse_staged` is enabled but an existing staged CSV has missing or
  mismatched request metadata
- **THEN** the fetcher SHALL refetch the payload for the current request
- **AND** it SHALL NOT silently narrow the requested date coverage to the stale
  cached file

#### Scenario: duplicate market rows are staged
- **WHEN** staged daily data contains more than one row for the same instrument and trade date
- **THEN** validation health is `error`
- **AND** the final provider bundle is not published

#### Scenario: non-trading date row is staged
- **WHEN** staged market data contains a trade date not present in the staged trading calendar
- **THEN** validation health is `error`
- **AND** the validation profile identifies calendar alignment as the failure category

#### Scenario: empty instrument coverage is staged
- **WHEN** the staged data has no valid instruments over the requested date range
- **THEN** publishing fails before qlib bundle conversion
- **AND** the error message distinguishes empty coverage from Tushare connectivity failures

### Requirement: Publisher SHALL preserve raw staged market payloads across instrument scopes

The publisher SHALL keep raw per-API staged payloads independent from the
current instrument scope. Filtering requested instruments SHALL only affect the
current in-memory staged view or a scope-specific derived artifact, never the
raw staged daily or adjustment-factor cache file.

#### Scenario: narrow instrument scope is staged before a wider run
- **WHEN** a run with an explicit instrument subset writes or reuses raw staged
  daily and adjustment-factor payloads
- **THEN** the raw staged CSV files retain all instruments returned by Tushare
  for that request
- **AND** only the current staged view is filtered to the explicit subset

#### Scenario: wider instrument scope reuses staged raw payloads
- **WHEN** a later `instruments: all` or wider subset run uses the same staging
  directory and matching raw API request parameters
- **THEN** the wider run sees all instruments present in the raw cached payload
- **AND** no validation success is possible from a previously narrowed raw CSV
  pretending to be broad coverage

### Requirement: Generated bundles SHALL include provenance and validation manifests

Each successful publish SHALL write a sidecar manifest that records source name,
source APIs, source package version when available, requested date range,
actual coverage range, instrument count, row counts, output adjustment mode,
configured benchmark indexes, snapshot timestamp, validation health, and
publisher version. The manifest SHALL exclude secrets and SHALL be sufficient
for later training runs to identify the data bundle used.

#### Scenario: publish succeeds
- **WHEN** a Tushare qlib provider bundle is published successfully
- **THEN** its manifest contains source, coverage, adjustment, validation, and publisher metadata
- **AND** the manifest does not contain the Tushare token or any token-derived value

#### Scenario: training run records generated provider
- **WHEN** a training run uses a Tushare-generated qlib provider bundle
- **THEN** run metadata can record the bundle path and manifest identity
- **AND** the run remains distinguishable from runs using the previous qlib provider

### Requirement: Publisher SHALL avoid partial publication on failure

The publisher SHALL stage downloaded data and converted qlib files separately
from the final provider bundle location. A failed download, validation, or
conversion SHALL leave any previously published bundle unchanged.

#### Scenario: validation fails after staging
- **WHEN** staged data fails validation
- **THEN** the staged validation profile is available for diagnostics
- **AND** the final provider bundle directory is not replaced

#### Scenario: conversion fails
- **WHEN** qlib bundle conversion fails after validation
- **THEN** the failure is reported as a typed publisher error
- **AND** previously published provider files remain available

### Requirement: Tushare provider comparison SHALL be informational

The system SHALL support an optional comparison report between a
Tushare-generated qlib bundle and an existing qlib provider. The report SHALL
surface coverage overlap, row-count differences, missing instruments, and price
or volume deltas without declaring either provider canonical by default.

#### Scenario: baseline provider is supplied
- **WHEN** the publisher or validation command receives a baseline qlib provider path
- **THEN** it emits an informational comparison report
- **AND** it does not automatically change the runtime `provider_uri`

#### Scenario: comparison finds differences
- **WHEN** comparison detects coverage or price differences
- **THEN** those differences are reported with enough context for review
- **AND** official metric semantics remain anchored to whichever provider the operator explicitly configured

### Requirement: Tushare VWAP conversion SHALL apply adjustment factors exactly once

Generated qlib `vwap` values SHALL be expressed on the same adjustment basis as
OHLC fields. When raw traded value/volume is unavailable and close is used as a
fallback, the fallback SHALL start from raw close and apply the selected
adjustment scale exactly once.

#### Scenario: zero-volume row uses close fallback
- **WHEN** a Tushare daily row has zero volume and adjusted output is requested
- **THEN** generated `vwap` equals raw close multiplied by the selected
  adjustment scale once
- **AND** it does not equal already-adjusted close multiplied by the scale again

### Requirement: Tushare client SHALL reuse its pro_api handle

The Tushare client wrapper SHALL avoid reconstructing the underlying `pro_api`
handle for every API call on the same client instance.

#### Scenario: multiple API calls use one client
- **WHEN** two Tushare API calls are made through the same `TushareClient`
- **THEN** the wrapper constructs the underlying `pro_api` client at most once
- **AND** both calls still use the same token boundary and typed error handling

