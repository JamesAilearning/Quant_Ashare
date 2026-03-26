# Architecture Overview (V2 Bootstrap)

Updated: 2026-03-26

## Target Layers

- `app/` or `web/`: operator UI and workflow controls
- `src/core/`: training/backtest orchestration
- `src/data/`: data adapters and contracts
- `src/contracts/`: validation and status payload contracts
- `research/factor_lab/`: research-only factor discovery and evaluation, clearly separated from production and canonical runtime
- `tests/`: regression coverage for logic + governance boundaries

## Core Flow (Target)

1. Data ingestion and contract validation
2. Benchmark artifact contract validation and operator status emission
3. Taxonomy artifact contract validation and operator status emission
4. Universe artifact contract validation and operator status emission
5. Feature handler and dataset preparation
6. Model training and prediction
7. Canonical backtest and official metrics
8. Run artifact contract validation and reproducibility status emission
9. Operator status/workflow snapshot emission with informational-vs-governance boundaries
10. Optional experimental analytics with explicit labels

## V1 Lessons Applied

- Keep one canonical official metrics path.
- Treat risk constraints migration as decision-first work.
- Surface status and failure-path metadata explicitly in UI.
- Lock boundaries with regression tests.
