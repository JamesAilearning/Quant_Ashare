# Design: Normalize Model Training Diagnostics

## Boundary

The change is diagnostic-only. `ModelTrainer._fit_dispatch()` continues to
call qlib model wrappers with the existing configured controls. After fitting,
the trainer normalizes whatever eval history the wrapper or inner model exposes.

## Eval History Shape

The canonical in-process shape is:

```python
{"valid": {"rmse": [0.5, 0.4]}}
```

Nested mappings already in this shape are preserved. Flat XGB-style keys such
as `valid-rmse` are converted to the same nested shape.

## Best Iteration Indexing

LightGBM exposes `best_iteration` as a 1-based boosting round for this code
path, so final-loss lookup uses `best_iteration - 1`. XGB and CatBoost expose
zero-based best iteration indexes, so final-loss lookup uses `best_iteration`.
Out-of-range or missing indexes fall back to the last recorded metric value.

## Failure Handling

Eval-history extraction remains best-effort. Malformed or missing framework
diagnostics must not fail a completed training run; they produce empty
`train_metrics` or `None` final diagnostics, matching the existing boundary.
