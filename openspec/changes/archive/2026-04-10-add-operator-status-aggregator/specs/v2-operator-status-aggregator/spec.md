## ADDED Requirements

### Requirement: V2 SHALL provide an operator status aggregator that bridges data contract statuses to the workflow snapshot

The system SHALL provide an `OperatorStatusAggregator` that accepts
individual data-contract statuses (`BenchmarkContractStatus`,
`UniverseContractStatus`, `TaxonomyContractStatus`) and produces a
unified `OperatorWorkflowStatusSnapshot` via the existing
`OperatorStatusWorkflowContract.build_snapshot()`.

#### Scenario: all three data contracts healthy
- **WHEN** the aggregator receives benchmark, universe, and taxonomy
  statuses all with `contract_health == "ok"`
- **THEN** the produced snapshot has `overall_status_category == "not_ready"`
  because runtime and placeholder boundaries are auto-filled as not_ready
- **AND** the three data-contract entries each have
  `status_category == "ok"`

#### Scenario: one contract in error propagates to overall status
- **WHEN** the aggregator receives a benchmark status with
  `contract_health == "error"` and the other two as `"ok"`
- **THEN** the produced snapshot has `overall_status_category == "error"`
- **AND** the benchmark entry's `errors` tuple is non-empty

#### Scenario: caller provides runtime boundary entries
- **WHEN** the caller supplies pre-built `OperatorStatusEntry` objects
  for `canonical_runtime_boundary` and `runtime_placeholder_boundary`
  with `status_category == "ok"` and all three data contracts are ok
- **THEN** the produced snapshot has `overall_status_category == "ok"`
- **AND** `missing_boundary_types` is empty

### Requirement: Aggregator SHALL auto-generate placeholder entries for unrepresented boundary types

The aggregator SHALL NOT require the caller to manually construct
placeholder entries. If a required boundary type has no entry, the
aggregator SHALL insert a `not_ready` placeholder automatically.

#### Scenario: no runtime entries provided
- **WHEN** the aggregator is called with only data-contract statuses
- **THEN** `canonical_runtime_boundary` and
  `runtime_placeholder_boundary` each have an auto-generated
  `not_ready` placeholder entry in the snapshot
- **AND** `OperatorStatusWorkflowContract.build_snapshot()` does NOT
  raise

### Requirement: Aggregator SHALL map contract_health to status_category without reinterpretation

The mapping from `contract_health` to `status_category` SHALL be
direct: `"ok"` → `STATUS_OK`, `"warning"` → `STATUS_WARNING`,
`"error"` → `STATUS_ERROR`. No additional filtering, thresholds, or
overrides.

#### Scenario: warning contract produces warning entry
- **WHEN** a universe contract status has `contract_health == "warning"`
  with `warnings == ("stale_data",)`
- **THEN** the aggregator produces an entry with
  `status_category == "warning"` and `warnings == ("stale_data",)`

### Requirement: Aggregator SHALL delegate overall status computation to OperatorStatusWorkflowContract

The aggregator SHALL NOT re-implement worst-wins logic. It SHALL call
`OperatorStatusWorkflowContract.build_snapshot()` and return the
resulting `OperatorWorkflowStatusSnapshot` directly.

#### Scenario: governance scan confirms no duplicate aggregation logic
- **WHEN** governance tests scan `src/core/operator_status_aggregator.py`
- **THEN** the file does NOT contain the string `"overall_status"` in
  any assignment context — only `build_snapshot()` determines it
