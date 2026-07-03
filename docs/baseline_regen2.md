# REGEN-2 baseline shift — total-return benchmark (SH000300TR)

**Status:** ③/A — REGEN-2 **replay anchor landed**. The deterministic frozen-score
replay anchor `tests/regression/fixtures/regen2/walk_forward_baseline_metrics.json`
is generated + reproduced on the project's **CANONICAL dependency stack** (pyproject:
numpy<2, scipy<1.14, pandas<2.3 — the stack CI runs), CI-real (not RUN_E2E-gated).
A gen-env==canonical-pin assertion fails generation loud off-pin. **The numbers
below are the CANONICAL-STACK values.** The canonical-fixture SWAP
(`fixtures/walk_forward_baseline_metrics.json` REGEN-A → REGEN-2), the benchmark-
default flips, the governance value-pin migration, and the contract update are **PR-2**.
**Date:** 2026-06-24 (supersedes the 2026-06-20 off-pin ② figures).

> **★ Number change vs the 2026-06-20 ② doc (off-pin → canonical): mean fold IR
> 0.16 → 0.28, mean ann excess 2.55% → 3.40%. This rise is 100% a fold-0 ARTIFACT,
> NOT signal** — fold-0's single-fold IR swings −0.889 → +1.767 (swing ~2.66)
> because its DEGENERATE scores (~39 value-buckets / 261 ties over 300 stocks) select
> a different stock set under the canonical numpy sort tie-break. folds 1..22 are
> byte-identical (|Δ|<1e-9). See "fold-0 known limitation" below. The honest edge and
> the "unproven, not disproven" conclusion are **unchanged** — fold-0's swing inflated
> the mean AND the variance (SE 0.43, t 0.65, CI straddles zero), so the global
> picture does not move.

---

## TL;DR (honest headline)

Switching the excess-return benchmark from the **SH000300 price index** to the
official **SH000300TR total-return index** lowers the *honest* information ratio
(REGEN-A 0.48 → REGEN-2 honest-edge ~0.16–0.20). **This is the benchmark becoming
honest, not a performance regression and not a data defect.** The total-return
index adds back ~2.35%/yr of reinvested dividends to the benchmark leg, so the
strategy must clear a higher bar; a same-model price-vs-TR comparison attributes
essentially the entire change to that dividend. **The honest read is "unproven,"
not "disproven."**

The committed canonical-stack **aggregate mean fold IR is 0.28** (mean ann excess
**+3.40%/yr**), but read it with the fold-0 caveat: **the rise above the off-pin ②
figure (0.16) is 100% a fold-0 tie-break ARTIFACT, not signal.** fold-0 is a
DEGENERATE fold whose two metrics are **information_ratio = +1.767** and
**annualized_return = +0.1336** (two SEPARATE metrics — do not conflate; on the
off-pin stack they were IR −0.889 / ann −0.0616). Its IR alone swings 2.66 between
stacks because its ~39-bucket / 261-tie scores select a different stock set under
the canonical sort tie-break. Crucially that swing inflated the **variance** as well
as the mean (mean-fold SE ≈ 0.43, t ≈ 0.65, 95% CI [−0.59, 1.04] straddles zero),
so the conclusion is unchanged: a **small, possibly-real but statistically UNPROVEN,
not refuted** edge. Mean IC is stable and positive at **0.018**. We do not quote a
precise REGEN-A→REGEN-2 delta. **Absolute return ~24.5%/yr is essentially market
beta; the honest alpha — excess over the dividend-inclusive benchmark, fold-0's
artifact set aside — is ~2.5%/yr.**

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

The **Excess vs total-return** column is the committed **canonical-stack** anchor
(`fixtures/regen2/walk_forward_baseline_metrics.json`, numpy<2).

| | **Absolute return** (strategy, no benchmark) | **Excess vs price index** (historical, inflated) | **Excess vs total-return** (official, CANONICAL) |
|---|---|---|---|
| Source | REGEN-2 retrain | REGEN-A replay (`baseline_20260616.md`) | REGEN-2 replay anchor (canonical numpy<2) |
| Benchmark | — (gross) | SH000300 (price) | SH000300TR (total-return) |
| Mean ann. return | **+24.5%** (≈ market beta; noisy) | +5.27% | **+3.40%** ² |
| Mean fold IR | n/a (no benchmark) | 0.482 | **0.278** ² |
| SE of mean IR | — | 0.414 | **0.426** (t ≈ 0.65) |
| Pooled IR | — | not recomputable¹ | off-pin ② was 0.209; not recomputed on canonical³ |
| 95% bootstrap CI | — | [−0.363, 1.243] | **[−0.588, 1.045]** |
| Folds | 23 | 22 | 23 |

