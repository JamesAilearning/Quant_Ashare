## ADDED Requirements

### Requirement: Today Workbench SHALL accept only its supported recommendation schema

Today Workbench SHALL require an integer `artifact_schema_version` that
exactly equals the current supported producer schema version before presenting
a dated daily-recommendation artifact as a current daily, HOLD, or rebalance
signal. Missing, boolean, non-integer, or unsupported version values SHALL be
classified as `needs_verification` before provenance or cadence is used to
render a signal state.

#### Scenario: artifact schema version is missing or unsupported
- **WHEN** the newest dated recommendation artifact has matching dates and
  otherwise plausible metadata but omits `artifact_schema_version` or supplies
  an unsupported value
- **THEN** Today Workbench SHALL render `needs_verification`
- **AND** it SHALL NOT render the artifact as HOLD, rebalance, or a current
  daily signal

#### Scenario: artifact schema version matches the producer contract
- **WHEN** the newest dated recommendation artifact carries the exactly
  supported integer schema version
- **THEN** Today Workbench SHALL continue with its existing metadata,
  provenance, candidate-list, and cadence checks

### Requirement: Today Workbench SHALL not mutate job lifecycle artifacts while summarising operations

Today Workbench SHALL obtain its unified operational summary through a
non-mutating job-list reader. That read path SHALL NOT reconcile zombie jobs,
write `job.json`, or change a job's recorded lifecycle state. The existing Jobs
page MAY continue to use its reconciling lifecycle list.

#### Scenario: workbench reads a dead process recorded as running
- **WHEN** a UI job record is `running` but its PID is confirmed absent
- **THEN** the Today Workbench reader SHALL return a summary from the recorded
  job artifact without changing its `job.json`
- **AND** opening Today Workbench SHALL NOT create an `ended_at` or a failure
  reason for that job

#### Scenario: Jobs page lists the same dead process
- **WHEN** the operational Jobs page reads the same confirmed-dead running job
- **THEN** its existing reconciliation path MAY mark the lifecycle artifact as
  failed according to its job-management policy
