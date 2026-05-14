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

#### Scenario: Tushare token is present

- **WHEN** the operator clicks "Pull Tushare Data" with valid date and output settings
- **THEN** `JobManager.start()` creates a `tushare_provider` job
- **AND** `job_runner.py` launches `scripts/ingest_tushare_qlib_provider.py`
- **AND** the generated config does not contain a token field

#### Scenario: Tushare token is absent

- **WHEN** the UI process has no `TUSHARE_TOKEN`
- **THEN** the Tushare ingest button is disabled or the action fails before starting a job
- **AND** the UI tells the operator to set `TUSHARE_TOKEN` in the environment
