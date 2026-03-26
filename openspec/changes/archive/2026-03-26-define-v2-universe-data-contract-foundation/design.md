## Context

V2 has contract foundations for benchmark and taxonomy artifacts but not for universe membership artifacts.  
This change defines universe contract boundaries only and intentionally excludes runtime universe-selection semantics.

## Goals / Non-Goals

**Goals:**
- Define universe artifact source-of-truth rules.
- Define required universe provenance metadata fields.
- Define supported temporal validity modes for membership artifacts.
- Define validation categories for universe contract health.
- Define operator-facing universe status requirements.
- Keep explicit separation between universe validation semantics and runtime universe-selection behavior.

**Non-Goals:**
- Do not implement runtime universe-selection behavior.
- Do not change trading semantics.
- Do not change canonical official-metrics definition.
- Do not add unrelated UI work.

## Decisions

1. Contract-first universe governance
- Decision: universe artifact contract boundaries are defined before runtime usage.
- Rationale: avoids hidden coupling and ad-hoc membership assumptions.

2. Explicit source-of-truth and temporal validity
- Decision: contract defines source-of-truth and supported temporal modes as first-class schema requirements.
- Rationale: membership timing must be explicit to reduce lookahead risk.

3. Provenance metadata is required
- Decision: universe contract requires source/snapshot/version metadata.
- Rationale: universe changes are high-impact and must be auditable.

4. Consistency and coverage checks are required
- Decision: validation expectations include membership consistency and date coverage checks.
- Rationale: file presence alone is insufficient for reliable universe artifacts.

5. Runtime universe semantics remain out-of-scope
- Decision: no provider precedence, selection fallback, or runtime enforcement semantics are defined here.
- Rationale: keep this change foundation-only and scope-safe.

## Risks / Trade-offs

- [Risk] Temporal validity definitions may be confused with runtime policy.
  - Mitigation: contract text explicitly keeps runtime semantics out of scope.
- [Risk] Validation categories may seem strict before runtime exists.
  - Mitigation: this change defines boundaries only; implementation severity remains a future decision.

## Migration Plan

1. Add universe contract spec delta with explicit requirements and scenarios.
2. Validate proposal with strict OpenSpec checks.
3. Follow with a future `/opsx:apply` to add:
   - universe contract interfaces/placeholders
   - governance tests
   - optional operator status surfaces (separate scoped change)

Rollback:
- Revert this change if governance direction changes before implementation.

## Open Questions

- Should universe internal date-gap policy default to warning or error in initial implementation?
- Should minimum coverage policy be globally shared with other contracts or universe-specific?
- Should sidecar manifest be mandatory from first implementation stage?
