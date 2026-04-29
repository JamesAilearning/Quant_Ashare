## MODIFIED Requirements

### Requirement: Canonical runtime orchestration SHALL preserve backtest execution semantics

Runtime orchestration components and shipped runtime CLIs SHALL preserve
caller-supplied canonical backtest controls when constructing canonical
runtime/backtest config instead of substituting hidden defaults. This includes
price adjustment mode, signal-to-execution lag, execution price kind, minimum
cost, and limit threshold.

#### Scenario: Walk-forward CLI initializes qlib runtime
- **WHEN** `scripts/run_walk_forward.py` loads a walk-forward YAML config
- **THEN** the returned `WalkForwardConfig.adjust_mode` is also used as
  `QlibRuntimeConfig.data_adjust_mode`
- **AND** qlib runtime initialization cannot silently use an adjustment mode
  different from the fold backtest requests
