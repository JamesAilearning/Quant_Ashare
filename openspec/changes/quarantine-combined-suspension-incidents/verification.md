# Verification and operational boundary

## Regression proof

Before the acquisition fix, `test_combined_exact_observation_has_separate_explicit_authorization` raised `AggregateResponseError: suspension quarantine candidate has unapproved conflict payload`. After the fix, the same synthetic test passed. Logs: `D:/qlib_data/aggregate_range_fix_20260907/logs/combined_{before,after}_20261008_1.log`.

## Local verification on final code

- Two independent read-only final-scope reviews found no P0/P1/P2. They included new untracked test/spec files; all must be committed.
- `pytest tests/logic/ tests/governance/`: 5696 passed, 33 skipped, 23 warnings, 2087 subtests; 296.03s. Supervisor elapsed298.09s, peak616075264 bytes, no abort.
- `pytest tests/data_pipeline/`: 1596 passed, 1 skipped, 1 warning, 94 subtests; 39.83s. Supervisor elapsed40.92s, peak237182976 bytes, no abort.
- Both old-policy compatibility and new synthetic CLI/build/update/serving/refusal paths ran without external data or vendor authorization. Skips/warnings are disclosed, not a warning-free or real-production claim.
- All five changed Python source/CLI/UI modules imported successfully; full Ruff, strict full-tree mypy (244 source files, no incremental cache) and strict OpenSpec gates passed before commit/push.
- Heavy validation was serial, one CPU and numerical threads1, below-normal priority, tree limit8GiB/free-memory floor6GiB. Detailed gates: `D:/qlib_data/aggregate_range_fix_20260907/logs/combined_*_20261008_*.*`.

## Live evidence is not code acceptance

The triggering actual probe is retained at `D:/qlib_data/quarantine_recovery_20261008/policy_probe_20261008T100323_0d920d41`. It FAILED; `independent_response_diagnostic.json` describes saved actual responses and is not a successful new acquisition. New-policy tests use synthetic data, never replay disguised as live Tushare.

## Required post-merge operational gates

- Preserve all prior failed roots and pending/evidence records. Use a separate ordinary raw generation, truthful completed price provenance and real canonical full fetch/update; do not skip fetching or restamp historical scopes.
- Bind real request/run/PID creation identity, terminal report, actual fetch 0 or uniquely qualified 3, complete overall success and exact matching evidence. Query covers both incidents, scope still 20151001–20260922 for the approved baseline.
- Independently validate data fidelity, provider, PIT and serving/refusal paths. Sep22 is stale on Oct8: a genuinely current successor and fresh serving are also required. No clock adjustment, future session fabrication or relaxed freshness.
- Keep existing production models, universe, scores, holdings and cadence. Historical serving checks do not certify historical performance. Both affected securities remain disclosed and excluded, even with zero scored rows.
- Complete matched rollback backups, reviewed reversible publication without replacing held raw-root/registry locks, fixed production-path checks and a first REAL production update. Scheduler restoration is last. The user-paused app heartbeat remains paused.
- Existing UI buttons have no quarantine opt-in. Use explicitly authorized, resource-supervised CLI operations; UI views actual artifacts and warnings. This PR does not add implicit button authorization.

Production is not restored by this PR or its tests. Any new affected payload or retained-key loss requires another explicit decision, not automatic policy switching or another retry.
