## 1. Regression evidence

- [x] 1.1 Add failing synthetic CSV round-trip tests for cadence fields and non-bool marker refusal, including empty tables and pre-I/O failure.
- [x] 1.2 Pin daily CSV byte compatibility, JSON picks/schema, original audit shape and input-frame nonmutation.

## 2. Implementation

- [x] 2.1 Project one cadence context to both CSVs and JSON; validate marker type before I/O without changing scheduling or stock rows.
- [x] 2.2 Update the operator runbook for appended columns, HOLD meaning, unknown dates, and old/empty CSV limitations.

## 3. Verification

- [x] 3.1 Complete root and fresh independent local reviews, fixing all P0/P1/P2 findings.
- [x] 3.2 Run targeted/full suites serially, source imports, pinned lint/type, strict OpenSpec and staged diff checks before publication.
