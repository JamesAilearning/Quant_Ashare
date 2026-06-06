## Why

The LightGBM hyperparameter **defaults** baked into the three runtime
config dataclasses are the *pathological* set that trains LGBModel to
`best_iteration ≈ 1` on this project's A-share Alpha158 data
(diagnosed in C2-c, "best_iter=1"). The defaults are identical in
`ModelTrainConfig` (`src/core/model_trainer.py`), `PipelineConfig`
(`src/core/pipeline.py`), and `WalkForwardConfig`
(`src/core/walk_forward/config.py`):

```
learning_rate=0.0421, max_depth=8, num_leaves=210,
lambda_l1=0.0, lambda_l2=0.0, min_data_in_leaf=20,
feature_fraction=1.0, bagging_fraction=1.0, bagging_freq=0
```

The `learning_rate`/`max_depth`/`num_leaves` trio are qlib's published
Alpha158-LGB *benchmark* params; the six regularisation/sampling fields
are LightGBM's own neutral defaults. The combination — an aggressive
learning rate over very wide trees (`num_leaves=210`) with zero L1/L2
and no row/column subsampling — drives validation loss to its local
optimum on the first split, so early-stopping fires at round 1-6 and
the boosting budget is never used. (The current docstrings call the
whole set "LightGBM defaults", which is imprecise: only the six reg
fields are LightGBM defaults — LightGBM's own `lr`/`max_depth`/
`num_leaves` are `0.1`/`-1`/`31`.)

**Why it's a footgun:** a config that overrides only *some* model
params silently inherits the rest. Any under-specified config trains
over-expressive, unregularised trees with no warning. Confirmed
exposed today (each inherits ≥1 pathological field):

