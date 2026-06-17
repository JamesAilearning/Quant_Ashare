# Tasks: retire-tushare-publisher

## 1. Pre-deletion check
- [x] Confirm no production / inference / WF / factor path imports the publisher
      (`provider_bundle`, `TushareQlibProvider*`, `ingest_tushare_qlib_provider`);
      only the ingest CLI + `config_forms` (type-only) + operator-UI job plumbing
      referenced it.

## 2. Delete
- [x] `src/data/tushare/provider_bundle/` (7 files), the ingest CLI, the UI
      Tushare page, `provider_catalog.py`, `config_tushare_qlib_provider.yaml`.

## 3. Rewire (drop the `tushare_provider` job mode)
- [x] `job_manager` (JobMode + output-path block), `job_runner` (dispatch),
      `job_io` (mode alias), `progress` (estimator + dispatch + orphan helpers),
      `pages/results.py` (filter + dispatch), `pages/_results_render.py`
      (`_render_tushare_provider`), `config_forms` (`TUSHARE_PROVIDER_KEYS`),
      `app.py` (nav + icon), `training_guards` (`_metadata_root`).
- [x] `config_run.py`: remove the saved-provider dropdown; keep manual
      `provider_uri` input.
- [x] Refresh stale doc references to the deleted publisher in
      `qlib_bin_builder.py` / `bundle_manifest.py` / `test_query_layer.py`.

## 4. Tests
- [x] Delete pure-publisher tests; remove `tushare_provider` test methods from
      mixed UI test files; re-home the production-config provider_uri guard from
      `test_tushare_provider_opt_in_boundary.py` to
      `test_production_config_provider_uri.py`.
- [x] Keep the U1 governance test (page gone → trivially passes; `_UI_FILES`
      updated).

## 5. Verification
- [x] Full fast suite green (2306 passed, 29 skipped); CI-scope `ruff` + CI
      `mypy --strict` (src/ scripts/ web/operator_ui/) + `openspec --strict`
      clean. (Pre-existing `web/app.py` UP031 is outside CI's `src/ tests/
      scripts/` ruff scope and unchanged by this PR.)
- [x] Residual grep clean; `py_compile` the rewired web modules.
