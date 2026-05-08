## MODIFIED Requirements

### Requirement: Canonical runtime orchestration SHALL preserve backtest execution semantics

Runtime orchestration components and shipped runtime CLIs SHALL preserve
caller-supplied canonical backtest controls when constructing canonical
runtime/backtest config instead of substituting hidden defaults. This includes
price adjustment mode, signal-to-execution lag, execution price kind, minimum
cost, and limit threshold.

#### Scenario: Pipeline rejects boolean signal lag
- **WHEN** a caller constructs `PipelineConfig` with
  `signal_to_execution_lag=True` or `signal_to_execution_lag=False`
- **THEN** Pipeline config construction raises a typed `PipelineError`
- **AND** no feature building, model training, qlib initialization, or backtest
  work starts before the malformed operator config is rejected

#### Scenario: Pipeline constructs a canonical backtest request
- **WHEN** `Pipeline.run()` builds `CanonicalBacktestInput`
- **THEN** the request's `adjust_mode` matches `PipelineConfig.adjust_mode`
- **AND** qlib runtime initialization records the same provider adjustment mode
- **AND** exchange and cost controls come from `PipelineConfig`
