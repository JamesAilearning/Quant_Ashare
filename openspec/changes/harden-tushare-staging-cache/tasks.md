## 1. Tushare Staged Cache

- [x] 1.1 Add request metadata sidecars for staged CSV reuse.
- [x] 1.2 Refetch staged files when metadata is missing or mismatched.
- [x] 1.3 Preserve raw daily and adjustment-factor CSVs when applying instrument filters.

## 2. Pipeline Validation

- [x] 2.1 Reject boolean `PipelineConfig.signal_to_execution_lag` values.

## 3. Tests

- [x] 3.1 Add staged cache regression tests for date-range invalidation.
- [x] 3.2 Add staged cache regression tests for narrow-to-wide instrument scope reuse.
- [x] 3.3 Add PipelineConfig bool lag regression test.

## 4. Verification

- [x] 4.1 Run targeted Tushare provider and Pipeline tests.
- [x] 4.2 Run `openspec validate harden-tushare-staging-cache --strict`.
