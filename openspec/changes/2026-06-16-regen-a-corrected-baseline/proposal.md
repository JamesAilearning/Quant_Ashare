# Proposal: regen-a-corrected-baseline

## Why

PR-C (#241, T+1 execution), PR-D (#242, close-derived price limits) and PR-F
(#251, mandatory PIT ST exclusion) corrected three backtest-semantics bugs. The
committed walk-forward regression baseline (`walk_forward_baseline_metrics.json`,
`mean_information_ratio = 0.3672`) predates all three: it was produced under
**T+2-stale execution** and a **dead float-mode price limit that let limit-up
phantom fills through**. It is therefore no longer the official metric — and a
retrain re-baseline would conflate the semantics fix with GPU/retrain noise and a
bundle refresh.

REGEN-A re-baselines by **replaying the C1 round's frozen per-fold prediction
Series** (the exact post-ensemble signals the backtest consumes) through the
CURRENT canonical `BacktestRunner`. **No model is retrained and the bundle is
only read** — the model scores are held fixed, so the move is attributable purely
to the corrected backtest semantics. The replay is deterministic (frozen scores +
fixed bundle + bootstrap seed 42), so the new baseline is reproducible to machine
precision.

## What Changes

- **`scripts/regen/replay_frozen_baseline.py`** — the single source of the
  replay: per fold it recomputes IC via `SignalAnalyzer` (unchanged by backtest
  semantics) and the backtest metrics via canonical `BacktestRunner`
  (T+1 / close-limit / ST-on), then aggregates with the project's own
  `compute_aggregate` (seed 42) into the committed JSON schema.
- **`tests/regression/fixtures/regen_a/frozen_fold_scores.pkl.gz`** — the 22
  frozen fold prediction Series (gzipped, ~3.1 MB), committed so the replay test
  has its fixture (provenance: C1 run 2026-06-01, fingerprint `22e0682…`).
- **`walk_forward_baseline_metrics.json`** — regenerated: `mean_information_ratio
  0.3672 → ~0.4815` (ST-on, T+1, real limits), IC unchanged (signal-derived),
  `mean_annualized_return` / `worst_drawdown` recomputed; provenance gains the
  corrected-semantics record, the **statistical caveat** (SE≈0.41 / within noise
  / metric-correction-not-improvement), the total-return-deferral note, and a
  `per_fold` block.
- **`tests/regression/test_walk_forward_replay_baseline.py`** (NEW) — the PRIMARY
  WF regression anchor: replays the frozen scores and asserts the aggregate AND
  every per-fold IR reproduce the committed baseline within a **tight** tolerance
  (`REPLAY_ABS_TOL = 1e-6`, in source not in the fixture). RUN_E2E + bundle gated.
- **`tests/governance/test_regen_baseline_value_pin.py`** (NEW, CI-runnable) —
  reads the JSON only: pins that the committed headline is the corrected value
  (clearly above the old 0.3672), and that the corrected-semantics + statistical
  caveat + TR-deferral + per_fold framing are committed with the number.
- **Drop the single-fold `fold0` anchor** (`test_fold0_baseline.py` removed):
  fold 0 is the worst, sign-flipping (+0.33→−0.27), within-noise fold — a fragile
  single-fold gate. The 22-fold deterministic replay is the anchor instead.
- **`docs/baseline_20260616.md`** + `tests/regression/fixtures/README.md` updated
  (old +0.301/+0.367 headline corrected, methodology debt recorded).

## Impact on recorded metrics

`mean_information_ratio` moves `0.3672 → ~0.4815`. **This is a METRIC correction,
not a strategy improvement.** The old T+2 underestimated the strategy (live
`daily_recommend` has always filled T+1); the limit fix removes inflated limit-up
fills. The shift is **outlier-driven and within cross-fold noise**: 11/22 folds
up, 11 down, median Δ≈−0.05, mean Δ ≈ 1 standard error (mean-fold-IR SE≈0.41);
dropping the top-3 positive folds reverses the shift. It does **not** predict
better live performance. Benchmark remains the **price index SH000300** — the
total-return SH000300TR switch is deferred to REGEN-2, where excess return will
revise **down ~2-2.5pp**.

## Non-Goals

- No retrain, no bundle rebuild, no total-return benchmark (all REGEN-2).
- No bit-exact per-commit (ΔC/ΔD) decomposition — PR-C bundles the microstructure
  mask re-keying with the lag remap, so the levers are not cleanly separable; the
  WF layer reports the two endpoints + directional notes only.
- The retrain-based `test_walk_forward_aggregate_baseline` keeps its ±5% band
  (retrain noise); only its baseline VALUE is updated. Tightening that tolerance
  is deferred to REGEN-2.
