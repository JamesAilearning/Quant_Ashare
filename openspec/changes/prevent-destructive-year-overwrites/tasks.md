## 1. Reproduction and contract

- [x] 1.1 Reproduce destructive narrow replacement with synthetic tests across all three per-ticker-year endpoints.

## 2. Protection and recovery

- [x] 2.1 Add the shared pre-write guard and stable unsafe-overwrite holes without changing no-write resume or safe replacement behavior.
- [x] 2.2 Cover corrupt/empty files, forced retries, listing-window misses, continuation, CLI/manifest recovery, and document operator recovery and remaining limitations.

## 3. Verification and review

- [x] 3.1 Run data-pipeline tests, full logic/governance tests, changed-module import smoke, version-matched lint/type checks, and strict OpenSpec validation.
- [x] 3.2 Independently review the final diff and address all P0/P1/P2 findings before publishing the PR.
