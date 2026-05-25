# Add UI Tushare And GPU Controls

## Why

The Streamlit operator UI currently keeps `provider_uri` inside a form while
using the same field to disable the Run button. Streamlit form values do not
rerender until submission, so a valid `provider_uri` can still leave the button
disabled.

Operators also need two common controls from the UI:

- launch the existing Tushare-to-qlib provider ingest without leaving the UI;
- explicitly request CPU or GPU training when supported.

## What Changes

- Move the training `provider_uri` input outside the form so the Run button
  updates immediately after the operator enters a path.
- Add a Tushare provider-ingest UI action that writes a config under the UI job
  directory and launches `scripts/ingest_tushare_qlib_provider.py` through the
  existing job runner boundary.
- Add `compute_device` to pipeline, walk-forward, and model-training configs.
  The default remains `cpu`. `gpu` is an explicit LightGBM request and is passed
  to qlib's `LGBModel` without silent CPU fallback.

## Non-Goals

- Do not store or prompt for the Tushare token in UI config; the existing
  `TUSHARE_TOKEN` environment variable remains the only approved secret path.
- Do not add a second metric or backtest path.
- Do not implement GPU support for XGBModel or CatBoostModel in this change.
