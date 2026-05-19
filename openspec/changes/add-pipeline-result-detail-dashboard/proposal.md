# Proposal: Pipeline Result Detail Dashboard

## Summary

Replace the Results page's JSON-first pipeline rendering with a readable
operator dashboard for a single pipeline run while preserving the existing
read-only artifact boundary.

## Motivation

Pipeline runs currently expose their output through raw JSON blocks and a list
of chart files. That is useful for debugging but hard for researchers to scan.
The UI should surface the same `pipeline_report.json`, generated charts,
positions, config, and logs as a structured detail page without recomputing
official metrics or introducing a new runtime result schema.

## Scope

- Add pipeline-focused header, KPI cards, chart sections, detail tabs, and raw
  JSON fallback to the Results page.
- Keep Tushare provider result rendering read-only.
- Keep walk-forward rendering simple; walk-forward detail remains owned by the
  existing Walk-Forward page.
- Add regression tests that protect the read-only artifact boundary and the new
  pipeline detail sections.

## Non-Goals

- No new pipeline runtime artifacts such as `metrics.json`, `nav.parquet`, or
  `trades.parquet`.
- No new metric calculations in the UI.
- No canonical runtime, data ingestion, or model-training behavior changes.

