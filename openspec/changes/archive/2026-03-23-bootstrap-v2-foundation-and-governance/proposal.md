## Why

V2 starts from a clean repository, so we need an explicit governance and architecture baseline before implementing runtime logic. Without this baseline, future work can quickly drift in metrics semantics and reproducibility.

## What Changes

- Add initial repository-level governance documents (`README`, `AGENTS`, architecture/current-state/roadmap docs).
- Record V1 lessons as explicit reusable guardrails for V2.
- Define a bootstrap OpenSpec capability for canonical-vs-experimental governance boundaries.
- Keep this change scope to foundation and docs only (no runtime trading behavior).

## Capabilities

### New Capabilities
- `v2-foundation-governance-bootstrap`: establish mandatory governance baseline and architecture intent for V2 before feature implementation.

### Modified Capabilities
- None.

## Impact

- Affected areas:
  - `README.md`
  - `AGENTS.md`
  - `docs/*`
  - `openspec/changes/bootstrap-v2-foundation-and-governance/*`
- No runtime code changes.
- No trading semantics changes.
