## Why

Feature dataset construction is currently hard-coded to `Alpha158`, while
runtime configs expose `feature_handler` as if other handlers can be selected.
That makes handler support difficult to extend and leaves the config surface
more permissive than the implementation.

## What Changes

- Add a feature-handler registry/factory boundary for qlib handler classes.
- Register `Alpha158` by default and support explicit registration of
  additional handlers such as `Alpha360` or test/custom handlers.
- Keep unsupported handlers as loud validation errors that list registered
  names.
- Add tests for default support, custom registration, and unknown handler
  rejection.

## Capabilities

### New Capabilities

- `v2-feature-handler-registry`: Defines runtime feature-handler registration
  and construction semantics for qlib dataset building.

### Modified Capabilities

- None.

## Impact

- Affected code: `src/data/feature_dataset_builder.py` and focused tests.
- No change to default `feature_handler: Alpha158`.
- No new external dependency and no change to official metrics semantics.
