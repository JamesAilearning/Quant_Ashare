# Isolate fold-0's per-runner-bimodal metrics in the REGEN-2 replay anchor

## Why

The `v2-canonical-backtest-contract` requires the committed walk-forward baseline to
reproduce "to machine precision ON THE CANONICAL DEPENDENCY STACK". That premise is
**falsified for fold-0**: its frozen scores are degenerate (~39 buckets / 261 ties), so
the top-k cutoff lands inside a tie block and the selected names depend on the sort
tie-break — which is **PER-RUNNER bimodal even on the canonical pin**, not merely
numpy-major-sensitive as the contract assumes.

Hard evidence: the divergent fold-0 values are **byte-identical across CI runs** (`IR
-0.0712889987158074`, `ann -0.004711347265649301`) on the pinned stack (numpy 1.26.4 /
scipy 1.13.1 / pandas 2.2.3). The selection is a **discrete A↔B flip**, fixed for a whole
CI run but varying between runs (a fresh GitHub runner can flip it). An earlier bounded
in-run retry (the wrong stopgap) could not help — all attempts share the runner — so the
REGEN-2 leg reds otherwise-unrelated PRs on a per-runner coin-flip.

## What Changes

- **Isolate fold-0** in `tests/regression/test_walk_forward_replay_baseline_regen2.py`:
  folds 1-22 (all metrics) and fold-0's ICs stay STRICT at 1e-6 on every runner (the real
  regression surface); fold-0's three topk-dependent backtest metrics (return / drawdown /
  IR) and the seven aggregate keys derived from the per-fold IR/ann set are asserted
  against `{committed A OR a recorded alternate B}` — a THIRD value is still a real
  regression and fails. The 1e-6 tolerance is NOT widened.
- **Remove the dead in-run retry** from `.github/workflows/test.yml` (the misdiagnosed
  stopgap) — a plain single run is correct again.
- **Modify** `v2-canonical-backtest-contract` to scope machine-precision reproduction to
  the strict surface and document fold-0's per-runner bimodality as a known-limitation.

## Impact

- The REGEN-2 anchor no longer reds CI on fold-0's per-runner flip; the real regression
  surface (folds 1-22 + aggregate, modulo fold-0's two known states) stays strict.
- The proper fix (a deterministic secondary sort key) changes the selection → needs a
  baseline regen → stays a phase-6 item. No baseline regen here.
