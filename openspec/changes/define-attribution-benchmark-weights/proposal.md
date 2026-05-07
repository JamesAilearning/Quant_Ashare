## Why

Brinson attribution currently compares portfolio sector weights to an implicit
equal-weight benchmark. Real A-share benchmarks such as HS300 are market-cap
weighted, so unlabeled equal-weight attribution can be mistaken for index
relative attribution.

## What Changes

- Add explicit benchmark-weight semantics to performance attribution.
- Keep the existing equal-weight calculation available, but label it as an
  `equal_weight_proxy`.
- Allow callers to pass explicit benchmark weights for Brinson attribution.
- Fail loudly when a market-cap/index-weight method is requested without an
  approved weight source instead of silently falling back to equal weight.

## Capabilities

### New Capabilities

- `v2-attribution-benchmark-weights`: Defines benchmark-weight source semantics
  for performance attribution.

### Modified Capabilities

- None.

## Impact

- Affected code: `src/core/performance_attribution.py`, pipeline/walk-forward
  attribution config wiring if needed, and attribution tests.
- No change to canonical backtest metrics; this affects the optional attribution
  analysis block only.
- Existing attribution calls remain compatible and now report the benchmark
  weight method explicitly.
