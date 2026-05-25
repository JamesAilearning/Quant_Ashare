## ADDED Requirements

### Requirement: Operator UI job access SHALL path-guard job identifiers

The operator UI SHALL resolve job identifiers through a child-directory guard
before reading or writing job directories. The guard SHALL reject empty values,
path separators, and resolved paths outside `output/operator_ui/jobs`.

#### Scenario: stop rejects traversal job id

- **WHEN** `JobManager.stop()` receives a job id containing path traversal
- **THEN** the UI refuses the operation
- **AND** no job state outside the UI job root is read or written

#### Scenario: status rejects traversal job id

- **WHEN** `JobManager.status()` receives a job id containing path traversal
- **THEN** the UI refuses the operation
- **AND** no job state outside the UI job root is read

---

### Requirement: Operator UI job state writes SHALL use shared atomic helpers

The operator UI SHALL use shared job.json read/write helpers for manager and
runner state updates. Writes SHALL merge with existing state and replace the
job.json file atomically under a lightweight per-job lock.

#### Scenario: manager and runner update job state

- **WHEN** the manager writes lifecycle metadata
- **AND** the runner writes completion metadata
- **THEN** both use the same job state helper
- **AND** partially written job.json files are not exposed

---

### Requirement: Operator UI runner SHALL parse config YAML structurally

The operator UI runner SHALL parse `config.yaml` as YAML when discovering
`output_dir`. It SHALL NOT infer `output_dir` through ad-hoc line splitting.

#### Scenario: output_dir has YAML quoting and comments

- **WHEN** `config.yaml` contains a quoted `output_dir` with an inline comment
- **THEN** the runner discovers the configured output directory correctly

---

### Requirement: Operator UI stop handling SHALL record stopped jobs

The operator UI runner SHALL record `stopped` status when it receives a stop
signal before normal CLI completion. The manager SHALL continue to record stop
failures loudly when the platform stop command fails.

#### Scenario: runner receives SIGTERM

- **WHEN** the runner receives SIGTERM after a job directory is known
- **THEN** it writes `status=stopped`
- **AND** it records an `ended_at` timestamp

---

### Requirement: Operator UI pages SHALL handle nullable or invalid UI state

The operator UI SHALL display user-correctable validation errors without a raw
Streamlit traceback. Run History SHALL tolerate missing or `null` timestamp
fields in job and catalog records.

#### Scenario: config contains an unknown key

- **WHEN** UI config validation rejects generated config keys
- **THEN** Config & Run displays the validation error
- **AND** the page stops without launching a job

#### Scenario: run history timestamp is null

- **WHEN** a job or catalog entry has `started_at` or `completed_at` set to null
- **THEN** Run History renders an empty timestamp cell instead of raising
