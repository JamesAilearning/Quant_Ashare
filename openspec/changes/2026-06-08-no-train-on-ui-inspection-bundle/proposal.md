# Proposal: no-train-on-ui-inspection-bundle

## Why

The operator-UI Tushare page builds a one-off, **inspection-only** qlib bundle
under `output/operator_ui/results/<job>/qlib_provider` (the publisher path).
Three UI copy spots — the Tushare page header, its post-submit info, and the
results view — told the operator to paste that `qlib_provider` path into a
training / backtest `provider_uri`. Doing so silently trains on a
**non-production** bundle (no survivorship masking, ad-hoc adjust mode, no
pipeline provenance), diverging from the production bundle built by the
data-pipeline scripts (`scripts/data_pipeline/`).

This is the unification (publisher-retirement) prerequisite: before retiring the
publisher, training must be decoupled from its output so no operator is left
pointing a real run at an inspection bundle. (Unify U1.)

## What Changes

- `web/operator_ui/pages/tushare.py` + `web/operator_ui/pages/_results_render.py`:
  the three "fill the qlib_provider path into your training provider_uri" prompts
  are replaced with an explicit "inspection only — do NOT use as a training /
  backtest provider_uri; production bundles come from scripts/data_pipeline/"
  warning.
- `web/operator_ui/training_guards.py`: `validate_pipeline_training_inputs` now
  **fail-loud rejects** a `provider_uri` that points at a
  `…/operator_ui/results/<job>/qlib_provider` inspection bundle
  (`_is_non_production_ui_bundle`), with an actionable error. A production bundle
  (not under `operator_ui/results`) is unaffected.

## Non-Goals

- Does NOT retire the publisher / the UI Tushare ingest page (that is Unify U3).
- Does NOT remove the `_metadata_root` publisher-metadata-location helper or the
  publisher inspection view — those are deleted with the publisher in U3.
- No change to production bundle building or to a valid production training run.
