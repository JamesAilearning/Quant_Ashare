# Current State Summary

Date: 2026-04-26

## Status

- Repository initialized from scratch for V2.
- OpenSpec governance baseline established in `AGENTS.md`.
- Canonical qlib runtime initialization is wired through
  `src.core.qlib_runtime.init_qlib_canonical`.
- The canonical official backtest path is implemented through
  `qlib.backtest.backtest`, guarded by `CanonicalBacktestInput` and runtime
  adjustment-mode checks.
- Pipeline and WalkForward runtime orchestration exist on main.
- Benchmark, universe, taxonomy, and run-artifact contracts/loaders provide
  validation and operator-status inputs.
- Risk-constraint runtime behavior is still pending a dedicated
  decision-first runtime-boundary change before it can be treated as approved
  canonical behavior.

## Immediate Goals

Continue tightening the runtime in staged OpenSpec changes:

1. keep the canonical official metrics path singular and auditable
2. remove implicit fallback and stale governance artifacts
3. decide risk-constraint migration in its own runtime-boundary change
4. keep dependency and operator-facing entry points installable from declared
   project metadata
