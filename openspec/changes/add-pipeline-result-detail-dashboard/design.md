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

## Compatibility

The product spec describes a future richer backend schema (`metrics.json`,
`nav.parquet`, `holdings.parquet`, `trades.parquet`). This change deliberately
does not invent those files. The dashboard presents the current artifact
contract and uses clear empty states for artifacts that are not produced yet
(for example trade logs).

## Governance

This is an operator-facing display change. It does not call `Pipeline.run()`,
`WalkForwardEngine.run()`, qlib data APIs, Tushare APIs, or metric calculators.
Official metrics remain those written by the canonical runtime into report
artifacts.

