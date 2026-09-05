## 1. Regression evidence

- [x] 1.1 Add synthetic direct-fetcher and repeated-CLI regressions for destructive aggregate replacement and unknown provenance; record failure before implementation.

## 2. Implementation and compatibility

- [x] 2.1 Add the shared pre-request guard for selected existing aggregates, using one validated prior manifest snapshot per fetch run and the actual calendar request interval.
- [x] 2.2 Migrate existing refresh fixtures/callers to explicit provenance and document safe staging recovery without changing schemas or no-write paths.

## 3. Verification and review

- [x] 3.1 Run focused data-pipeline tests, required logic/governance tests, changed-module imports, lint/type checks and strict OpenSpec validation serially.
- [x] 3.2 Review the final diff locally with two independent reviewers, fix all P0/P1/P2 findings and re-review to convergence before publication.
