# Core Layer

Purpose:
- Orchestration entrypoints for data -> feature -> train -> predict ->
  canonical backtest flow.
- Canonical contract definitions live in `canonical_backtest_contract.py`.
- The official backtest runtime is implemented by `backtest_runner.py` and
  remains bound to `qlib.backtest.backtest`.

Boundary:
- Official metrics must be sourced only from the canonical qlib-native path.
- No experimental logic should be silently promoted into official outputs.
- Avoid implicit fallback or hidden coupling between modules.

Current state:
- Pipeline, walk-forward, training, signal analysis, attribution, visualization,
  and canonical backtest runtime components exist.
- Risk constraints require a dedicated decision-first runtime-boundary change
  before they can be treated as approved canonical behavior.
- Runtime execution placeholder remains as a boundary reminder for behavior
  that has not been explicitly approved.
