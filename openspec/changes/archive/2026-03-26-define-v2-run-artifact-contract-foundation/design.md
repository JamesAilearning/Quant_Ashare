## Context

V2 has foundational contracts for data-side artifacts but lacks a formal contract for run artifacts and reproducibility metadata.  
This change is contract-only and intentionally excludes runtime execution behavior.

## Goals / Non-Goals

**Goals:**
- Define source-of-truth boundaries for run artifacts and run manifests.
- Define mandatory reproducibility metadata fields.
- Define validation categories for run artifact contract health.
- Define operator-facing status requirements for run artifact health.
- Keep explicit separation between run-artifact validation and runtime trading semantics.

**Non-Goals:**
- Do not implement runtime training/backtest orchestration.
- Do not change trading semantics.
- Do not change canonical official-metrics definition.
- Do not add unrelated UI work.

## Decisions

1. Contract-first run governance
- Decision: define run artifact and reproducibility contract before runtime pipeline implementation.
- Rationale: reduces ambiguity and improves auditability.

2. Explicit reproducibility metadata requirements
- Decision: contract requires explicit reproducibility fields (config fingerprint, dependency lineage, run timestamps, source contracts).
- Rationale: reproducibility cannot rely on implicit context.

3. Validation boundaries are explicit and testable
- Decision: contract includes categories for missing artifacts, missing metadata, schema mismatch, lineage inconsistency, and temporal/provenance anomalies.
- Rationale: these are core risks for auditable model/backtest outputs.

4. Runtime semantics remain out of scope
- Decision: no runtime behavior, scheduling, or execution logic is introduced.
- Rationale: keep scope minimal and compliant with AGENTS foundation-first rules.

## Risks / Trade-offs

- [Risk] Contract requirements may seem strict before runtime exists.
  - Mitigation: this change defines boundaries only; enforcement policy detail can remain configurable in apply-phase.
- [Risk] Some fields may evolve with runtime architecture.
  - Mitigation: use additive OpenSpec changes for future extensions.

## Migration Plan

1. Add spec delta for run artifact and reproducibility contract requirements.
2. Validate proposal under strict OpenSpec checks.
3. Use this as basis for future `/opsx:apply`:
   - contract interfaces/placeholders
   - governance regression tests
   - optional operator-facing status surfaces (separate scoped change)

Rollback:
- Revert this change if governance direction changes before implementation.

## Open Questions

- Which reproducibility fields are strict-required on day one versus warning-level in early runtime?
- Should code context require commit hash always, or allow local working-tree markers for dev-mode runs?
- Should config fingerprint include raw config snapshot hash only, or include resolved defaults hash as well?
