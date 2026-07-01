# Tasks: A trustworthy run-comparison ruler (pooled + paired, daily-series-anchored)

## OpenSpec (propose stage)

- [x] Draft `proposal.md` / `tasks.md`
- [x] Draft `specs/v2-run-comparison-methodology/spec.md` delta
- [x] `openspec validate add-run-comparison-methodology --strict` green
- [x] Operator review of the proposal (esp. the five unbiasing definitions) — approved with
      the tightening now folded in (pooled study-protocol label, overlap % of shorter,
      indistinguishable→diagnostics, split reconcile/seam, git-provable pre-registration)

## PR-1 — Daily-series persistence (the foundation)

- [x] Persist per fold: the **daily excess-return series** (`return − bench − cost`) +
      components + the **daily IC series** (`SignalAnalyzer.ic_series`, already computed)
      as a purely-additive `daily_series` block in the fold report (`aggregate.py`).
- [x] **Reconciliation guard (a) — lossless, PER-FOLD:** qlib-gated test — the persisted
      daily excess run back through the canonical `risk_analysis` reproduces the fold's
      scalar IR / max-drawdown within 1e-6.
- [x] **Pure-additive / zero-side-effect test:** the top-level key set is EXACTLY the
      pre-existing schema + the one `daily_series` key; `signal_analysis` untouched.
- [x] NaN-safety: IC NaN-days dropped in-series; `_sanitize_for_json` + `allow_nan=False`
      round-trip proven; back-compat: older runs simply lack the block (additive).
- [ ] **Actionable fail-loud detector (DATA side here; message in PR-2):** a helper to
      detect a run/fold lacking the `daily_series` block, so PR-2's comparison tool can
      fail loud NAMING the run + how to backfill (existing runs / REGEN-2 hit this first).
- [→PR-2] **Seam-bound guard (b):** MOVED to PR-2 — it needs pooled IR (excluded-vs-
      included boundary days), which only exists once the ruler lands. Kept SEPARATE
      from (a), per the split.

## PR-2 — The comparison ruler (pure statistics), stacks on PR-1

- [x] `src/core/comparison.py`: read two runs' persisted daily series →
  - [x] **Pooled IR** (true concatenation, per definition) per run (net + gross).
  - [x] **Paired daily-difference moving-block bootstrap** with ACF-calibrated,
        configurable block length (default from measured ACF decay; recorded in the
        caveat provenance). Annualized diff + 95% CI + SE.
  - [x] **Date alignment:** intersection; overlap = intersection ÷ shorter series;
        **fail loud** below the configurable overlap floor (default 90%).
  - [x] **Verdict** (fail-loud three-state): CI includes 0 → "indistinguishable"; CI
        one-sided → "treatment_better/worse". Never a point-estimate winner.
  - [x] **Backtest-primary / IC-diagnostic:** both computed; verdict on net excess;
        **contradiction flagged** when the IC verdict disagrees in sign.
  - [x] **Indistinguishable → mandatory diagnostics** (gross-vs-net / IC / direction +
        the "'indistinguishable' ≠ 'equivalent'" note).
  - [x] **CI caveat envelope:** regime-heterogeneity caveat + block-length provenance +
        date-overlap fraction + pre-registration reference on every output.
  - [x] **Seam-bound guard (from PR-1):** pooled net IR boundary-days included vs
        excluded + the seam_impact delta.
  - [~] **Pre-registration:** a non-empty ref is REQUIRED (fail-closed) and recorded;
        the **git-ancestor verification + variant-set-exceeds-plan flag** is the CLI's
        job → PR-2 tail below.
- [x] Synthetic-only unit tests (9, no bundle): indistinguishable-on-noise,
      better-when-CI-excludes-0, block-bootstrap SE > iid under autocorrelation,
      overlap-below-floor / missing-prereg / missing-substrate fail loud, contradiction
      flag, seam bound reported.
- [ ] **PR-2 tail:** repoint `scripts/compare_walk_forward_runs.py` to emit the ruler's
      verdict (keep the per-fold table; add significance) + the git-provable
      pre-registration gate (commit-hash ancestor check + variant-set flag).
- [ ] **PR-2 tail:** runbook constraints — **ST-off isolated labels** (PR#223 drift),
      winner re-verified **ST-on vs REGEN-2 canonical**, strict **variable isolation**.

## Longer OOS (methodology, no code)

- [ ] Runbook note: extend the WF span (more folds / longer test windows) is the
      data-side lever for power; it does not change the ruler.

## Must-not-touch

- [x] Existing per-fold scalar metrics + `compute_aggregate` numbers stay UNCHANGED
      (PR-1 only ADDS the `daily_series` key; additive + reconciliation tests prove it).
- [x] No change to backtest / IC computation, benchmark basis, or the REGEN-2 anchor.
