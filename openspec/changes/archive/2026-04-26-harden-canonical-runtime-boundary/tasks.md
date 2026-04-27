## 1. Runtime Init Semantics

- [x] 1.1 Add explicit provider adjustment metadata to `QlibRuntimeConfig`.
- [x] 1.2 Validate the runtime adjustment metadata against the canonical
  adjustment enum.
- [x] 1.3 Include adjustment metadata in idempotent re-init comparison so the
  same provider with a different declared adjustment mode is rejected.
- [x] 1.4 Update every `QlibRuntimeConfig(...)` call site in source and tests to
  pass the adjustment metadata explicitly.

## 2. BacktestRunner Boundary

- [x] 2.1 Import and use `is_canonical_qlib_initialized()` and
  `get_canonical_qlib_config()` in `BacktestRunner.run()`.
- [x] 2.2 Raise `BacktestRunnerError` before qlib backtest import/execution when
  canonical qlib init has not completed.
- [x] 2.3 Raise `BacktestRunnerError` when `request.adjust_mode` does not match
  the initialized provider adjustment mode.
- [x] 2.4 Ensure successful outputs still use
  `CANONICAL_OFFICIAL_BACKTEST_PATH == "qlib.backtest.backtest"` and the
  anchored official metric helper.

## 3. WalkForward Passthrough

- [x] 3.1 Add `execution_price_kind`, `adjust_mode`,
  `signal_to_execution_lag`, `min_cost`, and `limit_threshold` to
  `WalkForwardConfig`.
- [x] 3.2 Validate new WalkForward fields enough to reject unsupported enum
  values and invalid lag/cost/limit values before folds run.
- [x] 3.3 Pass the new fields through to `CanonicalExchangeConfig`,
  `CanonicalExchangeCostModel`, and `CanonicalBacktestInput` in
  `_run_single_fold`.
- [x] 3.4 Confirm Pipeline continues to initialize qlib and construct
  `CanonicalBacktestInput` from the same `PipelineConfig.adjust_mode` value.

## 4. Regression Tests

- [x] 4.1 Add or extend governance tests for `QlibRuntimeConfig` adjustment
  metadata validation and re-init mismatch rejection.
- [x] 4.2 Add BacktestRunner tests proving missing canonical init fails before
  qlib backtest execution.
- [x] 4.3 Add BacktestRunner tests proving adjustment-mode mismatch fails
  loudly.
- [x] 4.4 Add WalkForward tests proving configured backtest controls are passed
  into the fold's `CanonicalBacktestInput`.
- [x] 4.5 Confirm no new alternative official backtest or metric path is
  introduced under `src/core/`.

## 5. Quality Gates

- [x] 5.1 Run targeted governance tests for qlib init and canonical backtest
  boundaries.
- [x] 5.2 Run targeted logic tests for BacktestRunner and WalkForward.
- [x] 5.3 Run broader test suite if the local environment permits it; otherwise
  document the blocker.
- [x] 5.4 Run `openspec validate harden-canonical-runtime-boundary --strict` if
  the OpenSpec CLI is available. Attempted; CLI is not available in PATH.
- [x] 5.5 Review scope drift: no risk-constraint implementation, no dependency
  cleanup, no artifact-loader cleanup, and no docs/archive cleanup in this
  change.
