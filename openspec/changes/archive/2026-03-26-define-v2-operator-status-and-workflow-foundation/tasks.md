## 1. Discovery and Boundary Framing

- [x] 1.1 Review V2 governance baseline (`AGENTS.md`, current specs, architecture docs).
- [x] 1.2 Enumerate operator-facing status/workflow failure modes and messaging risks.
- [x] 1.3 Confirm this change is contract-only and introduces no runtime behavior.

## 2. Operator Status and Workflow Contract Definition

- [x] 2.1 Define operator-facing status categories and boundaries.
- [x] 2.2 Define representation rules for health/warning/error/placeholder states.
- [x] 2.3 Define explicit informational-vs-governance separation requirements.
- [x] 2.4 Define minimum workflow/status expectations for canonical boundary, data contracts, and placeholders.
- [x] 2.5 Define required operator-facing status summary fields at foundation level.

## 3. Validation and Regression Expectations

- [x] 3.1 Define minimum regression categories for operator-visible status boundaries.
- [x] 3.2 Define minimum regression expectations for informational-vs-governance separation.
- [x] 3.3 Ensure requirements are concrete and scenario-testable.

## 4. Proposal Quality Gates

- [x] 4.1 Run `openspec validate define-v2-operator-status-and-workflow-foundation --strict`.
- [x] 4.2 Run `openspec status --change define-v2-operator-status-and-workflow-foundation`.
- [x] 4.3 Confirm proposal is implementation-ready for a future `/opsx:apply`.
