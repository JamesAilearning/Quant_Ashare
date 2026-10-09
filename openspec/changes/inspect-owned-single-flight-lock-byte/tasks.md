## 1. Canonical owner inspection

- [x] 1.1 Add immutable one-byte snapshots and fail-closed lifetime/owner/identity checks.
- [x] 1.2 Preserve one acquisition body and the existing single_flight contract and cleanup.

## 2. Regression and review closure

- [x] 2.1 Exercise actual OS locks and synthetic unknown/expired/changed/short-read/I/O refusal twins.
- [x] 2.2 Run targeted and required full tests, imports, lint/types and strict OpenSpec validation serially.
- [x] 2.3 Verify the whole-file functional diff and locally review to convergence before publication.

## 3. Publish the bounded API

- [ ] 3.1 Publish this coherent PR and request Codex review against its latest head.
- [ ] 3.2 Verify CI and actionable review closure before the user's authorized merge.

No task in this change starts another data update or relaxes its ledger contract.
