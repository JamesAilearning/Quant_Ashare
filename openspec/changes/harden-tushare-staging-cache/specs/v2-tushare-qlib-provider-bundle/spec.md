## MODIFIED Requirements

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
