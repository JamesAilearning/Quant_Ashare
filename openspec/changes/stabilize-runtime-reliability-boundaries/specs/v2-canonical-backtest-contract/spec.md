## MODIFIED Requirements

### Requirement: Canonical contract SHALL forbid implicit fallback semantics

The canonical contract SHALL require explicit behavior for missing dependencies
and unsupported output shapes, and SHALL NOT allow hidden fallback paths that
change official metric meaning without explicit labeling. Official backtest
return-series payloads SHALL remain structured mappings of date string to
numeric value; unknown series shapes SHALL raise a typed runtime error instead
of being wrapped as raw display text.

#### Scenario: return-series serialization fails
- **WHEN** the qlib report return, benchmark, or cost series cannot be
  iterated as date/value pairs or contains non-numeric values
- **THEN** `BacktestRunner.run()` raises `BacktestRunnerError`
- **AND** `CanonicalBacktestOutput.return_series` is not populated with a
  `{"raw": ...}` fallback envelope
