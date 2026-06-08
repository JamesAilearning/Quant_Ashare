# Tasks: no-train-on-ui-inspection-bundle

## 1. Implementation
- [x] Replace the three UI training-invitation prompts (tushare.py header +
      post-submit info; _results_render.py results view) with an explicit
      do-not-train warning.
- [x] Add `_is_non_production_ui_bundle` + a fail-loud reject in
      `validate_pipeline_training_inputs` for
      `…/operator_ui/results/<job>/qlib_provider` paths.

## 2. Tests
- [x] `_is_non_production_ui_bundle` detection: UI results bundle → True;
      production bundle / bare qlib_provider / None → False.
- [x] Guard integration: a UI results bundle is rejected (even with valid
      dates); a production-style bundle at the same dates passes.
- [x] Governance: no UI page invites training on a results/.../qlib_provider
      bundle (do-not-train warning present; legacy invitation phrasings gone).

## 3. Verification
- [x] Full fast suite green (no regression); `ruff` + `mypy --strict` +
      `openspec validate --strict` clean.
