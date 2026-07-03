# Configurable label horizon (the 阶段6 enabler)

## Why

The 降频 (n_drop) sweep falsified "cut cost → net positive": the canonical model's
**+2.73%/yr gross alpha collapses monotonically to −6.23%** as turnover drops,
because the alpha is intrinsically high-frequency — the T+1→T+2 label gives a
~2-day signal half-life, so holding longer just trades on a stale signal. The
strategic conclusion (recorded in the post-promo plan): to trade lower-frequency
you first need a model whose **alpha persists longer** — i.e. a longer label
horizon (阶段6) — and only then re-test 降频 on that model.

But the label horizon is not configurable today:

- The label is **hard-coded inside qlib's** `Alpha158.get_label_config()`
  (`Ref($close, -2)/Ref($close, -1) - 1`); our `_alpha158_factory`
  (`src/data/feature_dataset_builder.py`) never overrides it. (qlib's `Alpha158`
  DOES accept a `label=` kwarg — `kwargs.pop("label", self.get_label_config())` —
  so no subclass is needed, only threading.)
- The **feature-dataset cache identity** is the fixed string
  `"alpha158_default"`. If the label changed without the identity changing, the
  cache would silently serve 2-day-label datasets to a 5-day run — cross-label
  cache poisoning, the worst kind of silent-wrong.
- The **label-lookahead embargo** is the module constant
  `LABEL_LOOKAHEAD_DAYS = 2` (`src/data/_segment_embargo.py`), consumed by both
  the builder's embargo check and the walk-forward engine's fold gap. A 5-day
  label with a 2-day gap silently leaks label information across segment
  boundaries — inflated validation, look-ahead OOS.

Without this enabler, 阶段6 cannot run at all; with a naive hack (edit the
constant, edit the label) it would run WRONG in two silent ways (cache, embargo).
This is a behavior-adjacent contract change crossing runtime boundaries
(config → dataset builder → engines), hence spec-first.

## What changes

One new config knob, threaded end-to-end, with the default provably identical to
today:

1. **`label_horizon_days: int = 1`** on the dataset/pipeline/walk-forward
   configs — the HOLDING horizon in trading days (buy T+1 close, sell T+1+H
   close). The label expression becomes
   `Ref($close, -(H+1))/Ref($close, -1) - 1`; **H=1 reproduces today's
   expression character-for-character** (`Ref($close, -2)/Ref($close, -1) - 1`).
2. **Cache identity folds in the horizon**: H=1 keeps `"alpha158_default"`
   (existing caches stay valid); H≠1 gets a distinct identity
   (`alpha158_label{H}d`) so cross-label reuse is structurally impossible.
3. **Embargo follows the horizon**: lookahead = H+1 trading days replaces the
   fixed constant in the builder check and the engine fold gap. H=1 → 2, today's
   value. Too-close segments under a larger H fail loud.
4. **Resume fingerprint** picks up the new config field (a resumed run cannot
   silently mix horizons). Honest consequence, stated up front: adding the field
   changes the fingerprint of existing configs ONCE, invalidating pre-existing
   resume manifests (same one-time cost as the PR-1 schema fold-in; re-running is
   the correct behavior for a semantics-bearing config change).
5. **Horizon-sensitive consumers audited**: `SignalAnalyzer` IC periods (1d/5d)
   are MEASUREMENT horizons computed from realized prices, independent of the
   model's label — pinned by a test, not assumed. Both engines (Pipeline +
   WalkForwardEngine) get the same field semantics (two engines, one schema).

## Exhaustiveness (grep-proven consumer inventory)

Machine-verified sweep (`LABEL_LOOKAHEAD`, `Ref($close, -2)`, `T+2`, `shift(-2)`
over `src/ web/ scripts/`) — the complete set of horizon-sensitive points:

| # | Consumer | Treatment |
|---|----------|-----------|
| 1 | Label expression (`_alpha158_factory`) | the change itself |
| 2 | Feature-cache identity (`alpha158_default`) | extensible composition (below) |
| 3a | Builder segment-embargo check | shared horizon-driven helper |
| 3b | Walk-forward fold gap (`engine.py`) | same shared helper |
| 3c | **Operator-UI segment-gap guard** (`web/operator_ui/training_guards.py`) — the 4th consumer the naive plan missed | same shared helper; reads the horizon from the parsed config when present, else 1 (today's UI cannot set the field → behavior unchanged) |
| 4 | `SignalAnalyzer` IC | VERIFIED label-independent (fetches realized T+1→T+1+period returns; `entry_offset=1`); pinned by test. Comment/UI copy that says period-1 IC "matches the training label" is true only for H=1 — wording made horizon-neutral |
| 5 | UI layout suggester (`_config_run_helpers._six_increasing_indices`) | suggestion-only (validator is the enforcement point); H=1 comment added |
| 6 | `factor_mining` `_synthetic_panel` / miner / promote comments | D5-isolated synthetic mirror, not a runtime consumer of this config; untouched (D6-frozen workstream) |

## Review outcomes folded in (operator review, 2026-07-03)

- **Cache identity mechanism is extensible**: base identity + ordered
  non-default dimension tokens; this change registers only the horizon
  dimension, future dimensions (ST-mask / universe / feature-set) slot in
  without refactor.
- **Resume invalidation is fail-loud**: the fold manifest additionally records
  its horizon; a mismatch re-run NAMES the cause (changed horizon with both
  values, or "pre-upgrade manifest") — never a bare unexplained re-run.
- **Forward warning (recorded, not blocking)**: at H=5 the embargo widens 2→6
  trading days, which shifts walk-forward fold windows — before the 阶段6 GPU
  runs, confirm the fold structure stays intact (no fold squeezed out, no
  boundary-fold overflow of the fold-22 class). Recorded in tasks.md.
- **Dependency chain**: the 阶段6 experiment requires this enabler AND the
  comparison ruler; the ruler is already fully merged (#310–#316), so this
  enabler is the last prerequisite.

## What does NOT change

- **Default behavior is byte-identical**: H=1 label expression, cache identity,
  embargo gap — all unchanged; the REGEN-2 replay anchor stays green. This is
  the must-not-touch line for the whole change.
- No experiment configs, no prereg plan, no runs — the 阶段6 experiment itself
  (plan file through the #316 gate, ST-off presets, GPU runs) is operational
  work AFTER this enabler merges.
- No new statistics; the ruler (`src/core/comparison.py`) is untouched.

## Impact

- `src/data/feature_dataset_builder.py` (factory label kwarg + cache identity),
  `src/data/_segment_embargo.py` (horizon-parameterized helper; constant stays
  as the H=1 value), `src/core/walk_forward/engine.py` (gap from config),
  configs (`FeatureDatasetConfig` / `PipelineConfig` / `WalkForwardConfig`).
- Tests: synthetic/mock only (dev-batch red line) — expression construction,
  cache-identity separation, embargo refusal, fingerprint fold-in, default-path
  byte-identity regression.
- Risk: LOW when defaulted (identity-preserving); the dangerous paths (cache,
  embargo) are exactly the ones the spec pins with fail-loud requirements.

## PR plan

Single PR (`feat/label-horizon-config`): the knob + threading + guards + tests.
The 阶段6 experiment (plan file, presets, GPU runs, gated comparison) follows
separately once this merges.