¹ The committed REGEN-A `per_fold` stores fold IRs but not the daily excess
moments, so its return-pooled IR cannot be reconstructed without re-running the replay.

² **fold-0 ARTIFACT.** Both the mean fold IR (0.278) and the mean ann excess (3.40%)
are inflated above the off-pin ② figures (0.162 / 2.55%) ENTIRELY by fold-0's
tie-break flip (IR −0.889 → +1.767; ann −0.0616 → +0.1336) on the canonical stack —
NOT signal (see "fold-0 known limitation"). folds 1..22 are byte-identical to the
off-pin run; the std of fold IRs is **2.0** (fold-0 dominates both mean and variance).

³ The pooled-IR row was an off-pin auxiliary computation over all fold-days; fold-0's
daily excess series changed on the canonical stack, so 0.209 is stale and not
re-derived here (the pooled estimate would shift with the same fold-0 artifact).

**Statistical reading:** the point estimate is **positive** (canonical mean fold IR
0.278, mean ann excess +3.40%/yr; IC stable and positive at 0.018), but the 95% CI
[−0.59, 1.04] straddles zero (t ≈ 0.65 < 1) — and the headline mean is propped up by
the lone fold-0 artifact, whose extreme swing is itself the reason that fold is **not
evidence**. So the edge is **unproven, not disproven**: a small, possibly-real excess
(honest estimate ~0.16–0.20 IR / ~2.5%/yr, fold-0's artifact set aside) that the
sample lacks the power to confirm or reject — "not yet established," **not** "shown to
be absent." **The absolute return (~24.5%/yr) is overwhelmingly market beta from
being long csi300-style names.**

---

## fold-0 known limitation — degenerate scores + numpy sort tie-break

**Recorded fail-loud, not silently accepted.** The deterministic replay anchor
reproduces to 1e-6 ONLY on the canonical dependency stack (numpy<2, scipy<1.14,
pandas<2.3). The reason is **fold-0 alone**:

- **fold-0's frozen predictions are DEGENERATE**: only **~39 unique values over 300
  stocks** (261 ties), on **56/59** days of the fold. Every OTHER fold (1..22) has
  **300 continuous unique scores** (no ties) and is numpy-version-insensitive.
- The strategy's **topk=50 cutoff lands inside a tie block** (e.g. day 0, ranks
  44–54 all = 0.00023117), so *which* tied names make the top-50 depends on **numpy's
  sort tie-break — which differs across numpy MAJORS**. Different names → different
  return → different excess. fold-0's two metrics differ by stack:

  | fold-0 metric | off-pin (numpy 2.4.4) | **canonical (numpy<2)** |
  |---|---|---|
  | `information_ratio` | −0.889 | **+1.767** |
  | `annualized_return` | −0.0616 | **+0.1336** |

  (IR and annualized_return are **two separate metrics** — historically conflated in
  discussion; pinned here to stop that.)

- **PRE-EXISTING across the replay lineage, NOT introduced by REGEN-2.** REGEN-A's
  fold-0 frozen scores are **byte-identically degenerate** (same ~39 uniques). REGEN-A
  is the current canonical baseline and already anchors on this fold; it never surfaced
  only because REGEN-A's replay test is RUN_E2E-gated (ran solely on the off-pin gen
  machine). REGEN-2's CI-real replay is the **first** time the anchor ran on the
  canonical numpy<2 stack, which exposed it.
- **NOT small-data underfit**: fold-0's training window is a full 2 years
  (2018-01-01 → 2019-12-27), same as the others. Suspected cause: 2020Q2 (COVID)
  test-window feature gaps / suspensions routing many stocks to one model leaf.

**Why this does not change the conclusion.** fold-0's IR swing (2.66) inflated the
aggregate mean (0.16 → 0.28) AND the variance (std of fold IRs ≈ 2.0; mean-fold
SE ≈ 0.43, t ≈ 0.65), so the 95% CI still straddles zero. The headline edge stays
**unproven, not disproven**; fold-0's extreme stack-sensitivity is precisely why that
single fold is **not** admissible evidence.

**Two layers, kept isolated (variable isolation):**
- **甲 selection determinism** — handled here: the anchor is pinned to the canonical
  stack and a gen-env==canonical assertion fails generation off-pin. A *cross-version*
  deterministic tie-break (a stable secondary sort key) is a backlog item (it changes
  the alpha, i.e. it would move other folds) — **not** part of this anchor.
