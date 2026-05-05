## Why

The latest review found that runtime industry semantics and Tushare taxonomy
integration are still not fully boundary-safe: risk constraints can execute
from `src/core`, Pipeline attribution cannot consume a validated taxonomy
artifact, Tushare is not discoverable from project metadata, and duplicate
static taxonomy rows can be published successfully before failing in the map
consumer.

This change closes those gaps without widening the canonical official metrics
path or introducing implicit industry fallback.

## What Changes

- Move executable risk-constraint behavior out of the canonical `src/core`
  layer and leave a fail-closed compatibility boundary there.
- Add optional Pipeline attribution wiring for an explicitly configured static
  taxonomy CSV + manifest + taxonomy id, validated through the existing
  taxonomy loader and contract before the map is used.
- Keep the default Pipeline attribution behavior unchanged: no configured
  taxonomy means the existing board heuristic remains explicitly labeled.
- Add a project metadata extra for the shipped Tushare integration and update
  install hints to reference that extra.
- Reject duplicate instruments in static taxonomy publish rows before writing
  any artifact.

## Capabilities

### New Capabilities
- `v2-runtime-dependency-metadata`: declares dependency metadata expectations
  for shipped runtime-adjacent integrations such as Tushare.

### Modified Capabilities
- `v2-canonical-runtime-orchestration`: adds explicit risk-constraint and
  taxonomy-attribution runtime boundary requirements.
- `v2-taxonomy-artifact-publisher`: requires static taxonomy publishing to
  reject duplicate instruments before IO.

## Impact

- Affected code: `src/core/pipeline.py`, `src/core/risk_constraints.py`,
  `src/data/taxonomy_artifact_publisher.py`, Tushare scripts/client metadata,
  and focused tests.
- API impact: Pipeline gains optional taxonomy-attribution config fields. The
  canonical `src.core.risk_constraints` import path becomes a fail-closed
  boundary; executable behavior moves to an explicitly experimental namespace.
- Dependency impact: Tushare becomes discoverable via a project optional extra.
