## ADDED Requirements

### Requirement: Operator UI SHALL provide polished pipeline result inspection controls

The Results page SHALL add polish interactions for pipeline result inspection
without recomputing official metrics or mutating stored runtime artifacts.

#### Scenario: sticky header and run navigation are available

- **WHEN** the operator views a pipeline result
- **THEN** the header remains visible while scrolling
- **AND** the page offers a way back to the job list
- **AND** run id and run directory are exposed as copyable text, not hidden
  behind browser-specific file explorer behavior

#### Scenario: NAV and drawdown use a shared displayed time range

- **WHEN** `nav.parquet` exists
- **THEN** the Results page offers one time-range selector for NAV and drawdown
- **AND** both charts are rendered from the same filtered artifact rows
- **AND** filtering affects only the displayed chart data

#### Scenario: monthly returns heatmap is artifact sourced

- **WHEN** `metrics.json` contains `monthly_returns`
- **THEN** the Results page renders a monthly return heatmap from those rows
- **AND** the raw monthly return rows remain visible in a table
- **AND** missing monthly data displays an empty state

#### Scenario: logs can be searched and filtered

- **WHEN** job log artifacts exist
- **THEN** the Logs tab lets the operator search log text and filter by
  severity level
- **AND** filtering changes only displayed log lines
- **AND** missing logs continue to show an empty state
