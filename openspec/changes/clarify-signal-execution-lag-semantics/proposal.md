## Why

`signal_to_execution_lag=1` currently performs no shift, even though operators
normally read that value as "execute one trading day after the signal." This is
a look-ahead trap for A-share close-to-next-day workflows and can change
official metrics without the user realizing it.

## What Changes

- Redefine canonical lag semantics so `lag=0` is the explicit no-op path and
  `lag=N` shifts signals by N trading rows.
- Update pipeline and walk-forward validation to allow `lag=0` only as an
  explicit same-day execution mode.
- Keep `signal_to_execution_lag=1` as the default, now representing the common
  T+1 execution convention.
- Add regression tests proving lag shifts predictions and no-op behavior is
  explicit.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `v2-canonical-backtest-contract`: Clarifies canonical signal-to-execution lag
  semantics and permits explicit same-day execution with `lag=0`.

## Impact

- Affected code: `src/core/backtest_runner.py`,
  `src/core/canonical_backtest_contract.py`, `src/core/pipeline.py`, and
  `src/core/walk_forward.py`.
- Affected tests: backtest runner, canonical contract, pipeline config, and
  walk-forward config tests.
- **BREAKING semantic change**: existing configs that relied on `lag=1` as a
  no-op must set `signal_to_execution_lag: 0` to preserve same-day execution.
