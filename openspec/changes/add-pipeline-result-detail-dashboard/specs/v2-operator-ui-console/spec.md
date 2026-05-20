## ADDED Requirements

### Requirement: Operator UI SHALL render pipeline results as a readable detail dashboard

The operator UI Results page SHALL render completed, failed, and running
pipeline jobs with a structured detail view built from existing UI job metadata
and runtime artifacts. The dashboard SHALL include a run header, status,
artifact-derived KPI cards, generated charts, detail tabs, and a collapsed raw
JSON fallback. It SHALL NOT require every artifact to exist before rendering.

#### Scenario: pipeline report exists

- **WHEN** the operator opens Results for a pipeline job with
  `pipeline_report.json`
- **THEN** the UI displays pipeline KPI cards using values from that report
- **AND** generated PNG charts are displayed when present
- **AND** the raw report remains available in a collapsed Raw JSON panel

#### Scenario: run_id query parameter selects a run

- **WHEN** the operator opens Results with `run_id=<job_id>`
- **THEN** the UI selects that job directly
- **AND** an unknown run id displays a page-level error rather than silently
  falling back to another run

#### Scenario: nav artifact exists

- **WHEN** the selected pipeline run contains `nav.parquet`
- **THEN** the UI renders interactive NAV and drawdown charts from that
  artifact
- **AND** the chart rendering does not recompute official KPI metrics

#### Scenario: pipeline report is not yet available

- **WHEN** the operator opens Results for a running or partially completed
  pipeline job
- **THEN** the UI still displays job metadata, config, progress, and logs that
  are available
- **AND** report-dependent sections show `N/A` or an empty-state message
- **AND** no substitute metric is computed by the UI

#### Scenario: exact runtime config is available

- **WHEN** the UI job directory contains `config.yaml`
- **THEN** the Results page allows the operator to download those exact bytes
- **AND** the UI does not rewrite or normalize the config before download

#### Scenario: optional detail artifacts are absent

- **WHEN** positions, trade logs, generated charts, or log files are absent
- **THEN** the corresponding dashboard section displays an empty state
- **AND** the rest of the page continues to render

