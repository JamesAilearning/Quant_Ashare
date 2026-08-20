## Why

Results and walk-forward pages explain one research run at a time, but they do
not establish whether two experiments are comparable before an operator treats
one outcome as better.  A read-only comparison workbench makes the comparison
contract, provenance, and exact source pages visible in one place.

## What Changes

- Add a research-only, read-only page that selects two to five existing runs
  and preserves the selected run IDs in the URL.
- Compare required experiment-contract fields before allowing a controlled
  metric ordering, and state every missing or mismatched field.
- Display existing report metrics, walk-forward stability evidence, provenance,
  and exact links to Results, Walk-Forward, configuration, and logs.
- Treat missing, invalid, or unconfirmable metadata as a comparison block;
  never compute replacement metrics or promote a research result to serving.
- Stamp the existing canonical bundle-content identity into new backtest
  provenance, and version walk-forward folds so resumed runs cannot mix
  pre-identity evidence with current evidence.

## Capabilities

### New Capabilities

- `v2-research-run-comparison-workbench`: research-run selection, provenance
  checks, controlled comparison, and precise read-only navigation.

### Modified Capabilities

None.

## Impact

- Affected UI: one comparison page, a pure read-model helper, and the research
  navigation entry.
- Affected inputs: existing job catalog rows and existing pipeline/
  walk-forward report artifacts, plus additive provenance on newly written
  canonical backtest artifacts.
- No run launch, cancellation, configuration mutation, metrics calculation,
  production-serving change, or trading execution behavior changes.
