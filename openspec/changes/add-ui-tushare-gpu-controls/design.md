# Design: UI Tushare And GPU Controls

## Operator UI

`provider_uri` is rendered before the Streamlit form. Because it is outside the
form, changing it triggers a normal Streamlit rerun and updates the Run button
disabled state immediately.

The same Config & Run page gets a Tushare ingest section. The section exposes a
small set of publishing fields and a "Pull Tushare Data" button. It never asks
for a token; it only checks whether `TUSHARE_TOKEN` is present in the UI process
environment. Job execution still flows through `JobManager.start()` and
`job_runner.py`.

## Job Runner

`JobManager.start()` supports a third mode, `tushare_provider`. For this mode it
sets output paths under `output/operator_ui/results/<job_id>/`:

- `output_dir`
- `staging_dir`
- `manifest_path`
- `validation_path`
- `comparison_path`

`job_runner.py` maps the mode to `scripts/ingest_tushare_qlib_provider.py`.

## GPU Control

`compute_device` is a flat config field so existing YAML remains compatible.
The supported values are `cpu` and `gpu`.

- `cpu` is the default and preserves existing behavior.
- `gpu` is accepted only for `LGBModel` in this change.
- `ModelTrainer` passes `device_type="gpu"` to qlib's `LGBModel`.
- If the local LightGBM build lacks GPU support, the run fails loudly through
  the existing training path; it does not silently fall back to CPU.
