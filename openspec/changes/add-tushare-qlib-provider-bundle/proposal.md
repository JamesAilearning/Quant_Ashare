## Why

The project can already consume Tushare industry taxonomy artifacts, but
training data still depends on an existing qlib provider bundle whose source,
adjustment convention, and refresh provenance are external to the repository.
Adding a governed Tushare-to-qlib bundle path gives operators a reproducible
way to evaluate whether Tushare OHLCV data is a better training source without
silently changing canonical training semantics.

## What Changes

- Add a Tushare OHLCV ingestion and publishing capability that materializes a
  qlib-compatible provider bundle from Tushare A-share daily bars, adjustment
  factors, trading calendar, and instrument metadata.
- Require explicit provenance and validation metadata for the generated bundle,
  including source APIs, date coverage, adjustment convention, snapshot time,
  and validation health.
- Keep Tushare provider-bundle output opt-in: existing qlib provider paths and
  canonical training/backtest behavior remain unchanged unless an operator
  explicitly points config at the generated bundle.
- Add comparison-oriented validation so the new bundle can be evaluated against
  existing qlib data before any future default-source decision.

## Capabilities

### New Capabilities

- `v2-tushare-qlib-provider-bundle`: Defines the governed Tushare OHLCV to qlib
  provider bundle pipeline, including source APIs, adjustment semantics,
  provenance, validation boundaries, and opt-in runtime use.

### Modified Capabilities

- None.

## Impact

- Affected code areas: `src/data/tushare/`, future provider-bundle publisher
  modules under `src/data/`, config examples, and tests under
  `tests/logic/` and `tests/governance/`.
- Affected external integration: Tushare Pro APIs for daily bars, adjustment
  factors, trading calendar, stock metadata, and optionally index/benchmark
  data needed for comparison.
- No breaking change to current `config.yaml`, canonical qlib initialization,
  canonical backtest path, or official metrics definition.