- **乙 signal quality** — *why* fold-0's 2020Q2 predictions are degenerate (~39
  buckets) is filed to **PHASE-6** (and entails re-auditing REGEN-A, which carries the
  same diseased fold). Out of scope for the ③ anchor.

---

## The gate: explaining the excess-return drop (price → total-return)

> **Off-pin twin analysis.** The IR/excess figures in THIS section and the
> decomposition below are from the **off-pin ② run** (numpy 2.4.4) and are NOT
> re-derived on the canonical stack (that needs a fresh price-benchmark twin run —
> a phase-6/PR-2 task). The canonical TR-excess mean is **0.278 / 3.40%** (the gap
> to the off-pin 0.162 / 2.55% is the lone fold-0 tie-break artifact). What this
> section establishes — the **dividend effect** (price-excess − TR-excess) — is the
> benchmark-leg difference on identical holdings, so it is **fold-0-tie-break-
> insensitive**: the canonical price-excess would shift by the same fold-0 amount as
> the TR-excess, leaving the −0.253 IR / −2.31 pp dividend conclusion intact.

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

## Implications — ③ status (A landed; PR-2 promotes)

**Landed in ③/A** (this PR):
- **Replay anchor for REGEN-2** — `tests/regression/fixtures/regen2/walk_forward_baseline_metrics.json`,
  a deterministic frozen-score replay (tight in-source 1e-6 tolerance), run **CI-real**
  (committed mini-bundle tarball + provider, NOT RUN_E2E-gated). Generated + reproduced
  on the canonical numpy<2 stack, with a gen-env==canonical-pin assertion. This is the
  regression-debt close; it satisfies the `v2-canonical-backtest-contract` replay-anchor
  requirement.

**Still deferred to PR-2** (must land together, atomically):
- **Promote the canonical fixture** `fixtures/walk_forward_baseline_metrics.json`
  REGEN-A → REGEN-2, and migrate the governance value-pin (the band widens to
  ~0.20 < IR < 0.35 to bracket the canonical 0.278 and exclude REGEN-A 0.48 / old-T2
  0.37 / the off-pin 0.16) + split the REGEN-A replay test together.
- **Flip the canonical benchmark default** to SH000300TR (`config_walk.yaml`,
  `config.yaml`, `src/core/walk_forward/config.py`, the `config/presets/*`)
  — the archived REGEN checklist requires this atomically with the re-baseline.
- **Update the contract** (`v2-canonical-backtest-contract` spec): "TR deferral"
  → "TR applied", and reflect the REGEN-2 replay anchor.

Already established here (no further dependency):
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

---

## Re-sign channel (audit P2, operator decision 2 — 2026-07-03)

Any future regeneration of `walk_forward_baseline_metrics.json` goes ONLY
through `.github/workflows/regen-baseline.yml` (manual dispatch, runner
PINNED to ubuntu-22.04 — never `ubuntu-latest`; OS/BLAS drift is an off-pin
variable, the numpy 2.x lesson applies to runner images too).

**Acceptance rules — committed BEFORE the numbers are seen, enforced in-job
by `scripts/regen/diff_baselines.py`:**

- **R1** — folds without an attributable cause (for the PIT re-sign: no
  delisted instrument whose delist_date falls within the fold's IC
  forward-return reach) must be IDENTICAL, not "close".
- **R2** — backtest metrics (`annualized_return` / `max_drawdown` /
  `information_ratio`) must be identical on EVERY fold; the channel only
  re-signs IC-input changes. Any drift aborts.
- **R3** — a change that cannot be attributed aborts the re-sign.
  Investigate; never explain past it.

**Evidence, not trust:** the workflow emits `baseline_evidence.json`
(workflow run URL, baseline/registry sha256, pip-freeze hash, runner image).
A re-sign PR commits the new baseline + the diff table + the evidence sidecar
TOGETHER (artifacts expire after 90 days; committed evidence does not). The
regression test asserts sidecar-vs-file digest consistency whenever the
sidecar exists; presence is mandatory from the first re-sign onward. The
operator's merge of that PR is the signature — no auto-promotion.

**Registry fixture:** `regen2/delisted_registry_frozen_20260618.parquet` is a
FROZEN FULL byte-level snapshot of the production registry (sha256-pinned in
the replay test; three-way reconciled and operator-signed on 2026-07-03).
Updates go only through this channel, never in place.
