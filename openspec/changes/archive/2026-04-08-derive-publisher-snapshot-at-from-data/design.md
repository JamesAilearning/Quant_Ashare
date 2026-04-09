# Design: derive-publisher-snapshot-at-from-data

## 1. Why fix the publisher rather than relax the loader

Change 4 (`enforce-snapshot-at-matches-artifact-data`) made strict
equality between manifest `snapshot_at` and csv max row date a hard
invariant. It is the **operator-facing point-in-time guarantee**: when
operators see `snapshot_at = X`, they should be able to trust that the
csv's last row date is exactly `X`. Relaxing the loader to allow
`snapshot_at >= max_row_date` (or similar) would re-introduce ambiguity
about freshness and silently undermine the change-4 contract.

The asymmetry is clear: the loader enforces, the publisher must
produce conforming output. Currently the publisher does not. This
change fixes the producer side, leaving the consumer-side invariant
untouched.

## 2. Why derive from rows, not from `end_time`

`end_time` is a **request parameter** controlled by the caller. It
expresses intent ("I want data up to this date") not fact ("the data
actually ends here"). qlib will return whatever trading days exist in
`[start_time, end_time]`, which is generally a strict subset of
calendar days in the same window. Treating `end_time` as if it were
the actual data extent is a category error.

`rows[-1][0]` is the actual ground truth. `_flatten_close_frame`
already sorts rows ascending by date, so `rows[-1]` is the last
trading day in the published artifact. Reading from `rows` is O(1) and
adds no qlib calls.

## 3. Why error out on mismatched explicit `snapshot_at`

If a caller explicitly passes `snapshot_at`, the safest semantics are:

1. Accept it silently and overwrite from data → caller's intent gets
   ignored, surprising.
2. Accept it silently and trust the caller → loader rejects later,
   error appears far from cause.
3. **Validate strictly and reject mismatches → error appears at the
   boundary where the mistake was made.**

Option 3 is the only one consistent with V1 lessons on "fail loud at
the boundary". The error message must include both the requested value
and the actual max row date so the caller can correct without grepping
csv files.

## 4. Why not auto-correct silently

If the publisher silently rewrote a wrong `snapshot_at` to the actual
value, callers would never learn they were passing the wrong value and
the bug would propagate through downstream pipelines that compute
their own snapshot expectations. Loud rejection is cheap; silent
correction is a debt-builder.

## 5. Test strategy

Two new e2e tests, both gated on the local qlib bundle:

- **`test_snapshot_at_is_derived_from_actual_data`**: pass
  `end_time = "2026-02-28"` (Saturday), assert manifest `snapshot_at`
  is `"2026-02-27"`, and assert the round-trip contract is still
  "ok". This case **would fail today** (manifest would say
  `2026-02-28`, loader would reject) and **must pass after the fix**.
- **`test_explicit_snapshot_at_mismatch_raises`**: pass
  `snapshot_at = "2026-02-25"` (a date earlier than the actual max),
  assert `BenchmarkArtifactPublisherError` is raised at the publisher
  boundary, and assert the error message contains both dates. This
  exercises the strict validation path.

The init-guard tests and the existing happy-path round-trip test do
not need to change; they remain valid because they pick `end_time`
values that happen to coincide with trading days.

## 6. Rolling forward

This change closes the publisher → loader symmetry gap that change 4
opened. The next adjacent gap is "publisher does not use a calendar
for the round-trip profile's coverage_ratio" — but that is a quality
improvement, not a correctness bug, and is intentionally out of scope
here. It can be addressed by passing `QlibTradingCalendar()` to the
loader call inside `publish` in a separate, smaller change.

## 7. Risk inventory

- **Risk**: a caller in production was relying on `snapshot_at`
  defaulting to `end_time`. **Likelihood**: very low — the only
  in-repo caller is the e2e test, which uses an `end_time` that
  happens to be a trading day. **Mitigation**: the new behavior is
  strictly more correct; any caller relying on the old behavior was
  in fact broken because their artifacts were silently rejected by
  the loader.
- **Risk**: `_flatten_close_frame` returns rows that are not sorted.
  **Likelihood**: zero — the function explicitly sorts before
  returning (`rows.sort(key=lambda item: item[0])`). We will still
  defensively use `max(row[0] for row in rows)` to avoid coupling to
  sort assumptions in case the helper is later refactored.
