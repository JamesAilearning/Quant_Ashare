# Tasks: Streamlit Operator UI Console

## Phase 1 — Foundation (no UI)

- [ ] Create `openspec/changes/add-streamlit-operator-ui/` OpenSpec (5 files)
- [ ] Add `streamlit>=1.36` to `pyproject.toml` optional dependencies `[project.optional-dependencies] ui = [...]`
- [ ] Create `web/operator_ui/__init__.py`
- [ ] Create `web/operator_ui/job_runner.py` — CLI launcher subprocess
- [ ] Create `web/operator_ui/job_manager.py` — job lifecycle (start/stop/status/list)
- [ ] Create `web/operator_ui/report_reader.py` — read JSON reports with path guard
- [ ] Create `web/operator_ui/chart_reader.py` — discover PNG charts with path guard
- [ ] Create `web/operator_ui/config_forms.py` — Streamlit form widgets + validation

## Phase 2 — UI pages

- [ ] Create `scripts/run_ui.py` — CLI entry point (absolute paths)
- [ ] Create `web/operator_ui/app.py` — Streamlit entry, st.navigation
- [ ] Create `web/operator_ui/pages/config_run.py` — Config & Run page
- [ ] Create `web/operator_ui/pages/results.py` — Results page
- [ ] Create `web/operator_ui/pages/walk_forward.py` — Walk-Forward page
- [ ] Create `web/operator_ui/pages/run_history.py` — Run History page

## Phase 3 — Tests

- [ ] Create `tests/logic/test_operator_ui_job_manager.py` — monkeypatch Popen, assert shell=False, args shape, job.json written
- [ ] Create `tests/logic/test_operator_ui_report_reader.py` — path guard, existing fields read, no recomputation
- [ ] Create `tests/logic/test_operator_ui_config_validation.py` — empty provider_uri rejected, unknown keys hard-fail

## Phase 4 — Validation

- [ ] Run `openspec validate add-streamlit-operator-ui --strict`
- [ ] Run `openspec validate --all --strict`
- [ ] Run `python -c "import web.operator_ui.job_manager; import web.operator_ui.job_runner; import web.operator_ui.report_reader; import web.operator_ui.chart_reader; import web.operator_ui.config_forms"`
- [ ] Run `pytest tests/logic/test_operator_ui_*.py -v`
- [ ] Run `pytest tests/logic/ tests/governance/ tests/regression/ -q`
