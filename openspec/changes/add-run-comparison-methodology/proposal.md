# A trustworthy run-comparison ruler (pooled + paired, daily-series-anchored)

## Why

Phase-6 (label-horizon) and any future model/strategy experiment lives or dies on
one question: **is variant B better than baseline A, or is the difference noise?**
Today we cannot answer it honestly.

- `scripts/compare_walk_forward_runs.py` **only prints** per-fold deltas — no
  significance, so an operator reads a delta with no way to know if it is signal.
- `src/core/walk_forward/aggregate.py::compute_aggregate` bootstraps over the **K
  per-fold scalar metrics** (`_bootstrap_mean_ci` resamples the per-fold IR/IC
  scalars). That CI is dominated by **between-fold variance** — it IS the SE≈0.42
  noise floor. Almost every real difference falls inside it, so comparisons default
  to "can't tell", and the temptation is to eyeball point estimates and cherry-pick
  (exactly the trap the n_drop sweep hit: net-excess diffs were all "within noise",
  a naive rule picked n_drop=1, and only the gross-alpha decomposition revealed it
  was a −6.23% gross-alpha config).
- The enabling data is **computed but discarded**: the engine produces
  `backtest_output.return_series` (daily return/bench/cost) per fold and feeds it to
  attribution (`engine.py:860`) but never persists it. Pooled/paired statistics need
  those **daily series**; the fold artifact only stores scalars.

This change builds the ruler correctly ONCE, spec-first, because its correctness
determines the credibility of every phase-6 conclusion downstream. A ruler with a
systematic bias (a pooling seam, a silent date-subset, an unregistered multiple
comparison, an over-narrow CI) would let us "prove" a label horizon better when it
is not — the most expensive possible error.

## What changes

A new capability `v2-run-comparison-methodology` with two shippable layers plus a
methodology note:

1. **Daily-series persistence (the foundation).** Persist each fold's **daily
   excess-return series** (`return − bench − cost`) and **daily cross-sectional IC
   series** as a first-class run artifact, so any two already-completed runs can be
   compared **offline (CPU, no replay, no GPU)**.

2. **The comparison ruler (pure statistics).** `src/core/comparison.py` reads two
   runs' daily series and reports:
   - **Pooled IR** — the IR of the concatenated daily excess-return series over all
     OOS days (per run, and the pooled difference), which uses N days directly
     instead of averaging K noisy per-fold IRs.
   - **Paired daily-difference bootstrap** — `d_t = B_excess[t] − A_excess[t]` on
     the shared date set, resampled with a **moving-block bootstrap** (block length
     ACF-calibrated, see below) to honour autocorrelation, yielding an annualized
     difference + 95% CI.
   - **A fail-loud verdict** — CI includes 0 → "indistinguishable at this power";
     CI strictly one side → "B significantly better / worse". Never declares a
     winner on a point estimate alone.

3. **Longer OOS (methodology).** Extending the walk-forward span (more folds /
   longer test windows) is a data/runbook choice, recorded but not code.

### Key definitions — the ones that keep the ruler unbiased

These are load-bearing; getting them wrong gives a systematically wrong ruler.

- **Pooled semantics / the seam.** Pooled IR is the IR of the **true concatenation**
  of the per-fold daily excess-return series — deliberately NOT per-fold
  re-standardization, because the realized walk-forward strategy DOES switch models
  at fold boundaries in production, so the switch is real, not an artifact to
  normalize away. The only genuine seam is each fold-backtest starting from cash
  (a bounded ~1 boundary-day-per-fold turnover/return effect). We accept true
  concatenation as the definition AND bound the seam with the reconciliation guard
  below (a material seam distortion makes pooled-vs-per-fold reconciliation fail →
  it cannot hide).

- **Paired date alignment.** Paired statistics require a shared date set; different
  label horizons naturally yield different available dates (a longer horizon loses a
  few tail days). The ruler takes the **date intersection**, REQUIRES the overlap
  fraction be reported, and **fails loud** (refuses a verdict) when overlap drops
  below a configurable floor (default 90% of the shorter series) — so a comparison
  is never silently made on a biased date subset.

- **Multiple comparison = pre-registration as a PROCESS REQUIREMENT.** Each
  comparison experiment MUST carry a **pre-registered hypothesis** (the single
  planned comparison and its direction), committed BEFORE the runs, and the ruler
  records its reference in the output and flags any comparison whose variant set
  exceeds the pre-registered plan. Pre-registration is primary (harder than a
  post-hoc correction, and it cannot be quietly changed); a Bonferroni/FDR
  correction is only a backstop — and is explicitly noted to be near-useless under
  SE≈0.42 (it widens the CI past detectability, so the discipline must be
  design-time, not test-time).

- **CI is honest about its limits.** The pooled/block-bootstrap CI narrows SE by
  assuming approximate stationarity and handling autocorrelation — but it does NOT
  model **regime heterogeneity** (a COVID-2020 fold vs a calm fold carry structural
  uncertainty the bootstrap cannot resample away). Every comparison output MUST
  carry this caveat, machine-pinned, so a narrow CI never manufactures new false
  confidence.

- **Backtest primary, IC diagnostic, contradiction flagged.** The realized
  **backtest excess** is the primary arbiter; daily IC is a diagnostic. When they
  disagree in sign or verdict, the ruler resolves to the backtest AND **explicitly
  flags the contradiction** in the output (institutionalizing the n_drop
  gross/net-decomposition lesson and preventing the "IC positive but backtest
  negative" misread that made the incumbent look better than it traded).

- **Block length is ACF-calibrated, not a holding-period proxy.** The moving-block
  bootstrap block length defaults to the measured autocorrelation-decay length of
  the excess-difference series (≈10 days is a starting point, not a constant); it is
  configurable, and the chosen value + how it was derived are recorded in the result
  provenance.

### Guards (machine-enforced, in the spirit of the REGEN-2 value-pin)

- **PR-1 reconciliation:** aggregating the newly-persisted daily excess series back
  to a per-fold IR / max-drawdown MUST equal the existing per-fold scalar metrics
  within a tight tolerance — a machine proof that the new foundation shares the
  existing scalar convention (the ruler's root cannot be crooked). This same guard
  bounds the pooling seam.
- **Framing pins:** the comparison output's CI caveat, the pre-registration
  reference, and the date-overlap fraction are pinned by a CI-runnable test, so a
  verdict cannot be emitted stripped of its honesty envelope.

## Non-goals

- No change to how backtests / IC are computed — only what is persisted and how two
  runs are compared. Existing scalar metrics are unchanged.
- No auto-decision: the ruler emits a verdict + caveats; choosing a label horizon
  (or promoting anything) stays a human step.
- No new heavy compute: the ruler is offline CPU over persisted series (no replay,
  no GPU). Producing the runs to compare is the operator's separate GPU step.

## Honest framing (the ruler's own limits)

This ruler shrinks the *sampling* SE (pooled + paired + block bootstrap); it does
NOT create statistical power the data lack. Under a single-year, few-regime OOS, a
genuinely small edge may remain "indistinguishable" — and that is the correct,
honest answer, not a failure of the tool. The ruler's job is to stop us mistaking
noise for signal (and vice-versa), not to manufacture significance. Regime
heterogeneity and single-period structural uncertainty are surfaced as caveats, not
resolved.
