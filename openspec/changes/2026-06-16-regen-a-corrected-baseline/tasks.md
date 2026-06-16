# Tasks: regen-a-corrected-baseline

## 0. Diagnosis (read-only) — DONE
- [x] Located + verified the C1 frozen per-fold scores (post-ensemble Series,
      fingerprint 22e0682); confirmed bundle still 5-28 vintage (replay valid).
- [x] Old code reproduces 0.301 / fold0 / fold12 bit-exact (infra sound).
- [x] ① execution day: old `lag=1` = T+2, new `lag=1` = T+1 (controlled probe).
- [x] ② limit: real limit-up blocking correct (67-297/fold); resumption
      over-block tiny (max 4/fold, 37 total over 22 folds) → known residual.
- [x] ③ fold12 IR rise is real mean (std unchanged) — T+1 alpha recovery, not
      variance collapse.

## 1. Implementation
- [x] `scripts/regen/replay_frozen_baseline.py` — deterministic replay tool.
- [x] Commit `frozen_fold_scores.pkl.gz` (22 folds, ~3.1 MB).
- [x] Regenerate `walk_forward_baseline_metrics.json` (mean IR → ~0.4815, IC
      unchanged, corrected provenance + statistical caveat + per_fold).
- [x] `test_walk_forward_replay_baseline.py` — 22-fold deterministic anchor,
      tight tolerance in source, RUN_E2E + bundle + fixture gated.
- [x] `test_regen_baseline_value_pin.py` — CI-runnable value + framing pin.
- [x] Remove `test_fold0_baseline.py` (drop the fragile single-fold anchor).
- [x] Update `regression_baseline.py` docstring + `.gitignore` (fold0 refs).

## 2. Docs
- [x] `docs/baseline_20260616.md` (old→new two points, per-fold distribution +
      outlier/within-noise verdict, framing, known residuals, methodology debt).
- [x] `tests/regression/fixtures/README.md` (drop fold0, document replay anchor,
      correct stale +0.301 headline).

## 3. Verification
- [ ] Fast suite + ruff + mypy --strict green (value-pin runs in CI; replay test
      skips without bundle).
- [ ] Operator: `RUN_E2E=1 pytest tests/regression/test_walk_forward_replay_baseline.py`
      reproduces the committed baseline within 1e-6.

## STOP — user final sign-off
- New mean IR ~0.4815 + the within-noise framing reported; await sign-off before
  merge.

## REGEN-2 handoff
- Total-return SH000300TR ingest + flip → re-baseline (excess down ~2-2.5pp).
- Revisit the retrain test's ±5% tolerance (point estimate is noisy, SE≈0.41).
- Methodology debt: small WF mean-IR effects undetectable — affects Stage 6
  (label-span) + any "did this change move the baseline" comparison.
