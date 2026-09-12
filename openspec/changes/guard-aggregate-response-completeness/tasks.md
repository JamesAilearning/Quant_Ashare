## 1. Contract and reproduction

- [x] 1.1 Validate the scoped proposal and inspect affected producers, consumers and tests.
- [x] 1.2 Add synthetic regression tests and capture their failure before the fix.

## 2. Implementation

- [x] 2.1 Implement bounded suspension partitioning and fail-closed aggregate response validation.
- [x] 2.2 Protect retained and observed business keys before the sole atomic publication.
- [x] 2.3 Migrate real-schema fixtures, caller expectations and acquisition documentation without changing other endpoints.

## 3. Verification and review

- [x] 3.1 Run targeted data-pipeline tests, required logic/governance suites, imports, lint/type checks and OpenSpec validation serially.
- [x] 3.2 Review the complete diff locally to convergence and record remaining operational limitations.
- [ ] 3.3 Publish the focused PR, request Codex review after every push, and merge only after clean review and checks.

### Local verification (2026-09-13)

- Four synthetic loss/saturation cases failed before the source fix and passed after it.
- Final data-pipeline suite: 864 passed, 1 skipped; 51 new aggregate cases included.
- Required logic/governance suite: 5503 passed, 33 skipped. Its 23 warnings include
  pre-existing Windows subprocess decoding warnings also present in the prior
  accepted baseline; no failing test was waived.
- Ruff passed; mypy passed for 238 source files; both touched source modules imported.
- Strict validation of this OpenSpec change passed. Independent final local review
  found no P0/P1/P2; its P3 error-classification wording was clarified in the runbook.
- Checks ran serially with numeric threads limited; full-suite owned peak RSS was
  598,020,096 bytes. No production API, model training or data rewrite was performed.
- Complete vendor name history, isolated production reconstruction, publication and
  task restoration remain separate, unfulfilled operational gates.
