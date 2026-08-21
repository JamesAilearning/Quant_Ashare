## 1. Read model

- [x] 1.1 Define a pure effective-review projection and stable candidate-state model.
- [x] 1.2 Reject missing or duplicate candidate identifiers; exclude mismatched journal keys.
- [x] 1.3 Cover no, partial, full, corrected, mismatched, blank/non-string-reason malformed-boundary, and HOLD cases.

## 2. Page integration

- [x] 2.1 Render dated review summary and non-execution candidate labels for valid non-HOLD signals.
- [x] 2.2 Keep journal warning, HOLD block, append-only audit table, and artifact validation semantics intact.
- [x] 2.3 Replace Today Workbench's duplicate count helper with the shared read model.
- [x] 2.4 Keep the readable journal audit available when candidate identifiers prevent an exact review projection.
- [x] 2.5 Require strict, forward artifact entry-date evidence before certifying entry-session guidance or rendering review progress.
- [x] 2.6 Apply the shared entry-date validation before Today Workbench classifies a signal as reviewable.

## 3. Verification

- [x] 3.1 Run focused tests, ruff, import/startup smoke tests, and strict OpenSpec validation.
- [x] 3.2 Run required logic/governance tests.
- [x] 3.3 Run local code review and resolve P0/P1/P2 findings.
