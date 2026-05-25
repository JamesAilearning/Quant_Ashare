## ADDED Requirements

### Requirement: Operator UI SHALL display informational progress for UI-launched jobs

The operator UI SHALL display progress for jobs launched through the UI using
only job status, job logs, generated config, and existing output artifacts.
Progress display SHALL be informational and SHALL NOT recompute official
metrics, call core runtime APIs directly, or influence runtime execution.

#### Scenario: a Tushare provider job is running

- **WHEN** a `tushare_provider` job is running
- **AND** staged files, qlib provider files, manifest files, or validation files
  appear under the UI output tree
- **THEN** the UI displays a progress bar and stage label derived from those
  artifacts
- **AND** no Tushare API, qlib data API, or metric computation is invoked by
  the progress display

#### Scenario: a training job is running

- **WHEN** a `pipeline` or `walk_forward` job is running
- **AND** report, fold-report, model, position, log, or chart artifacts appear
  under the UI output tree
- **THEN** the UI displays progress derived from those artifacts
- **AND** the job continues to execute through the existing CLI subprocess
  boundary

#### Scenario: a job reaches terminal status

- **WHEN** a UI-launched job reaches `success`, `failed`, `stopped`, or
  `stop_failed`
- **THEN** the UI displays a terminal progress label consistent with the job
  status
- **AND** the job result status remains the source of truth
