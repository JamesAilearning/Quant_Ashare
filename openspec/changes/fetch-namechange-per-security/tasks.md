## 1. Contract and failing regressions

- [x] 1.1 Specify explicit mode, frozen universe, prerequisite and publication boundaries.
- [x] 1.2 Add synthetic regressions and record pre-fix failures.

## 2. Implementation

- [x] 2.1 Implement bounded per-security collection and validated prerequisites in the existing producer.
- [x] 2.2 Propagate and validate mode through fetch/daily-update config and CLI; document migration.
- [x] 2.3 Complete normal, failure, retention, budget and CLI regression coverage.

## 3. Verification and publication

- [x] 3.1 Run targeted/data-pipeline and required logic/governance tests, imports, lint/types and strict OpenSpec validation serially.
- [x] 3.2 Review the final diff locally and fix all P0/P1/P2 findings.
- [ ] 3.3 Publish the focused PR, request Codex review and merge only after clean review and CI.

## 4. Operational acceptance (separate from code completion)

- [ ] 4.1 Preserve current production and protected assets in a new dated recovery root; review the execution helper.
- [ ] 4.2 Rebuild aggregates in isolation and independently verify both retained generations and per-security request coverage.
- [ ] 4.3 Validate the isolated provider, current-data catch-up and unchanged-model workflow without heavy parallel work.
- [ ] 4.4 Publish reversibly, independently verify live data and supervised first update, then restore the original scheduled task only after acceptance.
