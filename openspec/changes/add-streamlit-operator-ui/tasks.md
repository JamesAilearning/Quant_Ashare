# Tasks: Streamlit Operator UI Console

## Phase 1 — Foundation (no UI)

- [x] Create `openspec/changes/add-streamlit-operator-ui/` OpenSpec (5 files)
- [x] Add `streamlit>=1.36` to `pyproject.toml` optional dependencies `[project.optional-dependencies] ui = [...]`
- [x] Create `web/operator_ui/__init__.py`
- [x] Create `web/operator_ui/job_runner.py` — CLI launcher subprocess
- [x] Create `web/operator_ui/job_manager.py` — job lifecycle (start/stop/status/list)
- [x] Create `web/operator_ui/report_reader.py` — read JSON reports with path guard
- [x] Create `web/operator_ui/chart_reader.py` — discover PNG charts with path guard
- [x] Create `web/operator_ui/config_forms.py` — Streamlit form widgets + validation

## Phase 2 — UI pages

- [x] Create `scripts/run_ui.py` — CLI entry point (absolute paths)
- [x] Create `web/operator_ui/app.py` — Streamlit entry, st.navigation
- [x] Create `web/operator_ui/pages/config_run.py` — Config & Run page
- [x] Create `web/operator_ui/pages/results.py` — Results page
- [x] Create `web/operator_ui/pages/walk_forward.py` — Walk-Forward page
- [x] Create `web/operator_ui/pages/run_history.py` — Run History page

## Phase 3 — Tests

- [x] Create `tests/logic/test_operator_ui_job_manager.py` — monkeypatch Popen, assert shell=False, args shape, job.json written
- [x] Create `tests/logic/test_operator_ui_report_reader.py` — path guard, existing fields read, no recomputation
- [x] Create `tests/logic/test_operator_ui_config_validation.py` — empty provider_uri rejected, unknown keys hard-fail

## Phase 4 — Validation

- [x] Run `openspec validate add-streamlit-operator-ui --strict`
- [x] Run `openspec validate --all --strict`
- [x] Run `python -c "import web.operator_ui.job_manager; import web.operator_ui.job_runner; import web.operator_ui.report_reader; import web.operator_ui.chart_reader; import web.operator_ui.config_forms"`
- [x] Run `pytest tests/logic/test_operator_ui_job_manager.py tests/logic/test_operator_ui_report_reader.py tests/logic/test_operator_ui_config_validation.py -v` (14 passed, 0 failed)
- [x] Run `python -m ruff check src tests scripts web` (0 errors)
