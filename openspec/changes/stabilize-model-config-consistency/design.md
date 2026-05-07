## Context

`PipelineConfig`, `WalkForwardConfig`, and `HyperparamOptimizer` all project
operator-facing model fields into `ModelTrainConfig`. The projection is
currently hand-written in multiple locations, while the hyperparameter
optimizer only covers a subset of fields supported by `ModelTrainConfig`.

The repository needs a safer way to add model knobs without requiring multiple
runtime paths to be updated in lockstep. Existing YAML files are flat, so this
change must avoid a disruptive config shape migration.

## Goals / Non-Goals

**Goals:**

- Create one shared model config projection helper for runtime flows.
- Preserve flat `PipelineConfig` and `WalkForwardConfig` fields.
- Extend Optuna search-space output to include existing LightGBM
  regularization and sampling fields.
- Add tests that catch projection drift.

**Non-Goals:**

- No nested YAML migration.
- No change to qlib initialization, feature datasets, backtest semantics, or
  official metric definitions.
- No new model family beyond existing `ModelTrainConfig` support.

## Decisions

1. **Use projection helpers before nested config dataclasses.**

   A shared helper can accept any object or mapping with model-like attributes
   and construct `ModelTrainConfig`. This removes duplication while preserving
   current config files. A nested dataclass migration can happen later if the UI
   or YAML schema is intentionally revised.

2. **Keep the search-space extension opt-in through defaults.**

   New `HyperparamSearchSpace` ranges will default to conservative LightGBM
   values already accepted by `ModelTrainConfig`. Optuna will sample those
   parameters, but callers that instantiate explicit params can still pass the
   same keys to the projection helper.

3. **Test the projection contract directly.**

   Unit tests should assert that pipeline, walk-forward, and optimizer params
   flow into the same `ModelTrainConfig` fields. This makes future added model
   fields fail close to the projection layer instead of diverging at runtime.

## Risks / Trade-offs

- **Risk: helper hides missing fields by falling back to defaults** ->
  Mitigation: tests cover non-default values for every projected field.
- **Risk: Optuna search behavior changes** -> Mitigation: only add parameters
  already supported by `ModelTrainConfig`; record them in trial params.
- **Risk: refactor drifts into unrelated runtime behavior** -> Mitigation: keep
  edits scoped to projection and hyperparameter search.

## Migration Plan

1. Add shared projection helper.
2. Replace duplicate `ModelTrainConfig` construction in pipeline,
   walk-forward, and optimizer evaluation.
3. Extend hyperparameter sampling and tests.
4. Roll back by restoring local construction if regressions appear.
