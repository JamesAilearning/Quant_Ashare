# Proposal: Polish Pipeline Result Detail Dashboard

## Summary

Improve the operator UI Results page toward the pipeline result detail product
spec with polish-focused, artifact-only interactions: sticky header navigation,
shared chart time ranges, monthly return heatmap, log filtering, and clearer
operator affordances.

## Motivation

PR90 established the core readable dashboard and artifact contract. The next
gap is usability: researchers should be able to scan a run, move between jobs,
filter logs, and inspect time-windowed charts without losing the canonical
read-only artifact boundary.

## Scope

- Make the pipeline result header sticky and add navigation/copyable run
  affordances that stay in the Streamlit UI boundary.
- Add a shared time-range selector that filters NAV and drawdown charts
  together from `nav.parquet`.
- Render `monthly_returns` as a heatmap/table pair from `metrics.json`.
- Add log search and severity filtering for existing log artifacts.
- Add regression tests and OpenSpec coverage for the UI polish behavior.

## Non-Goals

- No canonical runtime, data ingestion, model training, or metric calculation
  changes.
- No browser-native file explorer integration; browsers cannot reliably open
  local folders from a Streamlit page.
- No custom JavaScript component for global keyboard shortcuts in this PR.
