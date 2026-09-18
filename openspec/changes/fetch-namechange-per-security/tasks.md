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
- [x] 3.3 Publish the focused PR, request Codex review and merge only after clean review and CI. PR #494 merged as `5e2c0c9` after clean Codex review and all seven checks.

## 3a. Historical identity compatibility discovered in isolated acceptance

- [x] 3a.1 Specify the exact D/history-only `T600018.SH` exception and add synthetic failing regressions without remapping identities. Before fix: seven targeted cases fail because the historical ID is rejected before the expected name queries (153 pass).
- [x] 3a.2 Apply the same contextual rule to raw D, existing D, retained names and frozen name requests; protect L and unknown identifiers.
- [ ] 3a.3 Run serial targeted/full gates and local final-diff review; publish a focused follow-up PR, request Codex review, and merge only when clean.
- [x] 3a.4 Address PR #495's cross-endpoint finding: prevent generic historical-ID requests without silently omitting in-window data, bypassing existing files or healing unprocessed holes. Before fix: 107 of 116 new scenarios fail; after fix: 116 pass. Data pipeline: 1,141 pass (one skip); logic/governance: 5,504 pass (33 skips); imports, lint, types, strict OpenSpec and two independent local reviews pass. Remote re-review and merge remain gated by 3a.3.

## 4. Operational acceptance (separate from code completion)

- [x] 4.1 Preserve current production and protected assets in a new dated recovery root; review the execution helper. The `production_recovery_20260918` attempt preserved both aggregate baselines and ten model hashes; this is not a complete production rollback backup.
- [ ] 4.2 Rebuild aggregates in isolation and independently verify both retained generations and per-security request coverage.
- [ ] 4.3 Validate the isolated provider, current-data catch-up and unchanged-model workflow without heavy parallel work.
- [ ] 4.4 Publish reversibly, independently verify live data and supervised first update, then restore the original scheduled task only after acceptance.

The first real attempt failed with CLI 3 on the historical D identifier. Both
stock snapshots and name history stayed unchanged. Suspension-only diagnosis
verified 257,566 unique rows, exact replay of 172 responses, and zero missing
keys against each 5,000-row baseline; the whole attempt remains unaccepted.
Keep its evidence immutable. A subsequent retry requires newly reviewed helpers
and a new directory, and must revalidate the complete attempt.
