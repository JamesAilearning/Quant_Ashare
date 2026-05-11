## Why

Recent review identified three reliability gaps around post-backtest
walk-forward steps and Tushare bundle validation. These gaps can either erase
completed official backtest outputs when optional attribution fails, allow
non-finite market data into a generated provider bundle, or leave operators
without explicit diagnostics when walk-forward test windows overlap or have
coverage gaps.

## What Changes

- Preserve completed walk-forward fold outputs when optional attribution raises
  an unexpected exception after the canonical backtest has completed.
- Reject non-finite staged Tushare OHLCV values during provider-bundle
  validation.
- Add explicit walk-forward test-window coverage diagnostics so gap/overlap
  behavior is visible without rejecting valid rolling configurations.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `v2-canonical-runtime-orchestration`: walk-forward optional attribution
  degradation and test-window coverage diagnostics.
- `v2-tushare-qlib-provider-bundle`: staged OHLCV validation rejects
  non-finite values.

## Impact

- Affected runtime code: `src/core/walk_forward/engine.py` and aggregate report
  generation.
- Affected data publishing code: `src/data/tushare/provider_bundle/publisher.py`.
- Affected tests: focused walk-forward and Tushare provider bundle tests.
- No new external dependencies and no change to canonical qlib-native backtest
  metric calculation.
