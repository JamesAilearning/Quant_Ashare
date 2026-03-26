# Contracts Layer (Skeleton)

Purpose:
- Future schema, validation, and status-contract definitions for runtime-facing artifacts.
- Canonical runtime boundary constants and checks live in `canonical_boundaries.py`.
- Benchmark data-contract foundation lives in `benchmark_data_contract.py`.
- Taxonomy data-contract foundation lives in `taxonomy_data_contract.py`.
- Universe data-contract foundation lives in `universe_data_contract.py`.
- Run artifact contract foundation lives in `run_artifact_contract.py`.
- Operator status/workflow foundation lives in `operator_status_workflow_contract.py`.

Boundary:
- Contract policy must be explicit and auditable.
- Contract status should not silently alter official-vs-experimental governance.

Current state:
- Foundation contract modules are present for canonical, benchmark, taxonomy, universe, run-artifact, and operator-status boundaries.
- Runtime behavior remains unimplemented in this layer; only contract validation surfaces are defined.
