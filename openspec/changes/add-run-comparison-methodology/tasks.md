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

- [ ] `src/core/comparison.py`: read two runs' persisted daily series →
  - [ ] **Pooled IR** (true concatenation, per definition) per run + pooled difference.
  - [ ] **Paired daily-difference moving-block bootstrap** with ACF-calibrated,
        configurable block length (default derived from measured ACF decay; recorded
        in provenance). Annualized diff + 95% CI + SE.
  - [ ] **Date alignment:** intersection; report overlap fraction; **fail loud** below
        the configurable overlap floor (default 90%).
  - [ ] **Verdict** (fail-loud): CI includes 0 → "indistinguishable at this power";
        CI one-sided → "significantly better/worse". Never a point-estimate winner.
  - [ ] **Backtest-primary / IC-diagnostic:** compute both; resolve to backtest;
        **flag the contradiction explicitly** when backtest and IC disagree.
  - [ ] **CI caveat envelope:** every output carries the regime-heterogeneity /
        single-period limitation + the block-length provenance + the pre-registration
        reference + the date-overlap fraction.
  - [ ] **Pre-registration gate (git-provable):** the hypothesis is a COMMITTED artifact
        (committed before the runs exist); the output records its git commit HASH so
        "hypothesis preceded experiment" is provable from history, not human-trusted;
        flag when the compared variant set exceeds the pre-registered plan.
  - [ ] **Seam-bound guard (from PR-1):** report the fold-boundary seam's upper-bound
        impact on pooled IR (boundary days excluded vs included) — a check DISTINCT from
        PR-1's per-fold lossless reconciliation.
- [ ] Synthetic-only unit tests (no bundle): known-difference series recover the right
      verdict; overlapping-regime autocorrelation widens the block-bootstrap CI vs iid;
      date-mismatch below floor fails loud; contradiction flag fires on sign-disagreement.
- [ ] Replace / repoint `scripts/compare_walk_forward_runs.py` to emit the ruler's
      verdict (keep the human-readable per-fold table; add significance).
- [ ] Constraints honoured & documented in the runbook: **ST-off isolated labels**
      for label-horizon experiments (PR#223 drift), winner re-verified **ST-on vs the
      REGEN-2 canonical** baseline, strict **variable isolation** (only the target
      variable differs).

## Longer OOS (methodology, no code)

- [ ] Runbook note: extend the WF span (more folds / longer test windows) is the
      data-side lever for power; it does not change the ruler.

## Must-not-touch

- [x] Existing per-fold scalar metrics + `compute_aggregate` numbers stay UNCHANGED
      (PR-1 only ADDS the `daily_series` key; additive + reconciliation tests prove it).
- [x] No change to backtest / IC computation, benchmark basis, or the REGEN-2 anchor.
