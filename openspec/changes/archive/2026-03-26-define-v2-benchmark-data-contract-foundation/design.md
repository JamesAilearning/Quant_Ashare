## Context

V2 has a governance baseline and canonical backtest contract, but benchmark artifacts do not yet have an explicit data contract.  
This change is decision/documentation-only and intentionally excludes runtime benchmark-selection semantics.

## Goals / Non-Goals

**Goals:**
- Define source-of-truth rules for benchmark artifacts.
- Define required benchmark provenance metadata fields.
- Define contract-level validation categories for benchmark artifacts.
- Define operator-facing status expectations for contract health.
- Keep clear boundary between validation contract and runtime benchmark selection semantics.

**Non-Goals:**
- Do not implement full benchmark runtime behavior.
- Do not change trading semantics.
- Do not change canonical official-metrics definition.
- Do not add unrelated UI work.

## Decisions

1. Contract-first benchmark artifact governance
- Decision: benchmark artifact governance is defined before runtime use.
- Rationale: avoids hidden source precedence and ad-hoc validation drift.

2. Explicit source-of-truth declaration
- Decision: contract must declare where benchmark artifacts come from and how artifact identity is established.
- Rationale: improves auditability and operator troubleshooting.

3. Provenance metadata as required contract surface
- Decision: benchmark contract requires metadata/provenance fields (origin, snapshot, coverage, schema hints, validation context).
- Rationale: benchmark health cannot be audited from raw prices alone.

4. Validation boundary is explicit and testable
- Decision: contract defines required validation categories for missing artifacts, schema integrity, staleness, coverage completeness, and temporal integrity.
- Rationale: these are the primary failure modes observed in V1 and brownfield operation.

5. Runtime benchmark selection semantics remain out-of-scope
- Decision: this change does not define or alter provider precedence or selection fallback semantics.
- Rationale: keep this change foundation-only and avoid silent semantic drift.

## Risks / Trade-offs

- [Risk] Contract may be stricter than current ad-hoc scripts.
  - Mitigation: define validation expectations first; implementation policy severity can remain configurable in future apply.
- [Risk] Operator status requirements may look UI-adjacent.
  - Mitigation: scope remains contract/status definition only; no UI implementation in this change.

## Migration Plan

1. Add a new benchmark contract spec delta with explicit requirements and scenarios.
2. Validate proposal artifacts under strict OpenSpec checks.
3. Use this contract as basis for a future `/opsx:apply` that adds:
   - normalized contract loader/validator
   - governance tests
   - optional UI status surfaces (separate scoped change)

Rollback:
- Revert the change if governance direction for benchmark contract shifts before implementation.

## Open Questions

- Should stale threshold defaults be globally shared with other data contracts or benchmark-specific?
- Should internal date-gap policy default to warning or hard fail in early V2 implementation?
- Should benchmark contract require sidecar manifest from day one, or support phased hardening?
