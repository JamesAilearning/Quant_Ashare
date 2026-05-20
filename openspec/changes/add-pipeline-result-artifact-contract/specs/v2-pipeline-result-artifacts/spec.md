## ADDED Requirements

### Requirement: Pipeline SHALL write structured result artifacts for UI detail views

The pipeline SHALL write structured, read-only result artifacts under the run
directory for operator UI consumption after its canonical backtest and
report-writing step completes. These artifacts SHALL be projections of existing
canonical outputs and SHALL NOT create a competing official metric path.

#### Scenario: canonical outputs are available

- **WHEN** a pipeline run has a `CanonicalBacktestOutput`
- **THEN** the run directory contains `metadata.json`, `metrics.json`,
  `nav.parquet`, `holdings.parquet`, `trades.parquet`,
  `predictions.parquet`, `config.yaml`, `logs/pipeline.log`,
  `logs/stage_timings.json`, and `artifacts/model.pkl`
- **AND** official metric values in `metrics.json` come from
  `CanonicalBacktestOutput.risk_analysis`
- **AND** the writer does not call qlib metric helpers or core analyzers

#### Scenario: trade logs are not exposed by the runtime

- **WHEN** the canonical runtime does not provide trade-level fills
- **THEN** `trades.parquet` is written with the expected schema and zero rows
- **AND** `metadata.json` records that trade logs are not produced by the
  current runtime
- **AND** the writer does not reconstruct trades from positions or predictions

#### Scenario: structured artifact writing fails

- **WHEN** canonical backtest/report generation has already completed
- **AND** writing structured result artifacts fails
- **THEN** the pipeline logs a warning
- **AND** the existing pipeline result remains valid
- **AND** no fallback metric computation is attempted

### Requirement: Operator UI SHALL prefer structured pipeline artifacts when present

The operator UI Results page SHALL read structured pipeline artifacts when they
exist and SHALL fall back to legacy `pipeline_report.json` and `positions.json`
for older or partially completed runs.

#### Scenario: structured artifacts are present

- **WHEN** `metrics.json`, `holdings.parquet`, or `trades.parquet` exists in
  the selected run directory
- **THEN** the Results page displays KPI, holdings, and trades sections from
  those artifacts
- **AND** raw JSON remains accessible for exact inspection

#### Scenario: structured artifacts are absent

- **WHEN** a selected run predates this artifact contract or is still running
- **THEN** the Results page falls back to existing report and positions
  artifacts where available
- **AND** missing sections display an empty state rather than fabricated values

#### Scenario: structured artifacts cannot be decoded

- **WHEN** an existing result artifact is malformed, unreadable, or outside the
  operator UI output boundary
- **THEN** the Results page displays an artifact read issue for that file
- **AND** the page continues to display other readable artifacts
- **AND** the UI does not recompute or fabricate replacement metrics
