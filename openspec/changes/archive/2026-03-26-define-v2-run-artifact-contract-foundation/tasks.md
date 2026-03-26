## 1. Discovery and Boundary Framing

- [x] 1.1 Review V2 governance baseline (`AGENTS.md`, canonical/data contract specs, architecture docs).
- [x] 1.2 Enumerate run-artifact and reproducibility failure modes plus out-of-scope runtime semantics.
- [x] 1.3 Confirm this change is contract-only and introduces no runtime behavior.

## 2. Run Artifact Contract Definition

- [x] 2.1 Define run-artifact source-of-truth boundary.
- [x] 2.2 Define required run manifest and reproducibility metadata fields.
- [x] 2.3 Define validation expectations for schema, metadata completeness, lineage consistency, and temporal/provenance anomalies.
- [x] 2.4 Define operator-facing contract status requirements.
- [x] 2.5 Define explicit boundary between run-artifact validation and runtime execution semantics.

## 3. Validation and Regression Expectations

- [x] 3.1 Define minimum test categories for run artifact contract validation behavior.
- [x] 3.2 Define minimum governance regression expectations for operator-visible run status.
- [x] 3.3 Ensure requirements are concrete and testable via scenario wording.

## 4. Proposal Quality Gates

- [x] 4.1 Run `openspec validate define-v2-run-artifact-contract-foundation --strict`.
- [x] 4.2 Run `openspec status --change define-v2-run-artifact-contract-foundation`.
- [x] 4.3 Confirm proposal is implementation-ready for a future `/opsx:apply`.
