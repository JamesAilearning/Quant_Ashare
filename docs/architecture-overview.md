# Architecture Overview (V2 Bootstrap)

Updated: 2026-03-23

## Target Layers

- `app/` or `web/`: operator UI and workflow controls
- `src/core/`: training/backtest orchestration
- `src/data/`: data adapters and contracts
- `src/contracts/`: validation and status payload contracts
- `research/factor_lab/`: research-only factor discovery and evaluation, clearly separated from production and canonical runtime
- `tests/`: regression coverage for logic + governance boundaries

## Core Flow (Target)

1. Data ingestion and contract validation
2. Feature handler and dataset preparation
3. Model training and prediction
4. Canonical backtest and official metrics
5. Optional experimental analytics with explicit labels

## V1 Lessons Applied

- Keep one canonical official metrics path.
- Treat risk constraints migration as decision-first work.
- Surface status and failure-path metadata explicitly in UI.
- Lock boundaries with regression tests.
