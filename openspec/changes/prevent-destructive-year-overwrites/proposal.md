## Why

The per-ticker fetcher clips API requests to the operator's date range but replaces the entire ticker-year parquet. A stale or force-retried narrow request can therefore erase earlier/later history, including replacing a populated year with an empty pre-listing slice.

## What Changes

- Refuse a partial-year replacement when existing rows fall outside the requested interval, preserving the old file byte-for-byte and recording a stable ticker-year hole before the data API call.
- Refuse partial-year replacement when existing dates cannot be safely established. Explicit full-calendar-year repair remains available for corrupt year files.
- Explain the required covering interval without silently widening the request or combining old rows with new responses.
- Preserve first-time partial downloads, safe year-to-date replacement, no-write resume skips, and the existing CLI nonzero/hole-ledger failure path.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `v2-ashare-survivorship-correction`: constrain content-fresh per-ticker-year replacement so a clipped refetch cannot destroy out-of-range history.

## Impact

`src/data/tushare/fetcher.py`, synthetic data-pipeline tests, and operator repair documentation. The affected endpoints are `daily`, `adj_factor`, and `daily_basic`. No public signatures or manifest schema fields change; the existing open-string hole reason gains `unsafe_overwrite` with zero network attempts. No dependencies, production artifacts, trading policies, or official metric paths change.

Interval merging, max-only freshness/left-boundary coverage validation, aggregate endpoint replacement, and production history repair are separate follow-ups, not claims of this change.
