## Why

Tushare staged payload reuse currently trusts file paths alone, so a short smoke
run or a narrow instrument run can poison later wider publishes that reuse the
same staging directory. A separate review also found `PipelineConfig` accepts
boolean `signal_to_execution_lag` values even though downstream canonical
contracts reject them.

## What Changes

- Validate staged cache reuse against the Tushare API name and request
  parameters before reading an existing staged file.
- Preserve raw staged daily/adjustment payloads across instrument scopes; scope
  filters are applied only to the in-memory staged view used by the current
  publish.
- Add regression tests for date-range cache invalidation and narrow-to-wide
  instrument reuse.
- Reject boolean `PipelineConfig.signal_to_execution_lag` values before any
  pipeline work starts.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `v2-tushare-qlib-provider-bundle`: staged raw payload reuse must be keyed or
  validated by request parameters, and raw staged market payloads must not be
  overwritten by scope-filtered views.
- `v2-canonical-runtime-orchestration`: Pipeline config validation must reject
  boolean signal lag values consistently with the canonical backtest contract.

## Impact

- Affects `src/data/tushare/provider_bundle.py` staged cache behavior and
  `src/core/pipeline.py` config validation.
- Adds focused regression tests under `tests/logic/`.
- Existing staging directories without cache metadata will be refetched once
  rather than trusted blindly.
