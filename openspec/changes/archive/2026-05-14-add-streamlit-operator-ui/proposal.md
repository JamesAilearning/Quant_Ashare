# Proposal: Add Streamlit Operator UI Console

## Motivation

The qlib trading system currently has no interactive interface. Operators must edit YAML configs and run CLI commands (`python main.py` / `python scripts/run_walk_forward.py`) to train, backtest, and review results. This is adequate for batch experimentation but slow for iterative parameter tuning and result exploration.

## Scope

Add a Streamlit-based operator UI that:

- Generates config YAML from form inputs (does NOT directly call Pipeline.run)
- Launches pipeline / walk-forward runs via the existing CLI entrypoints as subprocesses
- Displays results by reading existing report and chart artifacts (does NOT recompute metrics)
- Browses historical run history from the catalog and UI job directories
- Enforces explicit provider_uri — no implicit machine-local fallback
- Is entirely `src/`-neutral: zero changes to canonical runtime, data layer, or contracts

## Non-goals

- No automatic factor mining runtime integration (placeholder only)
- No new official metric computation
- No new backtest path
- No React / FastAPI / database backend
- No direct `Pipeline.run()` or `WalkForwardEngine.run()` inside the UI process

## Impact

- New optional dependency: `streamlit>=1.36`
- New Python modules under `web/operator_ui/`
- New CLI entry point: `scripts/run_ui.py`
- New test files under `tests/logic/test_operator_ui_*.py`
- Zero changes to `src/`
