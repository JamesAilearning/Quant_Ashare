## ADDED Requirements

### Requirement: Operator UI SHALL update run availability when provider URI changes

The operator UI SHALL render the training `provider_uri` input outside the
Streamlit form whose submit button depends on it, or otherwise ensure that
editing `provider_uri` rerenders the page before submission. A valid non-empty
provider URI SHALL enable the run button without requiring a failed submit or
manual page refresh.

#### Scenario: provider URI is entered

- **WHEN** the Config & Run page initially renders with an empty `provider_uri`
- **AND** the operator enters `D:/qlib_data/my_cn_data`
- **THEN** the run button is enabled on the next Streamlit rerun
- **AND** the stale "provider_uri is required" warning is not shown

---

### Requirement: Operator UI SHALL launch Tushare provider ingest through the existing CLI boundary

The operator UI SHALL provide a Tushare provider ingest action that writes a
Tushare provider config under the UI job directory and launches
`scripts/ingest_tushare_qlib_provider.py` through `JobManager` /
`job_runner.py` with `shell=False`. The UI SHALL NOT store Tushare tokens in
job config files and SHALL rely on the existing `TUSHARE_TOKEN` environment
variable boundary.
The UI job and result roots SHALL be anchored to the repository root so runner
subprocesses and artifact readers do not depend on the Streamlit process
current working directory.

#### Scenario: Tushare token is present

- **WHEN** the operator clicks "Pull Tushare Data" with valid date and output settings
- **THEN** `JobManager.start()` creates a `tushare_provider` job
- **AND** `job_runner.py` launches `scripts/ingest_tushare_qlib_provider.py`
- **AND** the generated config does not contain a token field
- **AND** the generated job directory and result paths are under the repository
  `output/operator_ui/` tree

#### Scenario: Tushare token is absent

- **WHEN** the UI process has no `TUSHARE_TOKEN`
- **THEN** the Tushare ingest button is disabled or the action fails before starting a job
- **AND** the UI tells the operator to set `TUSHARE_TOKEN` in the environment

## MODIFIED Requirements

### Requirement: Operator UI SHALL support stopping a running job

The operator UI SHALL support stopping a job launched through the UI.
Stopping SHALL terminate the runner process and, when the platform and launch
mode support it, its child CLI process group. Windows SHALL use
`taskkill /F /T /PID <runner_pid>` with `shell=False`. Non-Windows platforms
SHALL use POSIX signals rather than Windows-only commands. The UI SHALL NOT
mark a job as `stopped` unless the termination action succeeds.

#### Scenario: a running job is stopped on Windows

- **WHEN** the operator clicks Stop for a job with status "running"
- **AND** the UI is running on Windows
- **THEN** `taskkill /F /T /PID <runner_pid>` is executed with `shell=False`
- **AND** `job.json` is updated to `status: "stopped"` with `ended_at`

#### Scenario: a running job is stopped on non-Windows

- **WHEN** the operator clicks Stop for a job with status "running"
- **AND** the UI is running on a non-Windows platform
- **THEN** a POSIX termination signal is sent to the runner process or its
  process group
- **AND** `job.json` is updated to `status: "stopped"` with `ended_at`

#### Scenario: stopping a running job fails

- **WHEN** the platform termination command or signal fails
- **THEN** `JobManager.stop()` raises a typed job manager error
- **AND** `job.json` is updated to `status: "stop_failed"`
- **AND** the job is not represented as successfully stopped
