# Layer Boundaries

This note documents layer responsibilities for the current V2 runtime.

## `web/`

- Operator workflow boundary.
- No strategy/backtest/training logic is implemented here in this change.
- Informational operator status must remain separate from governance meaning.

## `src/core/`

- Orchestration boundary for canonical runtime path.
- Official metrics governance: canonical path only.
- Contract definitions: `src/core/canonical_backtest_contract.py`.
- Official backtest runtime: `src/core/backtest_runner.py`, bound to
  `qlib.backtest.backtest`.
- Runtime execution semantics placeholder: `src/core/runtime_execution_placeholder.py`.
- Risk constraints are not approved canonical runtime behavior until a
  dedicated decision-first OpenSpec change decides their boundary.

## `src/data/`

- Future data adapters and artifact loading boundary.
- Runtime benchmark selection/fallback semantics are intentionally out of scope.
- Placeholder only: `src/data/benchmark_selection_placeholder.py`.
- Runtime universe selection/fallback semantics are intentionally out of scope.
- Placeholder only: `src/data/universe_selection_placeholder.py`.
- Runtime industry-aware behavior is intentionally out of scope.
- Placeholder only: `src/data/industry_runtime_placeholder.py`.

## `src/contracts/`

- Future validation and status-schema boundary.
- Contract behavior must remain explicit and testable.
- Canonical boundary assertions: `src/contracts/canonical_boundaries.py`.
- Benchmark contract foundation: `src/contracts/benchmark_data_contract.py`.
- Benchmark contract health is informational and not runtime selection semantics.
- Taxonomy contract foundation: `src/contracts/taxonomy_data_contract.py`.
- Taxonomy contract health is informational and not industry-aware runtime semantics.
- Universe contract foundation: `src/contracts/universe_data_contract.py`.
- Universe contract health is informational and not runtime selection semantics.
- Run artifact contract foundation: `src/contracts/run_artifact_contract.py`.
- Run artifact contract health is informational and not runtime execution semantics.
- Operator status/workflow foundation: `src/contracts/operator_status_workflow_contract.py`.
- Operator status categories are informational and do not redefine governance labels.

## `tests/`

- `tests/logic/`: future runtime logic tests.
- `tests/governance/`: future governance/contract boundary regressions.

## `research/factor_lab/`

- Research-only boundary.
- Non-production and non-canonical by default.
- Promotion into production requires explicit OpenSpec approval.
