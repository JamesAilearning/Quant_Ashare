# Proposal: require-post-adjusted-for-mined-factor-wf

## Why

`scripts/run_walk_forward.py` initialises the canonical qlib runtime with
`data_adjust_mode = wf_config.adjust_mode`, and `WalkForwardConfig.adjust_mode`
defaults to **`pre_adjusted`**. But the `MinedFactor` feature handler resolves
its factors through `PITDataProvider`, which **pins the canonical runtime to
`post_adjusted`** (`src/pit/query.py`: "match what Phase B.2 wrote"). The
single-canonical-runtime guard then rejects the second, mismatched init and
**every fold dies with a cryptic `QlibRuntimeInitError`** before any result is
produced. The shipped `config_walk_mined.yaml` does not set `adjust_mode`, so
anyone following the documented MinedFactor walk-forward recipe hits this.

This was diagnosed in the C2-b dry-run (the first MinedFactor WF run failed
exactly this way and was worked around by setting `adjust_mode: post_adjusted`
in the throwaway OOS configs — see `docs/phase_c2b_dryrun_result.md` §3). This
PR fixes it properly.

The mismatch is not a style nit. The PIT bin bundle is **written
post-adjusted**, so mined factor values are *physically constructed on
post-adjusted prices*. Running the walk-forward in `pre_adjusted` would, if the
runtime guard were relaxed, **silently score post-built factors against
pre-adjusted prices — wrong factor values with no error**. So the correct
behaviour is fail-loud, not "make the handler follow the runtime".

## Goals

- **Fail-loud at config construction.** When `feature_handler` is a PIT handler
  (today: `MinedFactor`) and `adjust_mode != post_adjusted`, raise a typed,
  actionable `WalkForwardError` in `WalkForwardConfig.__post_init__` — before
  any qlib init / feature build / backtest — instead of the runtime
  `QlibRuntimeInitError`.
- **Fix the shipped config + all committed MinedFactor configs/fixtures.** A
  retroactive validation rule makes every existing POST-less MinedFactor
  `WalkForwardConfig` fail to construct; fix them so CI stays green.

## Non-Goals

- **NOT** "make the MinedFactor handler follow the runtime's adjust_mode."
  That converts a loud crash into silent wrong factor values (pre-adjusted
  prices × post-built factors) — strictly worse than the current bug.
- **NOT** changing `PITDataProvider`'s post_adjusted pin (it is correct — the
  bundle is post-adjusted).
- **NOT** a dynamic "provider declares its required adjust_mode" mechanism —
  a small `frozenset` of PIT handlers is the right size today.
- **NOT** touching non-PIT handlers (`Alpha158` may use any supported mode).

## What Changes

1. `src/core/walk_forward/config.py`:
   - Add `_PIT_FEATURE_HANDLERS = frozenset({"MinedFactor"})` and import
     `ADJUST_MODE_POST`.
   - In `__post_init__` (right after the existing `adjust_mode in
     SUPPORTED_ADJUST_MODES` check): if `feature_handler in
     _PIT_FEATURE_HANDLERS and adjust_mode != ADJUST_MODE_POST`, raise
     `WalkForwardError` naming the offending value and the required
     `adjust_mode: "post_adjusted"`.
2. `config_walk_mined.yaml`: add `adjust_mode: "post_adjusted"`.
3. Retroactive fixes to committed MinedFactor `WalkForwardConfig` sites so the
   new guard does not break CI:
   - `tests/logic/test_run_walk_forward_mined.py`: add `adjust_mode:
     "post_adjusted"` to every MinedFactor test YAML (incl. the error-path
     tests, whose `_load_config` now constructs `WalkForwardConfig` first).
   - `tests/logic/test_walk_forward_resume.py`: set `adjust_mode=
     "post_adjusted"` on both configs in `test_includes_feature_handler` so the
     MinedFactor one constructs and the test still isolates `feature_handler`.
   - (FeatureDatasetConfig sites — test_mined_factor_handler, the cache tests,
     test_segment_embargo — are a different dataclass and are unaffected.)
4. New tests (`tests/logic/test_walk_forward.py`): MinedFactor+pre rejected at
   construction with a clear message; MinedFactor+post accepted; Alpha158+pre
   unaffected; the shipped `config_walk_mined.yaml` constructs cleanly.

## Impact

- **Affected specs**: `v2-canonical-runtime-orchestration` (ADDED requirement).
- **Affected code**: `src/core/walk_forward/config.py`.
- **Affected configs/tests**: `config_walk_mined.yaml`,
  `test_run_walk_forward_mined.py`, `test_walk_forward_resume.py`,
  `test_walk_forward.py` (new cases).
- **Backward compatibility**: non-PIT (`Alpha158`) configs unchanged; the only
  configs that now fail construction are MinedFactor configs missing
  `post_adjusted` — which were already broken at runtime (cryptic
  `QlibRuntimeInitError`). This turns that runtime crash into a construction-time
  actionable error.
- **Risk**: low. One cross-field check behind a `frozenset`; no qlib import
  added to config.py; PITDataProvider untouched.
