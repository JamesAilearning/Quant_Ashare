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
- [x] **PR-3a:** repoint `scripts/compare_walk_forward_runs.py` to emit the ruler's
      verdict (keep the per-fold table + aggregate deltas; add the pooled-IR / paired-CI /
      seam / contradiction / caveats block via `build_ruler_report`). `--prereg` is a
      recorded pass-through here; missing prereg / missing substrate / low overlap render
      an ACTIONABLE fail-loud note (the table still prints). Synthetic CLI tests added.
- [x] **PR-3b-i:** the walk-forward aggregate report records the CODE's `git_commit` +
      `git_dirty` (`capture_git_provenance` at the write boundary; `build_aggregate_report`
      takes injectable `git_provenance`, defaults to null for synthetic reports). Purely
      additive; the enabler for the topological ancestor check. Unit tests added.
- [x] **PR-3b-ii:** the git-provable pre-registration gate (`src/core/preregistration.py`,
      pure; `compare_runs` stays pure-stats): `--prereg-plan` points to a COMMITTED plan
      file (hypothesis / expected_direction / baseline / registered `treatments`); the
      plan's identity is its LAST-TOUCHED commit (post-hoc edits move it past the runs →
      caught); gate verifies that commit is a git ANCESTOR of each run's recorded
      `git_commit` (`merge-base --is-ancestor`, forgery-robust). REFUSED: uncommitted /
      locally-edited plan, run with null `git_commit` (pre-provenance or mixed-commit
      resume), dirty/unknown worktree, missing `--variant`. FLAGGED (not refused):
      unregistered variant (multiple-comparison), verdict opposite the registered
      direction. `--prereg <ref>` remains as RECORD-ONLY, loudly marked NOT git-verified.
      Tests use real throwaway git repos (init → commit plan → advance) — ancestry is
      exercised against git itself, no mocks.
- [x] **PR-3b-ii:** runbook `docs/run-comparison-runbook.md` — the ordered workflow
      (plan → commit → clean single-invocation runs → compare), the refusal matrix,
      **ST-off isolated labels** (PR#223 drift), winner re-verified **ST-on vs REGEN-2
      canonical**, strict **variable isolation**.

## Longer OOS (methodology, no code)

- [x] Runbook note ("power comes from the data side"): extend the WF span (more folds /
      longer test windows) is the lever; it does not change the ruler.

## Must-not-touch

- [x] Existing per-fold scalar metrics + `compute_aggregate` numbers stay UNCHANGED
      (PR-1 only ADDS the `daily_series` key; additive + reconciliation tests prove it).
- [x] No change to backtest / IC computation, benchmark basis, or the REGEN-2 anchor.
