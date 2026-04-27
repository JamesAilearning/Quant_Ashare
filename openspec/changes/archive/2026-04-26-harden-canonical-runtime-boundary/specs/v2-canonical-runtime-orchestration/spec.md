## ADDED Requirements

### Requirement: Canonical runtime orchestration SHALL preserve backtest execution semantics

Runtime orchestration components that construct `CanonicalBacktestInput` SHALL
preserve caller-supplied canonical backtest controls instead of substituting
hidden defaults. This includes price adjustment mode, signal-to-execution lag,
execution price kind, minimum cost, and limit threshold.

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

#### Scenario: WalkForward receives an unsupported canonical control
- **WHEN** a caller constructs `WalkForwardConfig` with an unsupported
  adjustment mode, execution price kind, signal lag, cost, or limit threshold
- **THEN** a typed `WalkForwardError` or canonical contract error is raised
- **AND** no fold backtest is executed with hidden substitute semantics
