## 1. Contract Discovery and Boundary Lock

- [x] 1.1 Review existing V2 governance baseline (`AGENTS.md`, architecture docs, prior specs).
- [x] 1.2 Enumerate canonical/offical boundary requirements and explicit non-goals.
- [x] 1.3 Confirm no runtime implementation is included in this change.

## 2. Canonical Contract Definition

- [x] 2.1 Define canonical path responsibilities and ownership boundaries.
- [x] 2.2 Define accepted canonical inputs and explicit exclusions.
- [x] 2.3 Define required canonical outputs and official-reporting fields.
- [x] 2.4 Define explicit canonical vs experimental vs research separation.
- [x] 2.5 Define no-implicit-fallback requirement for canonical semantics.

## 3. Validation and Regression Expectations

- [x] 3.1 Define minimum validation expectations for canonical source integrity.
- [x] 3.2 Define minimum regression expectations for boundary protection.
- [x] 3.3 Ensure requirements are testable via concrete scenarios.

## 4. Proposal Quality Gates

- [x] 4.1 Run `openspec validate define-v2-canonical-backtest-contract --strict`.
- [x] 4.2 Run `openspec status --change define-v2-canonical-backtest-contract`.
- [x] 4.3 Confirm proposal is implementation-ready for a future `/opsx:apply`.
