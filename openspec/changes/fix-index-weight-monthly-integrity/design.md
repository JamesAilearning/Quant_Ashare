## Context

The endpoint returns monthly constituent snapshots. Annual windows are not safe:
800 constituents times twelve snapshots exceeds observed upstream limits. The
file and hole unit are both per index, not per request. Existing resume rules
intentionally do not refresh index weights on each daily run.

## Goals / Non-Goals

**Goals:** prevent the reproduced annual truncation; preserve atomic all-or-hole
publication and stable manifest identity; provide a safe legacy repair route.

**Non-Goals:** changing index membership interpretation, live data promotion,
weight normalization, automatic cache certification, exact constituent-count
assumptions, retraining, or changing canonical evaluation results.

## Decisions

1. Partition by calendar month, clipping both edge months inclusively. Use
   calendar arithmetic, including leap years and year boundaries. Yearly requests
   are demonstrably unsafe; unverified offset pagination is not assumed. Reject
   impossible Gregorian endpoints before partitioning: eight digits alone can
   otherwise collapse into zero windows and falsely publish an empty file.
2. Reject a response with at least 6,000 rows as `unusable_response` through the
   existing `FetchHoleError` path. This is a deliberately conservative safety
   boundary, not a claim that every account's API cap is exactly 6,000. Do not
   retry an identical saturated query or publish preceding successful months.
3. Keep the old file untouched until every requested month succeeds. Preserve
   existing empty-response, per-index hole, resume, and refresh-current contracts.
4. Repair old files explicitly in an isolated raw-data staging directory using
   the complete historical range. Never infer that a cached legacy file became
   trustworthy merely because the fetcher was upgraded.

## Risks / Trade-offs

- More API calls → use the unchanged serial rate limiter; no parallel fetches.
- A large/custom index can exceed the safety threshold → fail closed with a
  visible hole; narrower future support needs its own reviewed change.
- Undetectable upstream omissions below the threshold → this patch does not
  claim universal completeness; staged snapshot/cardinality/weight inspection
  remains necessary before production rebuild.
- Cached bad data remains unchanged → explicit repair documentation and no
  claim of production remediation until the staged data is verified/deployed.

## Migration Plan

Deploy code first. Stage an index-weight-only full-history fetch at a new path;
verify coverage and snapshots before a separate, backed-up production cutover
and provider rebuild. Do not reset or replace the live manifest with the staging
manifest. Rollback before cutover simply discards the staged candidate; preserve
old raw and provider artifacts at cutover for recovery.

## Open Questions

None for this scoped acquisition fix. Automated per-snapshot completeness
certification and production data remediation remain separate work.
