# REGEN-2 baseline shift — total-return benchmark (SH000300TR)

**Status:** ② — REGEN-2 analysis, signed off. **The canonical regression
baseline REMAINS the REGEN-A price-index baseline** (`fixtures/walk_forward_baseline_metrics.json`,
replay-anchored per the `v2-canonical-backtest-contract` OpenSpec spec). REGEN-2
is **promoted to canonical in ③**, which delivers the deterministic frozen-score
replay anchor for REGEN-2, flips the canonical benchmark default to SH000300TR,
runs the replay CI-real, and updates the contract. The REGEN-2 numbers below are
backed by the **non-canonical** fixture `tests/regression/fixtures/regen2_tr/walk_forward_baseline_metrics.json`
(not wired into the regression/governance tests until ③).
**Date:** 2026-06-20. Will supersede `docs/baseline_20260616.md` as the official
basis once ③ promotes it.

---

## TL;DR (honest headline)

Switching the excess-return benchmark from the **SH000300 price index** to the
official **SH000300TR total-return index** lowers the reported information ratio
(REGEN-A 0.48 → REGEN-2 0.16) and the mean excess return (5.27% → 2.55%). **This
is the benchmark becoming honest, not a performance regression and not a data
defect.** The total-return index adds back ~2.35%/yr of reinvested dividends to
the benchmark leg, so the strategy must clear a higher bar; a same-model
price-vs-TR comparison attributes essentially the entire change to that
dividend. **The honest read is "unproven," not "disproven."** The point estimate
stays **positive** — TR-excess IR +0.16, excess +2.55%/yr, and mean IC stable and
positive at 0.018 — consistent with a **small, possibly-real edge**; but the 95%
CI straddles zero, so that edge is **statistically unproven, not refuted**. We can
neither confirm nor reject a small positive excess (the sample lacks the power),
and we do not quote a precise REGEN-A→REGEN-2 delta. **Absolute return ~24.5%/yr
is essentially market beta; the honest alpha — excess over the dividend-inclusive
benchmark — is ~2.5%/yr.**

The historical price data is **byte-identical** between the old and new bundles
(216/216 sampled stocks, 2018–2025), so the live model / daily recommend bundle
is price-clean — there is **no adj_factor bug**.

---

## What changed (exactly three sanctioned variables)

REGEN-2 re-runs the walk-forward with the C1/REGEN-A config held **byte-identical**
(csi300 / Alpha158 / 24-3-3 windows, step 3, embargo 2 / LightGBM with all 11
hyperparameters / seed 42 / topk 50, n_drop 5 / ensemble_window 3 / T+1, close,
limit 0.095 / commission 5bps, slippage 5bps, stamp 10→5bps / GPU), changing only:

1. **Benchmark** SH000300 (price) → **SH000300TR** (official total-return, tushare
   `H00300.CSI`). The one config line.
2. **Bundle** — the corrected 2026-06-17 vintage (2018-01-02 → 2026-06-17) instead
   of the C1 "5-28 vintage" (ends 2025-12-31). Same historical prices (proven
   below); extends the calendar so the previously-truncated **fold 22 (2025Q4)
   now completes** → 23 valid folds instead of 22.
3. **Already-merged corrected semantics on `main`** (PRs #270–#275 survivorship/
   PIT/freshness; the T+1 / close-limit / ST-mask fixes are already in the
   REGEN-A lineage).

Config fingerprint changes **by design** (benchmark_code feeds it). Everything
else is held fixed and was verified knob-by-knob.

---

## The three columns (NOT directly comparable)

Three different lenses on the **same** strategy. They are **not** apples-to-apples
(different benchmark basis, fold count, and replay-vs-retrain), so do not read a
column-to-column subtraction as a performance change.

