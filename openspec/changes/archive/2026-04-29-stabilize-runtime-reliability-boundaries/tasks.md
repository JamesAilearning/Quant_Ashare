## 1. Walk-Forward CLI Reliability

- [x] 1.1 Update `scripts/run_walk_forward.py` so `QlibRuntimeConfig` receives
  `data_adjust_mode` derived from the constructed `WalkForwardConfig`.
- [x] 1.2 Add a CLI config-loader regression test for `adjust_mode` /
  `data_adjust_mode` propagation.

## 2. Runtime Failure Boundaries

- [x] 2.1 Replace `_series_to_dict()` raw fallback with typed
  `BacktestRunnerError` failures.
- [x] 2.2 Add return-series serialization regression tests proving no `raw`
  envelope is produced.
- [x] 2.3 Fix `_temporal_artifact_loader_base._read_csv()` to report
  `artifact_file` on OSError.
- [x] 2.4 Add loader coverage for the OSError branch.

## 3. Dependencies and Docs

- [x] 3.1 Declare runtime dependencies used by shipped runtime/CLI modules.
- [x] 3.2 Refresh current-state and canonical-backtest docs to match the
  merged runtime path.
- [x] 3.3 Reconcile archived OpenSpec task checklists that remain unchecked.
- [x] 3.4 Archive `harden-canonical-runtime-boundary` by folding its spec
  deltas into baseline specs.

## 4. Validation

- [x] 4.1 Run focused tests for walk-forward CLI, BacktestRunner series
  serialization, temporal artifact loaders, and governance task hygiene.
- [x] 4.2 Run broader `tests/governance tests/logic` if local environment
  permits.
- [x] 4.3 Attempt `openspec validate stabilize-runtime-reliability-boundaries
  --strict` if the CLI is available; document if unavailable. Attempted; CLI
  is not available in PATH.
- [x] 4.4 Scope-drift check: no risk-constraint behavior changes and no new
  official metric path.
