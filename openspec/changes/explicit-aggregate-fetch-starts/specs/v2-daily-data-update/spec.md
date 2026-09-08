## ADDED Requirements

### Requirement: Daily update SHALL forward explicit aggregate starts without widening prices

Daily update SHALL accept optional namechange, suspend_d and index_weight start
dates and forward each explicitly provided value to the fetch stage. Omitted
values SHALL retain the shared start. Price ticker-year acquisition and benchmark
arguments SHALL retain their existing common start, and all endpoints SHALL retain
the frozen run end. Invalid new options SHALL fail before operational writes;
dry-run SHALL display the explicit overrides without running stages. No history
range SHALL be inferred from prior metadata as automatic permission to widen.

#### Scenario: Mixed production history retains price scope
- **WHEN** the common start is 20180101 and explicit aggregate starts are
  19900101, 20151001 and 20000101 respectively
- **THEN** fetch receives all three overrides while its common start and the
  benchmark start remain 20180101
- **AND** no new price years are requested solely because aggregates are wider

#### Scenario: Invalid override is rejected before run state changes
- **WHEN** a supplied aggregate start is not a real eight-digit ASCII date or
  exceeds the resolved execution end
- **THEN** configuration or plan validation refuses before locking, status
  writes, startup repair or any fetch/build action

#### Scenario: Legacy configuration stays explicit and unchanged
- **WHEN** no aggregate starts are supplied
- **THEN** the existing fetch and benchmark range and index refresh cadence
  remain unchanged, including existing range-protection refusals
