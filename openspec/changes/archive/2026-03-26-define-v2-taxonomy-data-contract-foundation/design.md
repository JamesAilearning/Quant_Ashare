## Context

V2 needs taxonomy-contract hardening before any industry-aware runtime behavior is introduced.  
This change is contract-only and intentionally excludes industry-cap or other runtime constraint semantics.

## Goals / Non-Goals

**Goals:**
- Define taxonomy artifact source-of-truth rules.
- Define required taxonomy provenance metadata fields.
- Define supported temporal validity modes for taxonomy mappings.
- Define validation categories for taxonomy contract health.
- Define operator-facing taxonomy status requirements.
- Keep explicit separation between taxonomy validation semantics and future industry-aware runtime behavior.

**Non-Goals:**
- Do not implement industry-cap runtime behavior.
- Do not change trading semantics.
- Do not change canonical official-metrics definition.
- Do not add unrelated UI work.

## Decisions

1. Contract-first taxonomy governance
- Decision: taxonomy contract boundaries are defined before runtime use.
- Rationale: prevents implicit coupling between mapping artifacts and runtime logic.

2. Explicit temporal-validity modes
- Decision: contract defines supported temporal validity modes (`static`, `trade_date`, `range`) as schema-level expectations.
- Rationale: temporal semantics must be explicit to control lookahead risk.

3. Provenance metadata is required
- Decision: taxonomy contract requires source and snapshot provenance fields.
- Rationale: operators and reviewers need auditable lineage for taxonomy mappings.

4. Inconsistent-mapping and coverage checks are first-class
- Decision: validation expectations explicitly include inconsistent mappings and incomplete coverage.
- Rationale: taxonomy health risks are often mapping consistency and date coverage, not only file presence.

5. Runtime industry-aware semantics remain out-of-scope
- Decision: taxonomy contract does not define selection, industry-cap enforcement, or any runtime behavior.
- Rationale: keep this change foundation-only and preserve governance baseline.

## Risks / Trade-offs

- [Risk] Temporal-validity mode definitions may be interpreted as runtime policy.
  - Mitigation: define them as contract schema expectations only; runtime usage remains separate future work.
- [Risk] Strict taxonomy checks could be perceived as changing behavior.
  - Mitigation: this change defines expectations only; enforcement severity remains implementation-phase policy.

## Migration Plan

1. Add taxonomy contract spec delta with explicit requirements and scenarios.
2. Validate proposal under strict OpenSpec checks.
3. Use this contract in a future `/opsx:apply` for:
   - normalized taxonomy contract loader/validator
   - governance tests
   - optional operator-facing status surfaces (separate scoped change)

Rollback:
- Revert this change if governance direction changes before implementation starts.

## Open Questions

- Should taxonomy stale thresholds be shared with benchmark contract defaults or remain taxonomy-specific?
- Should incomplete coverage default to warning or hard error in initial implementation?
- Should manifest-side timestamps be required to be <= data snapshot date by default?
