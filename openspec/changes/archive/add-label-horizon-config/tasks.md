# Tasks: Configurable label horizon (the 阶段6 enabler)

## OpenSpec (propose stage)

- [x] Draft `proposal.md` / `tasks.md` / spec delta
- [x] `openspec validate add-label-horizon-config --strict` green
- [x] Operator review — three design points confirmed (H semantics as-is;
      cache identity via EXTENSIBLE composition; resume invalidation fail-loud
      with named cause) + grep-proven exhaustiveness demanded and delivered
      (4th consumer found: operator-UI segment-gap guard)

## PR — feat/label-horizon-config (single PR, dev-batch safe: mock/synthetic only)
## — **MERGED #318**; items ticked retroactively 2026-07-03 (grep-verified in-tree)

- [x] `label_horizon_days: int = 1` on `FeatureDatasetConfig` + `PipelineConfig` +
      `WalkForwardConfig`; validation rejects non-positive/non-integer fail-loud.
- [x] `_alpha158_factory` passes `label=[f"Ref($close, -{H+1})/Ref($close, -1) - 1"]`
      (H=1 char-identical to today's expression; unit test pins both H=1 and H=5).
- [x] Cache separation via the EXTENSIBLE `compute_cache_key` payload (the file's
      own "future fields MUST be included here" contract — mechanism refined
      during implementation grounding: the callable identity is zero-arg and
      cannot see the config, whereas the key payload is exactly the extensible
      dimension-composition point the review asked for): horizon added as a
      payload key ONLY WHEN NON-DEFAULT — H=1 payload byte-identical to today
      (existing caches stay valid), H≠1 structurally distinct. Identity string
      (`alpha158_default`) untouched, single responsibility. Test pins
      separation AND default key stability.
- [x] Embargo: ONE shared horizon-driven helper consumed by (a) the builder check,
      (b) the walk-forward fold gap, (c) the operator-UI segment-gap guard
      (`training_guards.py`; horizon from parsed config when present, else 1);
      `LABEL_LOOKAHEAD_DAYS` stays as the H=1 value; refusal message names the
      horizon and required gap; tests for H=1 (unchanged) and H=5 (refuses a
      2-day gap) on all three consumers.
- [x] Resume fingerprint picks up the field (auto via config asdict — verify and pin
      with a test: H=1 vs H=5 fingerprints differ). FAIL-LOUD messaging: FoldManifest
      additionally records `label_horizon_days` (additive; legacy=None); a mismatch
      re-run names the changed horizon (both values) or the pre-upgrade manifest —
      test pins the message.
- [x] SignalAnalyzer IC-period label-independence pinned by test; horizon-conditional
      wording fixed (analyzer comment + `_results_render` IC help text made
      horizon-neutral).
- [x] UI layout suggester `_six_increasing_indices`: H=1 assumption commented
      (suggestion-only; the validator is the enforcement point).
- [x] Default-path byte-identity regression: default-config label expression, cache
      identity, and gap all equal pre-change values; REGEN-2 anchor green in CI.

## After merge (operational, NOT this change)

- [x] **H=5 fold-structure preflight (operator warning, recorded here per review):**
      DONE — `scripts/preflight_label_horizon.py`; evidence committed at
      `docs/prereg/label_horizon_preflight.md`: 23 folds BOTH sides, per-fold
      test windows identical, smoke preset = exactly 1 fold, paired
      shared-OOS total = 1397 trading days. (Prerequisite discovered en
      route: audit E1/PR-F had made ST-off walk-forwards impossible — added
      the explicit `st_mask_mode: off_experiment` opt-out: validated against
      contradictory namechange_path, report-stamped, tests pin both paths.)
- [x] 阶段6 prereg plan file — DRAFTED + committed
      (`docs/prereg/label_horizon.yaml`: variant set {5d}; 10d escalation/
      termination rules pre-written; sanity band baseline-vs-anchor with
      abort-before-run-2; fold-0 + 2020H2 sensitivity slices; ST-on
      re-verify = option (a) 4-run winning branch; decisions record).
      Operator review of the band numbers = registration sign-off; any later
      edit re-registers (by design of the ancestry gate).
- [x] Experiment presets (H=1 + H=5, ST-off both sides per the runbook):
      `config/presets/stage6_label_h{1,5}.yaml` + `stage6_smoke_h5_1fold.yaml`
      (extends-based — the pair's file diff IS the treatment variable).
- [x] **Step 3.5 gate rehearsal (added per operator review):**
      `scripts/rehearse_label_horizon_gate.py` — REAL compare-CLI subprocess
      over synthetic run dirs: accept (registered 5d) / flag (unregistered
      10d) / refuse (dirty provenance); evidence at
      `docs/prereg/label_horizon_rehearsal.md`.
- [x] 1-fold smoke ignition — DONE 2026-07-04 (49s wall-clock; caught the ST
      provenance-layout gate bug -> fixed in #324 before any decision-grade use).
- [x] Runs: baseline + treatment — DONE 2026-07-04 (23 folds each, ~16 min
      each, clean `fa85ddc`, one invocation each; first run-1 ignition
      breached band v1 -> aborted + re-registered as v2 (#325, convention
      error recorded) -> run-1 re-run passed v2 ALL-CHECKS -> run 2).
- [x] `compare_walk_forward_runs --prereg-plan --variant 5d` — DONE:
      gate PASSED; **VERDICT: INDISTINGUISHABLE** (net −0.71pp
      [−5.80, +4.44]pp); gross point NEGATIVE -> 10d escalation rule did not
      fire; verdict state stable on both pre-specified sensitivity slices.
      **Campaign concluded with a pre-registered NEGATIVE result — the label
      line closes; the ic_5d signal-level gain (+15%) did not convert under
      a horizon-blind daily rebalance (handoff to 阶段7 cadence).**
      Full adjudication: `docs/prereg/label_horizon_results.md`; verbatim
      verdict: `docs/prereg/label_horizon_verdict_20260704.txt`.

## Must-not-touch

- Default (H=1) behavior byte-identical; REGEN-2 replay anchor green.
- No change to the ruler / comparison statistics.