| | **Absolute return** (strategy, no benchmark) | **Excess vs price index** (historical, inflated) | **Excess vs total-return** (official) |
|---|---|---|---|
| Source | REGEN-2 retrain | REGEN-A replay (`baseline_20260616.md`) | REGEN-2 retrain |
| Benchmark | — (gross) | SH000300 (price) | SH000300TR (total-return) |
| Mean ann. return | **+24.5%** (≈ market beta; noisy) | +5.27% | **+2.55%** |
| Mean fold IR | n/a (no benchmark) | 0.482 | **0.162** |
| SE of mean IR | — | 0.414 | **0.424** (t ≈ 0.38) |
| Pooled IR | — | not recomputable¹ | **0.209** (pooled t ≈ 0.49) |
| 95% bootstrap CI | — | [−0.363, 1.243] | **[−0.689, 0.932]** |
| Folds | 23 | 22 | 23 |

¹ The committed REGEN-A `per_fold` stores fold IRs but not the daily excess
moments, so its return-pooled IR cannot be reconstructed without re-running the
replay. REGEN-2's pooled IR (all 1397 fold-days pooled into one excess series) is
**0.209 annualized**, slightly above the mean-fold IR (0.162) because pooling
weights by window length.

**Statistical reading (both columns):** the point estimates are **positive**
(TR-excess IR +0.16, excess +2.5%/yr; IC stable and positive at 0.018), but the
95% CIs — REGEN-A [−0.36, 1.24], REGEN-2 [−0.69, 0.93] — straddle zero (t < 1).
So the edge is **unproven, not disproven**: a small, possibly-real excess that the
sample lacks the power to confirm or reject — read it as "not yet established,"
**not** "shown to be absent." **The absolute return (~24.5%/yr) is overwhelmingly
market beta from being long csi300-style names; the honest *alpha* — the excess
column — is ~2.5%/yr**, and statistically inconclusive (neither established nor
refuted).

---

## The gate: explaining the excess-return drop (5.27% → 2.55%)

This drop had to be explained before signing ②, because the same bundle feeds
the live model and daily recommend — a *data* problem would be bigger than the
baseline. Three candidate causes; the verdict is **benchmark (legitimate), not a
bug**.

**Correction to the framing:** the headline `mean_annualized_return` is the mean
per-fold **excess-return-with-cost** (verified: fold-0 `metrics.annualized_return`
= `risk_analysis.excess_return_with_cost.annualized_return`), i.e. it is measured
*against the benchmark*. So the 5.27→2.55 drop already carries the benchmark
switch — it is not a benchmark-independent "absolute return halving."

### Decomposition (same-model price-vs-TR, measured on identical folds)

A price-benchmark twin run trains the **same models** (training is
benchmark-independent), so price-excess − TR-excess on the *same* REGEN-2 folds
isolates the pure dividend effect. Full 23-fold result (17-fold agreed):

- **Benchmark (dividend) effect:** REGEN-2 **price**-excess IR **0.415** →
  REGEN-2 **TR**-excess IR **0.162** = **−0.253 IR / −2.31 pp/fold** — exactly the
  ~2.35%/yr reinvested dividend the TR index adds. This is the **dominant** driver
  (~80% of the drop). (17-fold measured −0.305 / −2.37 pp — consistent.)
- **Retrain + fold effect:** REGEN-A price-IR **0.482** (replay, 22f) → REGEN-2
  price-IR **0.415** (fresh retrain, 23f) = **−0.066**, well **within the SE
  (~0.41)** → statistically noise. GPU LightGBM is not bit-reproducible
  run-to-run and the corrected universe/ST masks shuffle a few picks, but the
  mean barely moves.
- **fold-mix:** the late folds (esp. fold 21 = 2025Q3, IR −5.5) are bad in *both*
  REGEN-A and REGEN-2 and are not the cause.

### Price-data integrity (the bug check) — PASS

The user's hypothesis (adj_factor refresh corrected inflated historical prices)
was tested directly and is **false** — because the prices did not change at all:

- For every strategy-held stock checked, old-vs-new adjusted close is identical
  (ratio 1.00000, Δreturn 0.00 pp).
- Broad sample: **216/216** stocks (csi300 + random) have **byte-identical**
  adjusted close across all of 2018–2025-12-31, including the 2024–25 window where
  tushare adj_factor was re-fetched.

