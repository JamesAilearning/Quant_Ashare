## Context

The V2 baseline defines contract health in multiple domains (benchmark, taxonomy, universe, run artifacts), but there is no single operator-facing status/workflow foundation that normalizes message categories and boundaries.

This change is contract-first and intentionally excludes full web workflow implementation.

## Goals / Non-Goals

**Goals:**
- Define operator-facing status categories and expected boundaries.
- Define consistent representation for health states, warnings, errors, and placeholders.
- Define explicit separation between informational status and governance meaning.
- Define minimum status/workflow expectations for canonical runtime, data contracts, and placeholders.
- Define regression expectations for operator-visible status boundaries.

**Non-Goals:**
- Do not implement full web UI behavior.
- Do not implement runtime trading behavior.
- Do not change official-vs-experimental governance.
- Do not add unrelated research functionality.

## Decisions

1. Status semantics are contract-level first
- Decision: define status categories and boundaries at contract/spec layer before UI implementation.
- Rationale: avoids inconsistent messaging across domains.

2. Informational vs governance must be explicitly separated
- Decision: status contract requires explicit messaging that contract health is informational unless governance policy says otherwise.
- Rationale: prevents accidental promotion of status indicators into governance meaning.

3. Placeholder states are first-class
- Decision: not-yet-implemented/runtime-placeholder states are explicitly represented and surfaced.
- Rationale: operators need clarity on why a status is missing or non-executable.

4. Cross-domain minimum workflow expectations
- Decision: define minimal workflow/status checkpoints across canonical boundary, data contracts, and placeholders.
- Rationale: ensures consistent operator experience before full runtime implementation.

5. Regression guardrails included now
- Decision: operator-visible status boundary tests are required as part of foundation.
- Rationale: status messaging drift is a known governance risk.

## Risks / Trade-offs

- [Risk] Status categories may appear UI-prescriptive too early.
  - Mitigation: keep this contract-level and avoid UI layout/interaction details.
- [Risk] Teams may misread informational states as governance states.
  - Mitigation: require explicit informational-vs-governance wording in requirements.

## Migration Plan

1. Add operator status/workflow foundation spec requirements and scenarios.
2. Validate proposal with strict OpenSpec checks.
3. Follow with future `/opsx:apply` to add:
   - status contract interfaces/placeholders
   - governance regression tests
   - minimal docs alignment

Rollback:
- Revert if governance steering changes before implementation begins.

## Open Questions

- Should unknown/unavailable status map to `warning` or a dedicated `not_ready` category in first implementation?
- Should placeholder status be mandatory for all core modules or only runtime-adjacent modules?
- Which operator-facing summary fields should be globally required versus domain-specific?
