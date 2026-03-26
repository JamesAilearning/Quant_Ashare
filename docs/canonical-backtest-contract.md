# Canonical Backtest Contract (V2, Foundation)

Date: 2026-03-24

## Purpose

Define the single canonical official-metrics contract before runtime backtest implementation begins.

## Canonical Path Boundary

- Canonical official metrics path identifier:
  - `qlib-native backtest_daily`
- Official metrics status:
  - `official`
- Canonical source must remain singular.

## Canonical Input Boundary

Required:
- `predictions_ref`
- `evaluation_start`
- `evaluation_end`
- `account_config`
- `exchange_config`

Optional:
- `benchmark_code`

Explicitly non-canonical (rejected by contract input):
- `experimental_controls`
- `research_artifact_refs`
- non-canonical `source_layer`
- `allow_implicit_fallback=True`

## Canonical Output Boundary

Contract output fields (schema-level placeholder):
- `metric_status`
- `official_backtest_path`
- `return_series`
- `risk_analysis`
- `report`
- `provenance`

Note:
- Runtime execution remains intentionally unimplemented in this change.

## Non-Canonical Separation

- Experimental runtime logic is non-canonical.
- Research artifacts under `research/factor_lab/` are non-production and non-canonical by default.
- No experimental/research output may be silently promoted to official metrics.

## Validation Expectations (Minimum)

- Contract must assert single canonical official path.
- Contract must reject implicit fallback semantics.
- Contract must reject experimental/research leakage into canonical input.
- Regression tests must lock the above boundaries.
