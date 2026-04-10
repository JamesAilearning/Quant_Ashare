# Tasks

## 1. Aggregator implementation (`src/core/operator_status_aggregator.py`)

- [x] Create `OperatorStatusAggregator` with `aggregate(...)` classmethod.
- [x] Accept optional `BenchmarkContractStatus`, `UniverseContractStatus`,
      `TaxonomyContractStatus` inputs.
- [x] Accept optional pre-built `OperatorStatusEntry` list for non-data
      boundary types.
- [x] Map each contract status to an `OperatorStatusEntry` with
      `boundary_type=BOUNDARY_DATA_CONTRACT`.
- [x] Auto-generate `not_ready` placeholder entries for any required
      boundary type not represented.
- [x] Delegate to `OperatorStatusWorkflowContract.build_snapshot()`.
- [x] Return `OperatorWorkflowStatusSnapshot` directly.

## 2. Tests (`tests/logic/test_operator_status_aggregator.py`)

- [x] All data contracts ok, no runtime entries → overall not_ready
      (auto-placeholders).
- [x] All data contracts ok + runtime entries ok → overall ok.
- [x] One contract error → overall error.
- [x] One contract warning → overall warning (when others ok + runtime ok).
- [x] Empty aggregation (no contract statuses, no extra entries) still
      produces valid snapshot with 3 auto-placeholders.
- [x] Mixed: benchmark error + universe warning → overall error.
- [x] Verify component_id format includes contract name and entity name.
- [x] Verify aggregator does NOT assign `overall_status` directly.

## 3. Quality gates

- [x] `python -m unittest discover -s tests` passes with full suite green.
- [x] Test count increases from 216 baseline.

## 4. Governance

- [x] Promote spec delta into `openspec/specs/v2-operator-status-aggregator/spec.md`.
- [x] Archive change.
