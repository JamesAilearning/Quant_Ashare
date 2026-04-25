## MODIFIED Requirements

### Requirement: Canonical backtest contract SHALL forbid implicit fallback semantics

The canonical contract SHALL require explicit behavior for missing dependencies
and SHALL NOT allow hidden fallback paths that change official metric meaning
without explicit labeling. Official backtest execution SHALL require the
canonical qlib runtime to be initialized through the approved runtime entry
point before any official output can be produced.

#### Scenario: missing canonical qlib initialization occurs
- **WHEN** `BacktestRunner.run()` is called before
  `src.core.qlib_runtime.init_qlib_canonical(...)` has completed
- **THEN** a typed `BacktestRunnerError` is raised
- **AND** `qlib.backtest.backtest` is not called
- **AND** no official metric output is produced

#### Scenario: missing canonical dependency occurs
- **WHEN** a required canonical dependency is unavailable
- **THEN** contract behavior is explicitly defined
- **AND** no implicit hidden fallback changes official metric semantics

### Requirement: Canonical backtest input SHALL require an explicit price-adjustment mode

The canonical backtest input SHALL require an `adjust_mode` field whose value is
one of `pre_adjusted`, `post_adjusted`, or `unadjusted`. There SHALL be no
default. Runtime execution SHALL treat the field as an execution boundary, not
only as provenance: an official backtest SHALL run only when the request's
adjustment mode matches the initialized qlib provider adjustment mode.

#### Scenario: unknown adjust_mode is rejected
- **WHEN** a caller supplies `adjust_mode="auto"`
- **THEN** `CanonicalBacktestContract.validate_input` raises
  `CanonicalBacktestContractError`
- **AND** the error message lists the allowed values

#### Scenario: requested adjustment mode differs from provider adjustment mode
- **WHEN** canonical qlib runtime is initialized with provider adjustment mode
  `pre_adjusted`
- **AND** a canonical backtest request supplies `adjust_mode="unadjusted"`
- **THEN** `BacktestRunner.run()` raises `BacktestRunnerError`
- **AND** `qlib.backtest.backtest` is not called
- **AND** no official metric output is produced

#### Scenario: requested adjustment mode matches provider adjustment mode
- **WHEN** canonical qlib runtime is initialized with provider adjustment mode
  `pre_adjusted`
- **AND** a canonical backtest request supplies `adjust_mode="pre_adjusted"`
- **THEN** `BacktestRunner.run()` may proceed to the anchored qlib backtest
  callable after all other contract checks pass
