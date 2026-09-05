## 1. Acquisition correctness

- [x] 1.1 Add synthetic annual-cap and monthly-boundary regressions; demonstrate failure before the fix.
- [x] 1.2 Implement monthly partitioning and a fail-closed response-size guard, preserving per-index atomicity and resume.
- [x] 1.3 Cover saturated/retryable later months, old-file preservation, other-index continuation, empty responses and dry-run.

## 2. Verification and handoff

- [x] 2.1 Document staged legacy repair and its production/metrics limitations.
- [x] 2.2 Run targeted data tests, full logic/governance tests, import smoke, lint/type checks and OpenSpec validation.
- [x] 2.3 Complete independent local review and address blocking findings before publication.

Before fix: annual-cap regression saved 7,000 rather than 9,600 rows; saturated
responses were published; monthly edge partition assertions failed. All pass
after the fix. Independent review caught an invalid-date/empty-publication
boundary; four synthetic invalid endpoints reproduced it before the follow-up
and now refuse before any API call or index-file write. Re-review found no
P0/P1/P2 issues.

Local suites run serially with one-thread numeric libraries: logic/governance
5,304 passed, 33 skipped, 1,911 subtests; data pipeline 491 passed, 1 skipped,
94 subtests; PIT 34 passed; lightweight regression 3 passed, 3 skipped. The
separate baseline test-accounting fix is the prerequisite PR, not part of this
data change. The full 23-fold replay is left to its dedicated CI job. No live
Tushare fetch, provider rebuild, or model training/cutover was performed.

Final source import smoke, Ruff 0.16.6, mypy 2.3.1 (235 source files),
`git diff --check`, and strict OpenSpec validation all passed.
