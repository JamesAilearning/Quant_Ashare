## Context

V2 currently has governance baseline docs but does not yet have a concrete directory/module/test skeleton aligned with that governance model. If feature work starts before structure is defined, later canonical-path, contract, and operator workflow changes can accumulate hidden coupling and unclear ownership.

This change creates structure only and intentionally avoids runtime logic.

## Goals / Non-Goals

**Goals:**
- Establish a minimal V2 skeleton aligned with OpenSpec-first governance.
- Separate production runtime boundaries (`app/web`, `src/core`, `src/data`, `src/contracts`) from research boundaries (`research/factor_lab`).
- Add placeholder modules and minimal test scaffolding for future logic and governance regression tracks.
- Encode V1 lessons in structure:
  - one canonical official metrics path
  - explicit contract boundaries
  - no hidden fallback coupling
  - minimal, archivable changes

**Non-Goals:**
- Do not implement canonical backtest contract yet.
- Do not implement benchmark/universe/taxonomy contract logic yet.
- Do not implement operator workflow/guardrail runtime behavior yet.
- Do not implement factor generation/evaluation logic.
- Do not modify trading semantics.

## Decisions

1. Boundary-first folder layout
- Decision: create explicit layer directories before adding runtime code.
- Rationale: makes later staged changes additive and less ambiguous.
- Alternative considered: defer folders until each feature lands.
  - Rejected because it encourages ad hoc coupling and path churn.

2. Research isolation by default
- Decision: reserve `research/factor_lab/` with explicit non-production README.
- Rationale: allows research velocity without contaminating canonical runtime/config paths.
- Alternative considered: keep research under `src/`.
  - Rejected to avoid accidental production coupling.

3. Minimal placeholders only
- Decision: add only package/README/test skeleton files, no functional trading code.
- Rationale: keep the change reviewable and foundation-only.
- Alternative considered: scaffold executable stubs.
  - Rejected because it blurs “skeleton vs implementation”.

4. Test skeleton split
- Decision: create minimal skeleton areas for logic tests and governance/contract regression tests.
- Rationale: preserves V1 lesson that governance boundaries need explicit regression protection.

## Risks / Trade-offs

- [Risk] Contributors may treat placeholders as implementation-ready modules.
  - Mitigation: boundary docs explicitly state “skeleton only” and list non-goals.
- [Risk] Early directory choices might need adjustment later.
  - Mitigation: keep files minimal to reduce migration cost.
- [Risk] Research artifacts could still leak into production in future changes.
  - Mitigation: explicit research boundary requirement + future regression checks.

## Migration Plan

1. Add skeleton directories and minimal placeholder files.
2. Add/update layer boundary docs and research-only disclaimer.
3. Add minimal tests skeleton to anchor future regression suites.
4. Validate structure and OpenSpec artifacts.
5. Archive as foundation-only change.

Rollback:
- Revert added skeleton files if structure direction changes before runtime work starts.

## Open Questions

- Choose `app/` vs `web/` as the final canonical UI root in the first operator workflow implementation change.
- Decide whether future CI should enforce boundary import rules (`research/` cannot be imported from production runtime).
