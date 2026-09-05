## Why

A ticker-year file containing only July through December is currently verified
for a January-through-December request because freshness checks only the maximum
date. That skips missing leading history and can establish unjustified coverage.

## What Changes

- Require existing scanned daily, adj_factor and daily_basic files to span both
  expected trading-session bounds before assigning positive freshness credit.
- Validate every stored date as a real date in the requested year before using
  its minimum/maximum; do not discard malformed rows to manufacture coverage.
- Derive the first expected session from the requested slice and listing window,
  using the existing calendar/weekday-fallback distinction. Treat malformed
  listing dates and reversed listing pairs conservatively as unknown.
- Route short/invalid files through the existing guarded requested-slice refetch;
  retain wider valid cached history, blind watermarks, no-session/window-miss
  distinctions, aggregate guards and existing post-fetch trailing-shortfall policy.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `v2-ashare-survivorship-correction`: two-bound positive ticker-year freshness,
  retaining the already merged non-destructive year replacement requirement.

## Impact

Fetcher helper/caller, synthetic tests and current operator guidance. No new
artifact/schema, provider, model, selection or official metric path. This does
not certify internal missing dates, vendor response completeness or old manifest
truthfulness, and does not repair production data. No new leading-shortfall
systemic threshold or suspension policy is introduced.
