## 1. Explicit schedule and timing contract

- [x] 1.1 Confirm user approval, producer/caller inventory, single-path schema v3 design, and non-goals in this change.
- [x] 1.2 Add bounded, provenance-bound Tushare SSE schedule acquisition/read validation and synthetic corruption/date-boundary tests.
- [x] 1.3 Add a shared qlib-free candidate schema/timing contract for strict writer/reader interpretation.

## 2. Canonical candidate generation and migration

- [x] 2.1 Resolve latest closed T and scheduled T+1 before model work, with no future price-calendar extension or online recommendation fetch.
- [x] 2.2 Query known microstructure on T only; preserve feature/model/ensemble/Top50/N5/ST/quarantine semantics and audit unknown entry tradability separately from eligibility.
- [x] 2.3 Publish strictly validated schema v3 JSON and both CSVs; migrate CLI inputs/messages and all affected callers/tests.
- [x] 2.4 Migrate operator readers/current-status/nominal-baseline/command boundaries; retain legacy history without current-candidate or holding promotion.
- [x] 2.5 Update manual runbooks with source acquisition, current/historical candidate meaning, trade-time human checks and rollback boundaries.

## 3. Review and verification

- [x] 3.1 Demonstrate a synthetic regression fails before the fix and passes after; run targeted tests and import checks for every changed source module.
- [x] 3.2 Converge independent local code reviews with no P0/P1/P2 findings.
- [x] 3.3 Run serial full logic/governance tests, lint/type checks and strict OpenSpec validation; inspect final staged diff before commit.
- [ ] 3.4 Publish one coherent PR, invite fresh Codex review after each push, verify latest-head CI/review and merge only under existing user authorization.

## 4. Separate operational acceptance

- [ ] 4.1 After merge, bind a real schedule snapshot and finite genuine candidate/refusal acceptance to the reviewed code without relabeling old fidelity source pins.
- [ ] 4.2 Complete same-point rollback backups, reversible fixed-path cutover, actual after-cutoff production update and candidate verification; restore the data scheduler last.
