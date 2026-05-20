# Design: Pipeline Result Artifact Contract

## Artifact Source Rules

- `metrics.json` mirrors official qlib-derived risk metrics already present on
  `CanonicalBacktestOutput.risk_analysis`. It does not compute substitute
  official metrics.
- `nav.parquet` is a display series derived from
  `CanonicalBacktestOutput.return_series`; it is not an additional official
  metric source.
- `holdings.parquet` is a long-form projection of
  `CanonicalBacktestOutput.positions`.
- `trades.parquet` is written with the expected schema and zero rows because
  the canonical runtime does not currently expose trade logs. The metadata file
  records this status explicitly.
- `config.yaml` is a normalized `PipelineConfig` dump for CLI runs. UI-launched
  jobs may overwrite it with the exact runtime config bytes from the job
  directory after the CLI exits.

## Failure Handling

Structured artifact writing is report-adjacent. If writing these files fails
after the canonical backtest/report completed, the pipeline logs a warning and
keeps returning the normal `PipelineResult`; it does not hide or recompute
official metrics.

## UI Behavior

The Results page reads the structured artifacts when present. If they are
missing, it falls back to `pipeline_report.json` and `positions.json`, preserving
compatibility with older runs and partially completed jobs.

