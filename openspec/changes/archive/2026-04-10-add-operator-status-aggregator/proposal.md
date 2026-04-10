# Proposal: add-operator-status-aggregator

## Why

V2 has three fully implemented data contracts (benchmark, universe,
taxonomy) that each produce an independent `ContractStatus` with
`contract_health` ∈ {ok, warning, error}. The operator status/workflow
contract defines `OperatorStatusEntry` and `build_snapshot()` to
aggregate entries across boundary types into a single
`OperatorWorkflowStatusSnapshot`. But there is no code that bridges
the gap — no module converts a `BenchmarkContractStatus` into an
`OperatorStatusEntry`.

Without this bridge, an operator who wants a single "is the system
healthy?" answer must manually inspect each contract status and
mentally aggregate them. The aggregator makes this a single function
call.

## Goals

1. Provide `OperatorStatusAggregator` that accepts zero or more
   contract statuses (benchmark, universe, taxonomy) and produces
   `OperatorStatusEntry` objects for the `data_contract_boundary`.
2. Accept optional pre-built entries for `canonical_runtime_boundary`
   and `runtime_placeholder_boundary` so that all three required
   boundary types are represented.
3. When a required boundary type has no entry, auto-generate a
   `not_ready` placeholder entry so `build_snapshot()` never rejects
   for missing boundaries.
4. Delegate final aggregation to `OperatorStatusWorkflowContract
   .build_snapshot()` — no re-implementation of overall-status logic.

## Non-goals

- No runtime selection semantics (still out of scope).
- No persistence or serialization of snapshots.
- No UI or reporting layer.
- The aggregator does not replace individual contract validation; it
  consumes their output.

## Design notes

### Contract status → OperatorStatusEntry mapping

Each contract status maps to one `OperatorStatusEntry`:

| Field               | Source                                                   |
|---------------------|----------------------------------------------------------|
| `component_id`      | `"{contract_name}:{entity_name}"` (e.g. `"v2-benchmark-data-contract:SH000300"`) |
| `boundary_type`     | `BOUNDARY_DATA_CONTRACT`                                 |
| `status_category`   | `contract_health` mapped: ok→ok, warning→warning, error→error |
| `summary`           | One-line generated from contract name + health           |
| `warnings`          | `status.warnings`                                        |
| `errors`            | `status.errors`                                          |
| `governance_label`  | `GOVERNANCE_CANONICAL`                                   |

### Auto-placeholder for missing boundaries

If the caller provides no entries for `canonical_runtime_boundary` or
`runtime_placeholder_boundary`, the aggregator inserts a `not_ready`
placeholder entry with `is_placeholder=True`. This satisfies the
workflow contract's requirement that all three boundary types are
represented.

### Worst-wins aggregation

The aggregator does NOT implement its own worst-wins logic. It passes
all entries to `OperatorStatusWorkflowContract.build_snapshot()` which
already implements: error > warning > not_ready > ok.
