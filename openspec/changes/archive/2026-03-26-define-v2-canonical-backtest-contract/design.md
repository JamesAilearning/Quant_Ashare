## Context

The V2 skeleton and governance baseline already exist, but canonical backtest semantics are not yet codified as a contract artifact. This creates risk that early runtime work may unintentionally mix official and experimental semantics or add implicit fallback behavior.

This change defines the contract only and intentionally avoids runtime implementation.

## Goals / Non-Goals

**Goals:**
- Define one canonical official-metrics backtest contract for V2.
- Specify canonical input/output interfaces and ownership boundaries.
- Define explicit separation between:
  - canonical runtime execution
  - experimental/runtime-diagnostic logic
  - research-only logic under `research/factor_lab/`
- Define minimum validation and regression expectations that future implementation must satisfy.

**Non-Goals:**
- Do not implement backtest runtime.
- Do not migrate experimental constraints into canonical execution.
- Do not implement strategy research logic.
- Do not change trading semantics beyond contract definition.

## Decisions

1. Canonical path is contract-first and singular
- Decision: V2 will define exactly one canonical official-metrics path in contract language.
- Rationale: prevents competing official metrics sources.
- Alternative considered: allow multiple "official" pipelines by use-case.
  - Rejected due to auditability and comparability risk.

2. Boundary-first interface design
- Decision: contract specifies accepted canonical inputs and explicit exclusions.
- Rationale: blocks hidden coupling and implicit fallback.
- Alternative considered: define only outputs first.
  - Rejected because input ambiguity causes semantics drift.

3. Explicit experimental/research demarcation
- Decision: contract requires experimental and research outputs to be non-canonical by default and separately labeled.
- Rationale: preserves governance baseline and avoids accidental promotion.

4. Regression requirements included in contract scope
- Decision: minimum regression expectations are part of contract, not postponed.
- Rationale: V1 lessons show boundary regressions are a primary protection mechanism.

## Risks / Trade-offs

- [Risk] Contract may appear strict before implementation starts.
  - Mitigation: define only minimum required interfaces and validation expectations.
- [Risk] Future implementation may need extension points not captured initially.
  - Mitigation: allow additive changes via new OpenSpec changes without relaxing core canonical boundaries.
- [Risk] Teams may conflate data-contract issues with backtest contract issues.
  - Mitigation: keep data-contract requirements referenced but separately owned under `src/contracts`.

## Migration Plan

1. Add canonical backtest contract spec with explicit requirements/scenarios.
2. Validate change under strict OpenSpec rules.
3. Use this contract as the basis for the next implementation changes:
  - canonical runtime skeleton wiring
  - contract tests
  - operator-facing canonical status surfaces

Rollback:
- Revert this change if governance steering changes before runtime implementation begins.

## Open Questions

- Should canonical output include a mandatory config-fingerprint field from day one, or first release as optional then harden to required?
- Should canonical contract require strict failure on missing benchmark input, or permit explicit "no benchmark" canonical runs with constrained metric set?
