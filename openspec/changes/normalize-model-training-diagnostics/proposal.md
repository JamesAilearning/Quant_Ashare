# Normalize Model Training Diagnostics

## Why

`ModelTrainer` returns best-effort training diagnostics through
`ModelTrainResult.train_metrics`, `best_iteration`, and `final_valid_loss`.
Review found that the diagnostics path is inconsistent across model families:
XGB may expose flat eval-history keys, CatBoost keeps eval history on the
inner model after fit, and zero-based best-iteration models need different
loss indexing than LightGBM.

These issues do not change model fitting itself, but they make reports and
sidecar metadata unreliable for non-LGB models.

## What Changes

- Normalize framework eval histories into one nested `{dataset: {metric:
  values}}` shape before returning `train_metrics`.
- Refresh XGB and CatBoost eval history from their fitted inner models when
  available.
- Select `final_valid_loss` with model-family-aware best-iteration indexing.
- Add targeted unit tests for flat XGB histories, CatBoost inner histories, and
  final-loss indexing.

## Non-Goals

- Do not change model training parameters, qlib initialization, prediction
  generation, backtest logic, or official metric calculation.
- Do not introduce GPU support for XGB or CatBoost.
- Do not require XGB/CatBoost to be installed for unit tests.
