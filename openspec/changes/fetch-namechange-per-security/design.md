## Context

PR #493 rejects observable aggregate loss but deliberately retains the legacy
announcement-date name query. Official Tushare documentation permits a query by
`ts_code` without date bounds. Date-filtered queries cannot establish history
whose announcement date is missing. The approved reconstruction needs a decided
security set, bounded requests, and a single publication boundary.

## Goals / Non-Goals

**Goals:** explicit full-history mode, validated listed/delisted/retained-code
union, null preservation, no partial publication, compatible legacy default,
synthetic regression coverage and an operator migration procedure.

**Non-Goals:** a new provider, metric or selection path; arbitrary security
subsets; code remapping; inferring missing announcement dates; old-row fallback;
raw/manifest schema changes; automatic production deployment or scheduler changes.

## Decisions

1. Add `namechange_mode` with exactly `date_range` (default) and
   `per_security_full`, shared validation at FetcherConfig and DailyUpdateConfig,
   and `--namechange-mode` on both CLIs. Unknown values fail before any reset,
   client creation, lock/status write or data operation. The daily plan explicitly
   forwards non-default mode; no automatic fallback on error. Full mode validates
   real ordered operational-envelope dates at configuration, even though the
   API queries have no dates. The old mode and
   suspension behavior remain unchanged.

2. The new mode freezes a sorted union of `active_stocks.parquet`,
   `delisted_stocks.parquet` and retained `all_namechanges.parquet` codes before
   the first name query. Both snapshots must be readable and include every
   producer `STOCK_BASIC_FIELDS` column plus `snapshot_date`, not just the
   fields consumed by the universe selector. They must have unique valid
   six-digit SH/SZ/BJ codes and their correct L/D status, and have the run's
   embedded snapshot date. Neither bucket can be empty (an empty table cannot
   attest its embedded date), and the buckets cannot overlap. A stock-basic
   response at the documented 6,000-row cap is rejected
   as an incomplete prerequisite. Prior stock-basic holes cannot be hidden by
   file existence: require complete prior provenance or both buckets successfully
   refreshed in this fetcher run, and reject any current stock-basic hole.
   Historical codes outside those snapshots are still queried. No suspended or
   never-observed security is silently asserted covered by this declared union.

3. Pure collection calls the existing rate-limited/retried callback once per
   security, with only `ts_code` and the existing six fields. No start/end,
   pagination, offset, code translation or local date filtering. Responses must
   match the requested code and current schema/date/null rules. A schema-bearing
   empty response is valid only if it does not erase known retained history.
   Retained rows are validated before network work and used only for comparison,
   never appended. Preserve the existing five-column business key and allowed
   `end_date` correction; refuse conflicting payloads.

4. Bound the frozen set to 10,000 securities/calls (separate from the suspension
   partition budget), each response below the 10,000-row observed tripwire, and
   cumulative accepted data to the existing 1,000,000 rows / 256 MiB deep-memory
   budgets. These are explicit safety limits, not completeness guarantees or an
   RSS promise. Serial calls reuse existing retry limits; periodic count-only
   progress avoids a long silent loop. Any failure aborts collection without
   publishing earlier responses.

5. Retain pre-request manifest/range guards even though queries are unbounded.
   The v1 coverage interval remains the requested operational envelope, not
   output min/max, a mode certificate, or a vintage/PIT snapshot. Full raw
   history can extend outside it; null announcements remain null. No new inferred
   coverage dates are written. In full mode, a would-be blind resume of an
   existing aggregate is rejected with guidance to refresh (dry-run remains
   read-only); a successful file-hole retry also executes the full collection.
   This prevents claiming the new mode ran merely because an older file exists.

6. The existing aggregate method remains the sole atomic file producer and hole
   mapper. Missing/corrupt prerequisite provenance or unreadable retained files
   hard-abort; current/prior known stock-basic holes and candidate validation
   failures produce a stable `namechange/file` unusable-response hole with zero
   writes. API retry exhaustion keeps its classification; auth/parameter errors
   still abort. No failed candidate advances retained coverage.

7. In full mode, stock-basic refreshes stage L/D responses before either file
   is replaced. Validate raw frame type/size, complete producer columns, bucket
   status, unique valid codes and nonempty/unsaturated rows before stamping;
   validate the complete same-day pair and disjoint codes before publication.
   A skipped counterpart is read and validated without being marked refreshed.
   Any API hole or pair validation failure preserves both prior snapshots and
   records both existing stock-basic bucket units as holes, retaining original
   API classification/attempts and marking withheld peers for retry. No writes
   or coverage advancement are claimed. Only actual fetched buckets are marked
   refreshed, after every pending write succeeds. Stock validation does not
   depend on retained name data or the name-specific call budget. Legacy writes,
   dry-run and no-pending blind skips remain unchanged. Individual file writes
   are atomic, not a two-file transaction: I/O failures hard-abort and are not
   reported as a successful pair refresh.

## Risks / Trade-offs

- Thousands of calls → serial, bounded acquisition; no parallel heavy jobs.
- Stock snapshots omit unknown historical or suspended-only codes → explicitly
  limit the declared universe and compare two retained generations in operational
  acceptance. Do not claim exhaustive vendor history from an API success.
- Vendor corrections other than end date or renamed exchange codes can block →
  preserve evidence, inspect separately; no automatic rewrite of identity.
- A successful code test does not repair live data → isolated raw reconstruction,
  independent key/digest audit, provider and workflow tests precede production.
- Date fields do not certify PIT knowledge → preserve raw values and existing
  consumer semantics, do not report reconstructed history as historical performance.

## Migration Plan

Run local regression/review gates, publish a focused PR, request Codex review,
and merge only after required checks and actionable feedback are clear. Keep the
old production task disabled. Create a new dated recovery directory; do not edit
old frozen helpers or reports. Refresh both stock snapshots and both aggregates
there. Independently compare against old staging AND current live business keys,
validate source completeness and provider fidelity, then catch up current prices
in isolation. Only a separately verified, journaled and reversible publication
can update production and later restore its unchanged schedule. The production
wrapper must explicitly select `per_security_full`.
