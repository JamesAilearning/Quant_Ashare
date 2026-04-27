# Canonical Backtest Contract (V2)

Date: 2026-04-26

## Purpose

Define and guard the single canonical official-metrics contract for V2 runtime
backtests.

## Canonical Path Boundary

- Canonical official metrics path identifier:
  - `qlib.backtest.backtest`
- Canonical official metric helper:
  - `qlib.contrib.evaluate.risk_analysis`
- Official metrics status:
  - `official`
- Canonical source must remain singular.
- Direct callers must initialize qlib through
  `src.core.qlib_runtime.init_qlib_canonical(...)` before official metrics can
  be produced.

## Canonical Input Boundary

Required:
- `predictions_ref`
- `evaluation_start`
- `evaluation_end`
- `account_config`
- `exchange_config`
- `benchmark_code`
- `adjust_mode`
- `signal_to_execution_lag`

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
- `positions`

Note:
- Runtime execution is implemented by `src.core.backtest_runner.BacktestRunner`
  and remains bound to the qlib-native canonical path above.
- `return_series` must remain structured date-to-float mappings for `return`,
  `bench`, and `cost`; unknown qlib shapes are runtime errors, not raw-string
  fallbacks.

## Non-Canonical Separation

- Experimental runtime logic is non-canonical.
- Research artifacts under `research/factor_lab/` are non-production and non-canonical by default.
- No experimental/research output may be silently promoted to official metrics.

## Validation Expectations (Minimum)

- Contract must assert single canonical official path.
- Contract must reject implicit fallback semantics.
- Contract must reject experimental/research leakage into canonical input.
- Regression tests must lock the above boundaries.
