## Why

Annual `index_weight` requests still exceed the upstream response limit for
CSI500/CSI800. The audited CSI800 dump contains exactly 7,000 rows in each
historical year and a partial 2025 snapshot, which changes downstream membership.

## What Changes

- Fetch index weights in non-overlapping calendar-month windows, clipped to the
  requested inclusive range, while retaining one atomic parquet per index.
- Treat responses at or above a conservative 6,000-row safety threshold as
  unusable; record the existing stable per-index hole and preserve any old file.
- Add synthetic truncation, boundary, and failure/preservation regressions.
- Document explicit staged repair of legacy dumps; ordinary resume and
  `refresh_current` behavior remain unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `v2-ashare-survivorship-correction`: monthly bounded index-weight acquisition
  and fail-closed handling of potentially truncated responses.

## Impact

Only the Tushare raw fetcher, its tests, and repair instructions change. No
persisted schema, constituent selection policy, production data, model, or
canonical metrics path changes. Smaller requests increase API call count and
continue to use the existing serial rate limiter. This is not a completeness
proof for arbitrary upstream omissions below the safety threshold.
