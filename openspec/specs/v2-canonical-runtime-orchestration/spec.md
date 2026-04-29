# v2-canonical-runtime-orchestration Specification

## Purpose

Define orchestration requirements for runtime entry points that construct
canonical qlib initialization and backtest requests.

## Requirements

### Requirement: Canonical runtime orchestration SHALL preserve backtest execution semantics

Runtime orchestration components and shipped runtime CLIs SHALL preserve
caller-supplied canonical backtest controls when constructing canonical
runtime/backtest config instead of substituting hidden defaults. This includes
price adjustment mode, signal-to-execution lag, execution price kind, minimum
cost, and limit threshold.

#### Scenario: Pipeline constructs a canonical backtest request
- **WHEN** `Pipeline.run()` builds `CanonicalBacktestInput`
- **THEN** the request's `adjust_mode` matches `PipelineConfig.adjust_mode`
- **AND** qlib runtime initialization records the same provider adjustment mode
- **AND** exchange and cost controls come from `PipelineConfig`

#### Scenario: WalkForward constructs a canonical backtest request
- **WHEN** `WalkForwardEngine` builds a fold's `CanonicalBacktestInput`
- **THEN** the request's `adjust_mode` comes from `WalkForwardConfig`
- **AND** the request's `signal_to_execution_lag` comes from
  `WalkForwardConfig`
- **AND** the exchange config's `execution_price_kind`, `min_cost`, and
  `limit_threshold` come from `WalkForwardConfig`

#### Scenario: Walk-forward CLI initializes qlib runtime
- **WHEN** `scripts/run_walk_forward.py` loads a walk-forward YAML config
- **THEN** the returned `WalkForwardConfig.adjust_mode` is also used as
  `QlibRuntimeConfig.data_adjust_mode`
- **AND** qlib runtime initialization cannot silently use an adjustment mode
  different from the fold backtest requests

#### Scenario: WalkForward receives an unsupported canonical control
- **WHEN** a caller constructs `WalkForwardConfig` with an unsupported
  adjustment mode, execution price kind, signal lag, cost, or limit threshold
- **THEN** a typed `WalkForwardError` or canonical contract error is raised
- **AND** no fold backtest is executed with hidden substitute semantics
