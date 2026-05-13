# Design: Streamlit Operator UI Console

## Architecture

```
scripts/run_ui.py
  → streamlit run web/operator_ui/app.py
      → st.Page pages/config_run.py     (forms → config dict)
          → config_forms.py             (Streamlit widgets)
          → job_manager.py              (start/stop/status)
              → job_runner.py           (subprocess CLI launcher)
                  → main.py / scripts/run_walk_forward.py
      → st.Page pages/results.py        (read pipeline_report.json + charts)
          → report_reader.py + chart_reader.py
      → st.Page pages/walk_forward.py   (read walk_forward_report.json + fold reports)
      → st.Page pages/run_history.py    (read _index.jsonl + job.json)
```

## Key design decisions

1. **Subprocess isolation**: The UI never imports `Pipeline.run()` or `WalkForwardEngine.run()`. It writes a config YAML and launches the existing CLI entrypoint via `subprocess.Popen(shell=False)`.

2. **Job runner as separate process**: `job_runner.py` is a lightweight Python module launched by `JobManager.start()`. It runs the real CLI via `subprocess.run`, captures return codes, and writes job status to `job.json`. This decouples job lifecycle from Streamlit's rerun cycle.

3. **Read-only artifact consumption**: All Results/Walk-Forward/History pages only read existing JSON reports and PNG charts. No new metric computation happens in the UI layer.

4. **Path boundary enforcement**: `report_reader.py` and `chart_reader.py` use `Path.relative_to()` to reject any path outside `output/` or `output/operator_ui/`.

5. **Explicit provider_uri**: The Config & Run form marks `provider_uri` as mandatory. Empty value disables the Run button and shows a validation error.

## Module responsibilities

| Module | Responsibility |
|--------|---------------|
| `app.py` | Streamlit entry, page navigation via `st.navigation` |
| `job_manager.py` | Create job dir, write config, spawn `job_runner.py`, stop via `taskkill` |
| `job_runner.py` | Read config, write job.json states, `subprocess.run` CLI, capture output |
| `config_forms.py` | Streamlit form widgets → `dict`, provider_uri validation |
| `report_reader.py` | Read JSON report artifacts, path guard |
| `chart_reader.py` | Discover PNG chart artifacts, path guard |
| `pages/config_run.py` | Config form + Run button + job status display |
| `pages/results.py` | Pipeline / walk-forward results display |
| `pages/walk_forward.py` | Per-fold results table + charts |
| `pages/run_history.py` | Catalog + job history table + run selection |
