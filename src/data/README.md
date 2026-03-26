# Data Layer (Skeleton)

Purpose:
- Future data adapters and ingestion services.
- Future benchmark/universe/taxonomy artifact loaders.

Boundary:
- Data-contract validation must remain explicit and testable.
- No benchmark or universe selection/fallback semantics are implemented in this change.

Current state:
- Runtime benchmark selection remains an explicit placeholder in `benchmark_selection_placeholder.py`.
- Runtime universe selection remains an explicit placeholder in `universe_selection_placeholder.py`.
- Industry-aware runtime behavior remains an explicit placeholder in `industry_runtime_placeholder.py`.
- No live data pipeline logic or selection semantics are implemented in this change.
