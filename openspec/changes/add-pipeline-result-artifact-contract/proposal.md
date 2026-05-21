# Proposal: Pipeline Result Artifact Contract

## Summary

Add structured pipeline result artifacts for the operator UI detail page:
`metadata.json`, `metrics.json`, `nav.parquet`, `holdings.parquet`,
`trades.parquet`, and `config.yaml`.

## Motivation

The Results dashboard can currently render only the legacy
`pipeline_report.json`, `positions.json`, generated PNG charts, job metadata,
and logs. The product design calls for a more explicit run-detail contract so
the UI can show readable sections without scraping raw report JSON.

## Scope

- Write structured artifacts from existing pipeline outputs after the canonical
  backtest and report are complete.
- Keep official metric values sourced from `CanonicalBacktestOutput` and qlib's
  anchored metric helper output.
- Write display-oriented NAV and holdings parquet files from the canonical
  return series and positions map.
- Write an empty, schema-correct `trades.parquet` until the canonical runtime
  exposes trade logs.
- Update the operator UI Results page to prefer these structured artifacts and
  fall back to existing artifacts when absent.

## Non-Goals

- No new official metric calculations.
- No trade reconstruction from positions or predictions.
- No walk-forward artifact schema change.
- No runtime trading behavior change.