- `config/presets/default.yaml` (the operator-UI "canonical starting
  point"), `config/presets/production.yaml`,
  `config/presets/my_preset1.yaml` — each sets `learning_rate: 0.005`
  but inherits `num_leaves=210`, `max_depth=8`, and all six neutral
  reg fields.
- `config/presets/smoke.yaml` / `config_smoke.yaml` — short smoke
  runs, lower stakes, same inheritance.

The one fully-tuned, safe config is `config_walk.yaml` (and its
`config_walk_n*` / `config_walk_mined` descendants via `extends`),
which overrides all eleven model fields explicitly.

`config.yaml` (the default `python main.py` entrypoint) also carries
the pathological set — but it sets `lr`/`max_depth`/`num_leaves`
*explicitly*, so flipping dataclass defaults cannot reach it.
Correcting the explicitly-pathological shipped YAML configs
(`config.yaml`, `config_smoke.yaml`) is tracked **separately** (C2-c).
This change deliberately keeps its blast radius to Python defaults
only, so the two efforts never edit the same files.

## What Changes

- Change the LightGBM hyperparameter **defaults** in the three runtime
  config dataclasses from the pathological set to the tuned set already
  validated by `config_walk.yaml`:

  | field | old default | new default |
  |---|---|---|
  | `learning_rate` | 0.0421 | **0.005** |
  | `max_depth` | 8 | **6** |
  | `num_leaves` | 210 | **64** |
  | `lambda_l2` | 0.0 | **1.0** |
  | `min_data_in_leaf` | 20 | **50** |
  | `feature_fraction` | 1.0 | **0.8** |
  | `bagging_fraction` | 1.0 | **0.8** |
  | `bagging_freq` | 0 | **5** |

  Unchanged (already equal to `config_walk.yaml`'s resolved values):
  `lambda_l1=0.0`, `num_boost_round=1000`, `early_stopping_rounds=50`,
  `seed=42`, `compute_device="cpu"`.

- After the change, the **complete** set of resolved model-config
  defaults equals `config_walk.yaml`'s — i.e. "explicit = default".
  A config that overrides every model field resolves to the same
  values as one that overrides none. Any under-specified config (the
  presets above) now resolves to the tuned values instead of the
  pathological ones — this is the intended fix.

- Rewrite the now-inverted docstrings on all three dataclasses and
  their guard tests. The old rationale ("defaults mirror LightGBM so
  callers who don't set them get unchanged behaviour") becomes false;
  it is replaced with "defaults are the tuned set that trains past the
  `best_iteration ≈ 1` plateau."

- Update the three tests that assert the old defaults (their *intent*
  inverts, not just their numbers):
  - `tests/logic/test_pipeline.py::test_lgb_regularisation_defaults_match_lightgbm`
  - `tests/logic/test_model_trainer.py::test_defaults_match_lightgbm_defaults`
  - `tests/logic/test_model_config_projection.py::test_mapping_projection_fills_model_train_defaults`
    (the `bagging_freq == 0` assertion)

- Add a guard test asserting the three dataclasses' model-field
  defaults are mutually identical **and** equal `config_walk.yaml`'s
  resolved model config — so a future edit can't re-introduce drift
  between the three defaults, or between the defaults and the tuned
  reference.

**Explicitly NOT in scope:**

- No edits to any `config*.yaml` / preset YAML. The under-specified
  presets are fixed *via* the default change; the
  explicitly-pathological `config.yaml`/`config_smoke.yaml` belong to
  C2-c.
- No fail-loud "under-specified config" guard. Unlike the `adjust_mode`
  invariant (a clean, decidable binary: PIT handler ⟹ POST),
  `best_iteration ≈ 1` has no clean invariant — it is an emergent
  product of `lr × leaves × L2 × data`, so a config-time "these
  hyperparams look pathological" heuristic would be subjective,
  false-positive-prone, and hard to maintain. Once the defaults are
  corrected the root cause is gone and an under-specified config
  inherits *good* values, so such a guard would have nothing left to
  protect. (A clean runtime variant — warn when post-training
  `best_iteration ≤ 5` — would catch a best_iter=1 regression from
  *any* cause and is worth doing, but as a separate small change, not
  here.)

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `v2-model-training-config-consistency`: the shared model-training
  config contract gains a requirement that the **default**
  hyperparameters across all runtime projection paths are the tuned,
  non-pathological set, stay mutually consistent across the three
  dataclasses, and equal the `config_walk.yaml` reference. The
  projection boundary itself (`build_model_train_config`) is unchanged.

## Impact

- **Affected code**: `src/core/model_trainer.py`,
  `src/core/pipeline.py`, `src/core/walk_forward/config.py` — default
  values + docstrings only, no logic change.
- **Affected tests**: `tests/logic/test_pipeline.py`,
  `tests/logic/test_model_trainer.py`,
  `tests/logic/test_model_config_projection.py` (3 assertions
  inverted), plus one new defaults-consistency guard test. A stale
  docstring reference to "the default `num_leaves=210`" in the
  XGB/CatBoost `num_leaves`-bound tests (which pass `210` explicitly
  and still pass) is corrected to "an LGB-unsafe `num_leaves`".
- **Behaviour change for callers relying on defaults**: any code/config
  that builds `ModelTrainConfig`/`PipelineConfig`/`WalkForwardConfig`
  without fully overriding model params now trains with the tuned
  hyperparameters. This is the intended fix; the in-tree beneficiaries
  are the `default`/`production`/`my_preset1`/smoke presets.
- **Regression baselines — verified no drift** (the key risk, since
  the drift tests are `RUN_E2E`-gated and silent-skip in normal CI):
  - `test_walk_forward_aggregate_baseline` is the only baseline whose
    value depends on model params. It runs `config_walk.yaml` (or a
    committed copy via `walk_forward_baseline_config.yaml`).
    `config_walk.yaml` overrides all eight changed fields explicitly →
    the resolved `WalkForwardConfig` is identical before/after →
    no drift.
  - `test_fold0_baseline` replays a *frozen predictions pickle*
    through `BacktestRunner` and never trains a model → structurally
    immune to model-default changes (its backtest params come from the
    fixture's own `config` block).
  - No default-reliant config (`production`/`default`/`my_preset1`) is
    wired to any regression fixture, so none can silently drift.
  - The no-drift guarantee is *structural* and codified as a guard
    test (`test_config_walk_overrides_every_model_field_so_baseline_cannot_drift`):
    `config_walk.yaml` overrides every model field explicitly, so a
    default change cannot move its resolved values. A separate test
    (`test_default_values_equal_config_walk_values`) confirms the new
    defaults equal config_walk's values so under-specified presets
    inherit the tuned set — note this second test is NOT a no-drift
    proof (it is trivially true for any field config_walk leaves at its
    default). If a future edit makes config_walk rely on a changed
    default, the no-drift guard fails and the baseline must be
    regenerated per `tests/regression/fixtures/README.md` in the same
    PR; no stale baseline is left behind.
- **No change** to: the projection boundary, qlib init, backtest
  semantics, official metric definitions, the hyperparameter optimizer
  search space (it samples explicit params — unaffected), or any YAML
  schema / field name.
