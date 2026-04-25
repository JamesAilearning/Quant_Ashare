## Why

The canonical backtest runtime now executes real qlib backtests and reports
official metrics, but the runtime boundary is looser than the contract it
serves:

1. `BacktestRunner.run()` can be called directly without proving that qlib was
   initialized through `src.core.qlib_runtime.init_qlib_canonical`.
2. `CanonicalBacktestInput.adjust_mode` is required and validated, but it does
   not affect execution. Official metrics can therefore claim different
   adjustment modes while using the same provider state.
3. `WalkForwardEngine` constructs canonical backtest requests with hard-coded
   execution semantics (`adjust_mode=pre_adjusted`,
   `signal_to_execution_lag=1`, `min_cost=5.0`, and the exchange defaults)
   instead of passing through the caller's canonical controls.

These are governance risks, not tuning preferences. The V2 rules require one
canonical qlib-native official path, no hidden global-state coupling, and no
implicit fallback that changes official metric meaning.

## What Changes

- Extend canonical qlib runtime initialization metadata to include the data
  provider adjustment convention.
- Require `BacktestRunner.run()` to verify canonical qlib initialization before
  producing official metrics.
- Require `BacktestRunner.run()` to enforce `request.adjust_mode` against the
  initialized provider adjustment convention. A mismatch is a hard error; it is
  not silently translated or ignored.
- Thread canonical backtest execution controls through `WalkForwardConfig`
  rather than hard-coding them inside `_run_single_fold`.
- Add regression tests that protect direct BacktestRunner calls, adjustment-mode
  mismatch behavior, and WalkForward passthrough.

## Capabilities

### New Capabilities

- `v2-canonical-runtime-orchestration`: orchestration entry points that create
  canonical backtest requests preserve caller-supplied execution semantics.

### Modified Capabilities

- `v2-qlib-runtime-bootstrap`: canonical qlib initialization records the data
  adjustment convention and treats it as part of the singleton config.
- `v2-canonical-backtest-contract`: official backtest execution requires
  canonical qlib init and rejects adjustment-mode mismatches before producing
  official outputs.

## Impact

- Expected code areas:
  - `src/core/qlib_runtime.py`
  - `src/core/backtest_runner.py`
  - `src/core/pipeline.py`
  - `src/core/walk_forward.py`
  - focused tests under `tests/governance/` and `tests/logic/`
- No new backtest engine, metric library, or alternate official path.
- No implementation of risk constraints in canonical runtime.
- No benchmark, universe, taxonomy, run-artifact, or UI scope changes.