So the rebuild preserved historical prices exactly; it only extended the calendar
to 2026 and corrected universe/survivorship/ST *membership*. The adj_factor
fail-loud guard (PR #230) is preventive and did not retroactively alter the 5-28
bundle. **No price bug; the live-model / recommend bundle is price-clean.**

> **Recorded as KNOWN-CLEAN (bundle bug-gate):** old↔new adjusted-close
> byte-identity across **216/216** sampled stocks (csi300 + random), every value
> 2018-01-02 → 2025-12-31, including the 2024–25 re-fetched-adj_factor window. The
> 2026-06-17 rebuild is **price-preserving**. Filed so future bundle refreshes can
> diff against this and so the live model / daily recommend are known price-clean.

### Verdict

The excess-return reduction is a **legitimate benchmark correction** — the TR
index honestly subtracts reinvested dividends. It reinforces, rather than
undermines, "the baseline is now more honest." **No bug; proceed.**

### Signal is intact

Mean IC is essentially unchanged across the corrected retrain — REGEN-A
`mean_ic_1d` 0.0176 → REGEN-2 0.0181 — so the model's raw predictive power held;
the IR/return shifts are benchmark + fold-mix, not signal decay.

---

## Implications — and what stays deferred to ③

This document + the non-canonical `regen2_tr/walk_forward_baseline_metrics.json`
are the whole of ②. The canonical baseline is **untouched** here. Promotion is ③,
because the `v2-canonical-backtest-contract` OpenSpec spec requires the canonical
regression baseline to be **replay-anchored** (a deterministic frozen-score
replay reproduces it) — REGEN-2 is a fresh retrain and has no replay anchor yet.

Deferred to ③ (must land together, atomically):
- **Replay anchor for REGEN-2** — freeze the REGEN-2 per-fold scores + the
  deterministic replay test (tight in-source tolerance), so the promoted baseline
  satisfies the contract; then run it **CI-real** (committed price/score subset +
  provider, not RUN_E2E-gated) — the actual regression-debt close.
- **Promote the canonical fixture** `walk_forward_baseline_metrics.json` → REGEN-2,
  and migrate the governance value-pin + the replay test together.
- **Flip the canonical benchmark default** to SH000300TR (`config_walk.yaml`,
  `config.yaml`, `src/core/walk_forward/config.py`, the four `config/presets/*`)
  — the archived REGEN checklist requires this atomically with the re-baseline.
- **Update the contract** (`v2-canonical-backtest-contract` spec): "TR deferral"
  → "TR applied", and reflect the REGEN-2 replay anchor.

Already established here (no ③ dependency):
- **Bundle health:** historical prices byte-identical old↔new (216/216) → the
  daily recommend / live-model bundle is price-clean (KNOWN-CLEAN above).

## Out of scope (separate gate)

- **④** production-model promotion — a *separate* `config.yaml`-trained candidate
  (not the WF fold models), trainable to 2026-06 for live recency; candidate-vs-
  incumbent on a validation window + guards, decided on its own sign-off.

## Provenance

- Bundle: `D:/qlib_data/my_cn_data_pit` 2018-01-02 → 2026-06-17 (2050 days), TR
  series `SH000300TR` (tushare `H00300.CSI`, implied div yield ~2.35%/yr).
- namechange/ST parquet: current run#6 snapshot (2018+, 5360 rows; csi300 ST≈0,
  ~+0.002 IR impact).
- Train device: GPU (matches C1); seed 42. Folds: 23 valid (fold 22 = 2025Q4 now
  completes). Run wall-clock ~18 min.
- Same-model decomposition: price-benchmark twin (identical config except
  benchmark), confirmed at the full **23 folds** (benchmark effect −0.253 IR /
  −2.31 pp; 17-fold agreed at −0.305 / −2.37 pp). REGEN-2 price-excess IR 0.415,
  CI [−0.389, 1.137] — also straddles zero, consistent with an unproven edge.
