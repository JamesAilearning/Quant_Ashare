# Core Layer (Skeleton)

Purpose:
- Future orchestration entrypoints for data -> feature -> train -> predict -> canonical backtest flow.
- Canonical contract placeholder is defined in `canonical_backtest_contract.py`.
- Runtime execution placeholder is defined in `runtime_execution_placeholder.py`.

Boundary:
- Official metrics must be sourced only from the canonical qlib-native path.
- No experimental logic should be silently promoted into official outputs.
- Avoid implicit fallback or hidden coupling between modules.

Current state:
- Skeleton only. No training/backtest runtime implementation in this change.
- Canonical contract validation is defined; execution remains intentionally unimplemented.
- Runtime execution placeholder remains intentionally unimplemented.
