# Tasks: Configurable label horizon (the 阶段6 enabler)

## OpenSpec (propose stage)

- [x] Draft `proposal.md` / `tasks.md` / spec delta
- [x] `openspec validate add-label-horizon-config --strict` green
- [x] Operator review — three design points confirmed (H semantics as-is;
      cache identity via EXTENSIBLE composition; resume invalidation fail-loud
      with named cause) + grep-proven exhaustiveness demanded and delivered
      (4th consumer found: operator-UI segment-gap guard)

## PR — feat/label-horizon-config (single PR, dev-batch safe: mock/synthetic only)

- [ ] `label_horizon_days: int = 1` on `FeatureDatasetConfig` + `PipelineConfig` +
      `WalkForwardConfig`; validation rejects non-positive/non-integer fail-loud.
- [ ] `_alpha158_factory` passes `label=[f"Ref($close, -{H+1})/Ref($close, -1) - 1"]`
      (H=1 char-identical to today's expression; unit test pins both H=1 and H=5).
- [ ] Cache separation via the EXTENSIBLE `compute_cache_key` payload (the file's
      own "future fields MUST be included here" contract — mechanism refined
      during implementation grounding: the callable identity is zero-arg and
      cannot see the config, whereas the key payload is exactly the extensible
      dimension-composition point the review asked for): horizon added as a
      payload key ONLY WHEN NON-DEFAULT — H=1 payload byte-identical to today
      (existing caches stay valid), H≠1 structurally distinct. Identity string
      (`alpha158_default`) untouched, single responsibility. Test pins
      separation AND default key stability.
- [ ] Embargo: ONE shared horizon-driven helper consumed by (a) the builder check,
      (b) the walk-forward fold gap, (c) the operator-UI segment-gap guard
      (`training_guards.py`; horizon from parsed config when present, else 1);
      `LABEL_LOOKAHEAD_DAYS` stays as the H=1 value; refusal message names the
      horizon and required gap; tests for H=1 (unchanged) and H=5 (refuses a
      2-day gap) on all three consumers.
- [ ] Resume fingerprint picks up the field (auto via config asdict — verify and pin
      with a test: H=1 vs H=5 fingerprints differ). FAIL-LOUD messaging: FoldManifest
      additionally records `label_horizon_days` (additive; legacy=None); a mismatch
      re-run names the changed horizon (both values) or the pre-upgrade manifest —
      test pins the message.
- [ ] SignalAnalyzer IC-period label-independence pinned by test; horizon-conditional
      wording fixed (analyzer comment + `_results_render` IC help text made
      horizon-neutral).
- [ ] UI layout suggester `_six_increasing_indices`: H=1 assumption commented
      (suggestion-only; the validator is the enforcement point).
- [ ] Default-path byte-identity regression: default-config label expression, cache
      identity, and gap all equal pre-change values; REGEN-2 anchor green in CI.

## After merge (operational, NOT this change)

- [ ] **H=5 fold-structure preflight (operator warning, recorded here per review):**
      the embargo widens 2→6 trading days, shifting fold windows — before the GPU
      runs, confirm the walk-forward fold structure stays intact (same fold count
      as intended, no fold squeezed out, no boundary-fold overflow of the fold-22
      class) on BOTH the H=1 baseline and the H=5 treatment configs.
- [ ] 阶段6 prereg plan file (through the #316 gate; operator reviews + commits)
- [ ] Experiment presets (2d + 5d, ST-off both sides per the runbook)
- [ ] GPU window: baseline + treatment runs (clean checkout, single invocation each)
- [ ] `compare_walk_forward_runs --prereg-plan ... --variant 5d`
      (dependency chain satisfied: the ruler #310–#316 is already merged; this
      enabler is the last prerequisite)

## Must-not-touch

- Default (H=1) behavior byte-identical; REGEN-2 replay anchor green.
- No change to the ruler / comparison statistics.
