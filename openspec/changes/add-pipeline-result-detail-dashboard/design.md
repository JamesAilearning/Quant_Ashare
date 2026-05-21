# Design: Pipeline Result Detail Dashboard

## Decisions

- The Results page remains a Streamlit page under `web/operator_ui/pages`.
- Pipeline detail data is read only from existing artifacts:
  `pipeline_report.json`, `positions.json`, generated PNG charts, job metadata,
  exact `config.yaml`, and job log files.
- Missing fields render as `N/A` rather than `0`.
- Raw `pipeline_report.json` and job metadata remain available in collapsed
  expanders for exact inspection.
- Generated config download uses the exact bytes from the UI job's
  `config.yaml` when available.
- Re-run prefill copies the exact `config.yaml` bytes into Streamlit session
  state and redirects to Config & Run. The operator still reviews and submits
  a new job explicitly.
- CSV, PDF, and ZIP exports are display/export helpers only. They copy values
  from existing artifacts and never compute replacement official metrics.
- Streamlit does not provide global keyboard shortcuts without a custom
  component in this codebase, so shortcut coverage is exposed as visible
  operator guidance and mirrored by buttons/tabs.

## Compatibility

The product spec describes richer backend artifacts (`metrics.json`,
`nav.parquet`, `holdings.parquet`, `trades.parquet`). This change consumes
those artifacts when present and uses clear empty states when a current run or
runtime path has not produced one yet.

## Governance

This is an operator-facing display change. It does not call `Pipeline.run()`,
`WalkForwardEngine.run()`, qlib data APIs, Tushare APIs, or metric calculators.
Official metrics remain those written by the canonical runtime into report
artifacts.

