## Why

Recent review found several small but high-leverage reliability gaps in the
runtime and governance surface:

1. `scripts/run_walk_forward.py` still constructs `QlibRuntimeConfig` without
   the required provider adjustment metadata introduced by
   `harden-canonical-runtime-boundary`.
2. `BacktestRunner` still serializes return/benchmark/cost series with a
   catch-all `{"raw": str(series)}` fallback.
3. The shared temporal artifact loader masks non-`FileNotFoundError` IO errors
   with a `NameError`.
4. Runtime dependencies used by shipped entry points are not declared in
   `pyproject.toml`.
5. State and canonical-path documentation, archived task checklists, and one
   completed active change are out of sync with the merged runtime state.

These are not new trading semantics. They are reliability and governance
housekeeping items that make the already-approved runtime behavior fail loud,
install cleanly, and remain auditable.

## What Changes

- Thread walk-forward `adjust_mode` into `QlibRuntimeConfig.data_adjust_mode`
  in the CLI loader and add a regression test.
- Replace the return-series raw fallback with typed `BacktestRunnerError`
  failures and add regression tests.
- Fix the temporal artifact loader's OSError message to reference the real
  artifact file path and add coverage for the branch.
- Declare runtime dependencies used by the CLI, visualization, optimization,
  and walk-forward window generation.
- Refresh current-state and canonical-backtest docs to match mainline runtime.
- Reconcile archived OpenSpec task checklists that were archived while still
  unchecked.
- Archive the already-merged `harden-canonical-runtime-boundary` change by
  folding its spec deltas into baseline specs.

## Non-Goals

- Do not change `RiskConstraintEngine` behavior in this change. Risk constraints
  remain a separate runtime-boundary decision.
- Do not add a second official backtest path or metric helper.
- Do not change model training, feature generation, signal analysis, or
  portfolio construction semantics.
- Do not archive this new change in the same loop.

## Impact

- Expected code areas:
  - `scripts/run_walk_forward.py`
  - `src/core/backtest_runner.py`
  - `src/data/_temporal_artifact_loader_base.py`
  - `pyproject.toml`
  - focused tests under `tests/logic/`
  - docs and OpenSpec governance files
- The change should be validated with targeted tests plus broader
  `tests/governance tests/logic` where the local environment permits.
