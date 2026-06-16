# Regression baselines

Three regression artifacts live here; all are about the walk-forward headline.

## 1. Deterministic frozen-score replay — `test_walk_forward_replay_baseline` (PRIMARY anchor)

The primary walk-forward regression anchor. It replays the C1 round's **22
frozen per-fold prediction Series** (`regen_a/frozen_fold_scores.pkl.gz`, the
exact post-ensemble signals the backtest consumes) through the CURRENT canonical
`BacktestRunner` (T+1 execution, close-derived price limits, PIT ST exclusion)
and asserts the aggregate AND every per-fold IR reproduce
`walk_forward_baseline_metrics.json` within a **tight** tolerance
(`REPLAY_ABS_TOL = 1e-6`, held in the test source — a tampered fixture cannot
widen its own gate).

**No model is retrained and the bundle is only read** — model scores are frozen,
so the test isolates backtest-semantics drift. Reproduction is exact because the
scores are fixed and the backtest + aggregation are deterministic (bootstrap
seed 42).

Regenerate (after an intentional canonical-semantics change):

```
RUN_E2E=1 python scripts/regen/replay_frozen_baseline.py \
  --provider-uri D:/qlib_data/my_cn_data_pit \
  --namechange-path D:/qlib_data/tushare_raw/all_namechanges.parquet
```

Skipped unless `RUN_E2E=1` **and** the PIT bundle + namechange parquet are
present (it runs a real backtest — not CI-runnable). The frozen-scores fixture
IS committed (~3.1 MB) so the test is not skipped for fixture absence.

> We dropped the old single-`fold0` anchor: fold 0 is the worst, sign-flipping
> (+0.33 → −0.27), within-noise fold — a fragile single-fold gate. The 22-fold
> deterministic replay covers every fold instead. See `docs/baseline_20260616.md`.

## 2. Value + framing pin — `test_regen_baseline_value_pin` (CI-runnable)

Runs in the fast suite (reads the JSON only, no bundle). Pins that the committed
headline is the corrected value (clearly above the old 0.3672) and that the
mandated framing is committed with the number — corrected semantics, the
statistical caveat (within noise / metric correction), and the total-return
deferral. So the higher IR can never be read without its context.

## 3. Walk-forward retrain baseline — `test_walk_forward_aggregate_baseline` (FU-5)

Re-runs the FULL walk-forward (retrain, all folds, ensemble) and asserts headline
aggregates stay within **±5%** of `walk_forward_baseline_metrics.json`. Unlike
the deterministic replay (1), this one retrains, so its band stays loose to
absorb GPU/retrain noise (tightening is deferred to REGEN-2). RUN_E2E-gated.

## Why some fixtures are git-ignored, some committed

`walk_forward_baseline_metrics.json` (metrics + per_fold) and
`regen_a/frozen_fold_scores.pkl.gz` (frozen scores) ARE committed — they are the
deterministic anchor. They change only on a deliberate, signed-off re-baseline,
per the reference-data workflow ("I pull, you eyeball, you sign off, I commit").

## When to refresh

Refresh (regenerate via `scripts/regen/replay_frozen_baseline.py`, eyeball,
re-sign, commit in the SAME PR) whenever a merged PR intentionally changes the
canonical backtest semantics. Do NOT refresh because the baseline is "slightly
off" — that's the regression these tests exist to surface.

## Current baseline (committed) — REGEN-A corrected

Produced by **frozen-score replay** (NO retrain, NO bundle rebuild) of the C1
round through the corrected canonical semantics. See
`docs/baseline_20260616.md` for the full old→new analysis, per-fold distribution,
and framing.

**Semantics:** T+1 execution (PR-C) + close-derived price limits (PR-D) + PIT ST
exclusion (PR-F). **Benchmark:** price index `SH000300` — total-return
`SH000300TR` deferred to REGEN-2 (excess will revise down ~2-2.5pp).

**Frozen-score provenance:** C1 run 2026-06-01, `config_fingerprint 22e0682…`,
csi300 / Alpha158 / 24-3-3 / ensemble_window=3, bundle
`D:/qlib_data/my_cn_data_pit` (2018-01-02 → 2025-12-31), GPU-trained scores.

**Headline (22 valid folds; fold 22 excluded — 2025Q4 test ends on the bundle's
last calendar day, no T+1 bar):**

| metric | OLD (T+2, limit-permissive) | **NEW (corrected)** |
|---|---:|---:|
| `mean_information_ratio` | +0.3672 | **+0.4815** |
| `mean_ic_1d` | +0.0223 | **+0.0176** |
| `mean_ic_5d` | +0.0346 | **+0.0293** |
| `mean_annualized_return` | +3.47% | **+5.27%** |
| `worst_drawdown` | −12.05% | **−12.93%** |

> ⚠ **The IR rise is a METRIC correction, not a strategy improvement.** Old T+2
> underestimated the strategy (live `daily_recommend` has always filled T+1); the
> limit fix removes inflated limit-up fills; PR-C also re-aligned IC to the label
> horizon (so `mean_ic` moved too). The shift is **outlier-driven and within
> cross-fold noise**: mean-fold-IR SE ≈ 0.41, IR 90% bootstrap CI ≈ [−0.36,
> 1.24], 11/22 folds up / 11 down, dropping the top-3 positive folds reverses it.
> It does NOT predict better live performance.

**Known residual:** the close-derived limit over-blocks a handful of resumption
days (Ref($close,1)=NaN across a suspension gap) — max 4 per fold, 37 total over
22 folds; negligible, tracked as a PR-D backlog. See `docs/baseline_20260616.md`.
