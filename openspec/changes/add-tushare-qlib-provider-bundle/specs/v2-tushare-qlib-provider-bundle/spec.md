## ADDED Requirements

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

### Requirement: Publisher validation SHALL reject malformed staged market data

Before publishing a final qlib provider bundle, the system SHALL validate staged
Tushare data for required columns, parseable dates, duplicate instrument-date
rows, invalid OHLCV values, calendar alignment, instrument coverage, and date
coverage. Validation failures SHALL be explicit and auditable.

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

### Requirement: Generated bundles SHALL include provenance and validation manifests

Each successful publish SHALL write a sidecar manifest that records source name,
source APIs, source package version when available, requested date range,
actual coverage range, instrument count, row counts, output adjustment mode,
snapshot timestamp, validation health, and publisher version. The manifest SHALL
exclude secrets and SHALL be sufficient for later training runs to identify the
data bundle used.

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
