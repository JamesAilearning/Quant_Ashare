## 1. Discovery and Boundary Framing

- [x] 1.1 Review V2 governance baseline (`AGENTS.md`, canonical contract spec, architecture docs).
- [x] 1.2 Enumerate benchmark contract failure modes and out-of-scope runtime semantics.
- [x] 1.3 Confirm this change is contract-only and introduces no runtime behavior.

## 2. Benchmark Contract Definition

- [x] 2.1 Define benchmark artifact source-of-truth boundary.
- [x] 2.2 Define required benchmark metadata/provenance fields.
- [x] 2.3 Define validation expectations for schema, coverage, freshness, and temporal integrity.
- [x] 2.4 Define operator-facing contract status requirements.
- [x] 2.5 Define explicit boundary between contract validation and runtime benchmark selection semantics.

## 3. Validation and Regression Expectations

- [x] 3.1 Define minimum test categories for benchmark contract validation behavior.
- [x] 3.2 Define minimum governance regression expectations for operator-visible benchmark status.
- [x] 3.3 Ensure requirements are concrete and testable via scenario wording.

## 4. Proposal Quality Gates

- [x] 4.1 Run `openspec validate define-v2-benchmark-data-contract-foundation --strict`.
- [x] 4.2 Run `openspec status --change define-v2-benchmark-data-contract-foundation`.
- [x] 4.3 Confirm proposal is implementation-ready for a future `/opsx:apply`.
