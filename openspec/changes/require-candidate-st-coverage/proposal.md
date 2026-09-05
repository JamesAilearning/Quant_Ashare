## Why

A fresh current-ST snapshot can still omit an individual scored candidate or contain a null, non-string, or duplicate name row. The recommendation path currently converts or defaults these values and can treat unknown ST status as non-ST, violating its existing fail-loud intent.

## What Changes

- Require exactly one original non-blank string name for every scored instrument not excluded by the authoritative entry-day microstructure mask, before current-ST filtering and Top-K selection.
- Refuse the entire recommendation with a classified error identifying affected codes and the name source when this evidence is incomplete or ambiguous; do not silently drop unknown candidates or guess their status.
- Preserve already-masked audit rows, whole-snapshot freshness/schema guards, valid-name ranking, current-ST marker rules, and HOLD cadence semantics.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `v2-daily-stock-recommendation`: add candidate-level current-ST evidence completeness to the existing mandatory snapshot contract.

## Impact

Scoped to `src/inference/daily_recommend.py`, synthetic recommendation tests, and this OpenSpec change. No persisted schema, CLI signature, new dependency, canonical metric, Pipeline/WalkForward artifact, production snapshot, model, or deployment changes. Previously accepted incomplete candidate snapshots will now fail explicitly.
