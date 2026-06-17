# Proposal: retire-tushare-publisher

## Why

The repo had TWO qlib-bundle builders: the production PIT builder
(`src/data/pit/qlib_bin_builder.py`, driven by the `scripts/data_pipeline/`
chain) and a second Tushare "publisher" (`src/data/tushare/provider_bundle/`)
driven by an operator-UI "Tushare 数据" ingest page. A read-only assessment
(unify Step 0) established that the publisher is **non-production**: nothing in
production / inference / walk-forward / factor-mining reads its output; the
default Alpha158 handler references only `$vwap` (already a dead NaN feature on
the no-`vwap` production bundle) and never `$factor` / `$change`; and the
publisher's PRE adjust mode is unused by the feature pipeline. The publisher only
added an OOM-prone second code path, a two-builder divergence, and (until unify
U1) a footgun inviting training on its inspection bundle.

This change RETIRES the publisher and its UI ingest path. Production bundles are
built solely by the data-pipeline scripts (`qlib_bin_builder`). (Unify U3.)

## What Changes

- DELETE `src/data/tushare/provider_bundle/` (publisher, fetcher, comparison,
  config, types, utils), the ingest CLI `scripts/ingest_tushare_qlib_provider.py`,
  the UI ingest page `web/operator_ui/pages/tushare.py`, the saved-provider
  `web/operator_ui/provider_catalog.py`, and `config_tushare_qlib_provider.yaml`.
- REWIRE the operator-UI job plumbing to drop the `tushare_provider` job mode
  (`job_manager`, `job_runner`, `job_io`, `progress`, `pages/results.py`,
  `pages/_results_render.py`, `config_forms`, `app.py` nav) + delete the
  `training_guards._metadata_root` publisher-inspection helper. The
  `config_run.py` saved-provider dropdown is removed — operators type a
  production `provider_uri` (defaulted by `QUANT_PROVIDER_URI`, ops Phase 1).
- The unify-U1 non-production-bundle training refusal
  (`_is_non_production_ui_bundle` / `non_production_bundle_error`) is KEPT as a
  permanent belt-and-suspenders backstop.

## Non-Goals

- No change to the production builder (`qlib_bin_builder`) or the data-pipeline
  scripts. The deferred thin "validate + inspect the production bundle" UI view
  belongs to Phase 3 P3-6, not here.
- The `_feature_dataset_cache` reader for a legacy `tushare_provider_manifest.json`
  is left in place (best-effort cache tag, harmless for production bundles).
