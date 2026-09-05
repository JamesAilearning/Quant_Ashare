## Why

The recommendation JSON carries `rebalance_day` and `next_rebalance_date`, but both CSV exports omit them. An operator opening a HOLD-day CSV alone sees ranked, tradable names and an entry date without the existing non-entry distinction.

## What Changes

- Append the existing two cadence fields to both recommendation CSVs for cadence-enabled results, mirroring the same result values used by JSON.
- Keep daily-mode CSV headers/content and JSON schema/picks unchanged. Preserve empty CSVs as header-only tables without fabricated stock rows; the sibling JSON remains the run-level authority.
- Document CSV `False` as HOLD/monitoring-only, blank next anchor as unknown, and the existing terminal/operator HOLD notice. Do not introduce a new action/status field or recompute the schedule.
- Refuse non-boolean cadence markers before writing any artifacts so the newly disclosed boolean field cannot silently become a truthy numeric/string value.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `v2-daily-stock-recommendation`: explicitly extend the CSV cadence projection and clarify the older generation-context requirement's CSV compatibility statement.

## Impact

`src/inference/daily_recommend.py`, synthetic writer tests, and `docs/daily-recommend-runbook.md`. CSV consumers must tolerate the two appended columns in cadence-enabled exports; no in-repo reader parses these CSVs for decisions. JSON consumers, Pipeline/WalkForward shared artifacts, ranking, date resolution, official metrics, production data and deployment are unchanged.
