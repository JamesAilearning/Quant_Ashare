> Branch: `fix/lgb-default-hyperparams` (off `main`, isolated worktree).
> Hard rule: this change touches **only** the three `.py` dataclass
> defaults + tests + spec. It does **NOT** edit any `config*.yaml` /
> preset YAML — `config.yaml` / `config_smoke.yaml` belong to C2-c.

## 1. Defaults in the three dataclasses

- [x] 1.1 `src/core/model_trainer.py` `ModelTrainConfig`: set
  `learning_rate=0.005`, `max_depth=6`, `num_leaves=64`,
  `lambda_l2=1.0`, `min_data_in_leaf=50`, `feature_fraction=0.8`,
  `bagging_fraction=0.8`, `bagging_freq=5`. Leave `lambda_l1=0.0`,
  `num_boost_round=1000`, `early_stopping_rounds=50`, `seed=42`,
  `compute_device="cpu"` unchanged. Rewrite the class docstring to
  document the tuned defaults + the best_iteration plateau, dropping
  the inaccurate "defaults match LightGBM's own defaults" claim.
- [x] 1.2 `src/core/pipeline.py` `PipelineConfig`: same eight default
  values; rewrite the inline comment block accordingly.
- [x] 1.3 `src/core/walk_forward/config.py` `WalkForwardConfig`: same
  eight default values; rewrite the inline comment block accordingly.

## 2. "Explicit = default" alignment

- [x] 2.1 Confirm `config_walk.yaml` explicitly sets every model field
  (it does today: model_type, num_boost_round, early_stopping_rounds,
  learning_rate, max_depth, num_leaves, lambda_l1/l2, min_data_in_leaf,
  feature_fraction, bagging_fraction, bagging_freq). Record the
  field-by-field table in the PR body.
- [x] 2.2 Codify the alignment as tests (section 4). NOTE the division
  of labor: 4.3 (no-drift) protects the baseline; 4.2 (preset-fix) only
  confirms presets get the right values.

## 3. Tests / docstrings that invert

> All three assert the **new specific values** (`learning_rate=0.005`,
> `max_depth=6`, `num_leaves=64`, `lambda_l2=1.0`,
> `min_data_in_leaf=50`, `feature_fraction=0.8`, `bagging_fraction=0.8`,
> `bagging_freq=5`, `lambda_l1=0.0`), NOT merely "not the old values" —
> pin the good defaults so a future wrong edit is caught. Distinct from
> section 4: these pin default==literals; 4.2 pins default==config_walk
> projection. The checks do not overlap.

- [x] 3.1 `tests/logic/test_pipeline.py`: rename
  `test_lgb_regularisation_defaults_match_lightgbm` →
  `test_lgb_defaults_are_tuned_not_pathological`; assert the tuned
  literals; docstring explains the inversion.
- [x] 3.2 `tests/logic/test_model_trainer.py`: rename
  `test_defaults_match_lightgbm_defaults` →
  `test_defaults_are_tuned_not_pathological` (class
  `LGBRegularisationFieldsTests`); assert tuned literals; rewrite the
  class docstring. Fix the stale "default `num_leaves=210`" →
  "an LGB-unsafe `num_leaves=210`" in the XGB/CatBoost bound tests
  (they pass `210` explicitly and keep passing — only prose was stale).
- [x] 3.3 `tests/logic/test_model_config_projection.py`: update
  `test_mapping_projection_fills_model_train_defaults` — `bagging_freq`
  0→5 and pin the other changed defaults. Keep `lambda_l1=0.0`,
  `early_stopping_rounds=50`, `compute_device="cpu"`.

## 4. New structural guards (`tests/logic/test_lgb_default_hyperparams.py`)

> Division of labor — do not conflate (encoded in the test docstrings):
> 4.3 (NO-DRIFT) is what protects the baseline; 4.2 (PRESET-FIX) only
> confirms presets get the right values and is trivially true for any
> field config_walk leaves at its default.

- [x] 4.1 Assert the model-field defaults of `ModelTrainConfig`,
  `PipelineConfig`, and `WalkForwardConfig` are mutually identical (a
  future edit can't change one and forget the others).
- [x] 4.2 PRESET-FIX check: assert the all-default projected
  `ModelTrainConfig` equals the `config_walk.yaml`-resolved projection
  on the model-hyperparameter subset only — the 8 tuned fields +
  `model_type`, `num_boost_round`, `early_stopping_rounds`,
  `lambda_l1`. EXCLUDE `compute_device` / `seed` (config_walk doesn't
  override them; correct defaults `cpu`/`42`; forcing them in would
  false-fail and invite a wrong "default to gpu" fix). Confirms
  under-specified presets inherit the tuned values. NOT a no-drift
  proof (trivially true where config_walk relies on a default). Runs
  without qlib (reads the flat YAML).
- [x] 4.3 NO-DRIFT guard: assert `config_walk.yaml` overrides *every*
  model field explicitly. THIS is what makes the RUN_E2E walk-forward
  aggregate baseline immune to a default change (a hardcoded field
  ignores its default, so the resolved value can't move). If a future
  edit drops a key, this fails and the baseline must be regenerated per
  `tests/regression/fixtures/README.md` in the same PR. Do NOT weaken
  this on the assumption 4.2 covers it.

## 5. Blast-radius scan

- [x] 5.1 Grep the repo for default-reliant construction points
  (`ModelTrainConfig(`, `PipelineConfig(`, `WalkForwardConfig(` without
  model-field kwargs). Result: only the 3 inverted tests asserted the
  old defaults; the only `0.0421`/`210` references left are
  `config.yaml` (C2-c's), config_walk/docs prose (historical), and
  explicit `num_leaves=210` test data (intentional LGB-unsafe values).
- [x] 5.2 Confirm no `RUN_E2E`-gated baseline other than the
  `config_walk` aggregate is reachable by a model-default change.
  `test_fold0_baseline` replays a frozen predictions pickle (never
  trains a model) → immune. No preset config feeds any fixture.

## 6. Verification

- [x] 6.1 `pytest tests/logic tests/regression tests/governance`
  (default, E2E-skipped) → 2106 passed, 32 skipped, 0 failures.
- [x] 6.2 `ruff check` on the touched files → clean.
- [x] 6.3 `openspec validate fix-lgb-default-hyperparams --strict` →
  valid.
- [x] 6.4 Did NOT run `RUN_E2E` baseline tests (no config_walk drift
  surfaced; project policy — E2E is heavy + machine-freezing).

## 7. Spec

- [x] 7.1 Spec delta at
  `openspec/changes/fix-lgb-default-hyperparams/specs/v2-model-training-config-consistency/spec.md`
  (ADDED requirement).
- [x] 7.2 At archive time, fold the new requirement into
  `openspec/specs/v2-model-training-config-consistency/spec.md`.
