## 1. Contract and Runtime Semantics

- [x] 1.1 Update canonical backtest input validation to accept `signal_to_execution_lag=0` and reject negative/bool values.
- [x] 1.2 Update `BacktestRunner._apply_lag` so `lag=0` is no-op and positive lag shifts by exactly that many trading rows.
- [x] 1.3 Update pipeline and walk-forward config validation messages to allow explicit same-day `lag=0`.

## 2. Regression Tests

- [x] 2.1 Update canonical contract tests for zero, negative, and bool lag behavior.
- [x] 2.2 Update backtest runner tests for `lag=0`, `lag=1`, and multi-day lag shift semantics.
- [x] 2.3 Update pipeline and walk-forward config tests for `lag=0` acceptance.

## 3. Verification

- [x] 3.1 Run targeted backtest, pipeline, and walk-forward tests.
- [x] 3.2 Run `openspec validate clarify-signal-execution-lag-semantics --strict`.
