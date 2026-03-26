## 1. Discovery and Boundary Framing

- [x] 1.1 Review V2 governance baseline (`AGENTS.md`, canonical contract spec, benchmark contract spec, architecture docs).
- [x] 1.2 Enumerate taxonomy contract failure modes and out-of-scope runtime semantics.
- [x] 1.3 Confirm this change is contract-only and introduces no runtime behavior.

## 2. Taxonomy Contract Definition

- [x] 2.1 Define taxonomy artifact source-of-truth boundary.
- [x] 2.2 Define required taxonomy metadata/provenance fields.
- [x] 2.3 Define supported temporal validity modes (`static`, `trade_date`, `range`).
- [x] 2.4 Define validation expectations for schema, freshness, coverage, mapping consistency, and temporal leakage.
- [x] 2.5 Define operator-facing contract status requirements.
- [x] 2.6 Define explicit boundary between taxonomy contract validation and future industry-aware runtime semantics.

## 3. Validation and Regression Expectations

- [x] 3.1 Define minimum test categories for taxonomy contract validation behavior.
- [x] 3.2 Define minimum governance regression expectations for operator-visible taxonomy status.
- [x] 3.3 Ensure requirements are concrete and testable via scenario wording.

## 4. Proposal Quality Gates

- [x] 4.1 Run `openspec validate define-v2-taxonomy-data-contract-foundation --strict`.
- [x] 4.2 Run `openspec status --change define-v2-taxonomy-data-contract-foundation`.
- [x] 4.3 Confirm proposal is implementation-ready for a future `/opsx:apply`.
