## Why

A narrow aggregate refresh can replace a broader raw file before the manifest
merge runs. With a hole-free prior manifest the merge can even report the old
broad coverage as complete after the file has been shortened; with prior holes
it can refuse only after the destructive write.

## What Changes

- Guard replacements of existing namechange, suspend_d, index_weight and
  trade_cal files inside the fetcher, before any data request for that unit.
- Require the effective request to contain the endpoint's previously declared
  coverage, including coverage records with holes; refuse narrowing with the
  stable unsafe-overwrite hole and preserve file bytes.
- **BREAKING**: replacing an existing aggregate without usable prior coverage
  hard-fails, rather than inventing provenance from a failed narrow attempt.
  Recovery requires inspection/backup and a separate staging rebuild.
- Preserve first-time writes, no-write resume and dry runs, stock snapshots,
  year-file guards, response checks, and existing manifest/result schemas.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `v2-ashare-survivorship-correction`: an additive pre-write aggregate range
  guard, with explicit unknown-provenance and retry semantics.

## Impact

Fetcher, focused synthetic tests, CLI help/documentation and OpenSpec. No model,
strategy, qlib metric, provider or production-data operation. This protects
declared ranges, not undetected corruption or historical content that was never
truthfully represented by the existing endpoint-level manifest. Per-year
left-bound freshness and vendor response completeness remain separate work.
