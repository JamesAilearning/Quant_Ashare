## ADDED Requirements

### Requirement: Explicit aggregate starts SHALL agree across requests guards and coverage

The fetcher and fetch CLI SHALL accept optional namechange, suspend_d and
index_weight start dates. A supplied value SHALL be a real eight-digit ASCII
date no later than the common end. An omitted value SHALL use the existing common
start. The endpoint-effective start SHALL drive its actual data requests, index
monthly window construction, overwrite containment guard and the manifest's
established coverage. The schema and stable hole units SHALL remain unchanged.
Invalid new options SHALL be refused before client construction or metadata reset.

An override SHALL NOT force an otherwise-skipped index refresh, widen ticker-year
or benchmark ranges, remove guards, or authorize overwriting unknown history.
Blind skips SHALL still establish no coverage; hole-only runs SHALL retain their
requested scope and existing merge containment rules. Trade-calendar behavior
SHALL remain unchanged.

#### Scenario: A wider aggregate refresh preserves its earlier history
- **WHEN** a trusted aggregate has history before the common price start and an
  explicit override covers that history through the requested end
- **THEN** the guard and API use the same wider interval and the manifest records
  that endpoint interval without claiming it for price endpoints

#### Scenario: Explicit does not mean safe without containment
- **WHEN** an override is still later than prior coverage, the end goes backward,
  or an existing selected aggregate lacks usable prior provenance
- **THEN** existing preservation/refusal behavior remains; no data request or
  overwrite occurs for that target, and a prior hole cannot be silently healed

#### Scenario: Index windows use the override but resume does not claim a fetch
- **WHEN** index retry is selected with an explicit wider start
- **THEN** monthly requests cover that effective start and complete before publication
- **WHEN** all existing indices are legitimately skipped without retry
- **THEN** the override does not advance their manifest coverage

#### Scenario: Invalid new options cannot erase a manifest
- **WHEN** fetch is given an invalid explicit aggregate start together with reset
- **THEN** it returns a configuration failure before removing the prior manifest,
  constructing a client or changing any raw file
