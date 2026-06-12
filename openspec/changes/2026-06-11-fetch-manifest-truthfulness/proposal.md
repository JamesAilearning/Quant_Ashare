# Proposal: fetch-manifest-truthfulness

## Why

The audit (docs/audit_rebase_20260611.md, C1-C4) found four ways the fetch
layer can claim more than the dump actually holds:

1. **Boundary-year freeze (C1, the P0 residue).** Resume skips any existing
   `(ticker, year)` file. A file fetched mid-year is never revisited: a later
   run with a wider `--end-date` skips it, and the manifest still advances
   coverage to the new end (`units_written > 0` from OTHER units) — "complete,
   but the file stops at an old date". P3-6a's `--refresh-current` papers over
   the DAILY cadence by blindly re-pulling the whole final year every run
   (wasteful: ~17.5k calls/day even when current; and useless for past-year
   truncation, manual runs, or a dump left idle across a year boundary).
2. **Manifest self-destruction (C2).** Every CLI failure path —
   merge refusal, manifest-write OSError, hard abort, corrupt-at-start —
   called `clear_manifest()`. The narrower-scope refusal exists to PRESERVE
   out-of-range hole records; answering it by deleting those records turns
   fail-loud into fail-forget.
3. **Disjoint-range union (C3).** `merge_manifest` min/max-unions coverage:
   prev `[2000,2010]` + cur `[2020,2025]` → "complete `[2000,2025]`" with a
   nine-year never-fetched gap and zero holes.
4. **Empty-string sentinel (C4).** The "" (coverage-not-established) sentinel
   participates in lexicographic min/max ("" sorts first and sticks forever)
   and in the narrower-scope comparison (spurious refusal).

## What Changes

- **Freshness rule (fetcher).** An existing `(ticker, year)` file is skipped
  ONLY when its `max(trade_date)` reaches everything this run can expect of
  it: `last_weekday(min(end_date, Dec-31-of-year))`, further bounded by the
  ticker's listing window (listed-after / delisted-before the slice ⇒ an
  empty placeholder is the truthful content; delisted mid-slice caps the
  expectation at the delist date). Stale, suspicious-empty, or unreadable
  files are re-pulled for the whole year — ONE API call, same cost as
  fetching a single day. A failed re-pull keeps the old file and records a
  hole; the file is still stale, so the next run re-attempts it with no extra
  bookkeeping. `--refresh-current` now governs only the aggregates
  (stock_basic / namechange / suspend_d); the per-ticker final-year blind
  re-pull is retired (the rule subsumes it, and a same-day crash re-run now
  resume-skips already-current files instead of re-pulling ~17.5k units).
- **Scan scope.** The FINAL requested year is always freshness-scanned
  (~5.8k × 3 single-column parquet reads ≈ seconds). A PAST year is
  re-scanned unless its whole expected slice lies INSIDE the previous
  manifest's per-endpoint attested (start, end) range — both ends checked
  (codex P1: an end-only watermark would silently trust never-attested
  years before the coverage start on a backward backfill) — with no
  watermark (no manifest — e.g. the current production dump) every year is
  scanned, and the new `--verify-all-years` forces the sweep (suspected
  external mutation, or a pre-rule manifest whose coverage may over-claim).
  Prior-manifest holes pierce the scan scope via the existing force-retry
  wiring.
- **Verified coverage (codex P2).** A file the freshness rule POSITIVELY
  confirms complete counts as `units_verified` — established coverage on
  par with written units, in `TushareFetchResult`, the manifest schema
  (additive field, tolerant read), `build_manifest`, and the merge's
  extension rule. Without this, the first sweep over an already-complete
  dump would write empty coverage and the build gate would reject a
  genuinely complete dump. Blind watermark/resume skips still establish
  nothing.
- **Manifest red line (01 CLI).** No failure path deletes the manifest,
  ever: merge refusal / write failure / hard abort / corrupt-at-start all
  exit 1 with the manifest left byte-for-byte intact (each with an
  explanatory error). The ONLY clear is the new explicit `--reset-manifest`
  flag. Safety note: a kept stale manifest can at worst over-record holes
  (re-attempted and self-healed next run) or under-claim coverage; the
  exit-1 already stops the orchestrated build (EXIT_FETCH_HARD), so no gate
  consumes a fresher-than-manifest dir silently.
- **Merge truthfulness (fetch_manifest).** (a) Disjoint coverage merge is
  refused — unioning ranges separated by a never-fetched gap (> 1 calendar
  day) fabricates coverage; adjacent/overlapping ranges merge as before.
  DATE-SCOPED endpoints only, like the narrower-scope guard (codex P2:
  stock_basic re-fetches the whole universe regardless of dates, so a
  non-overlapping refresh must not fail its merge). (b) "" is treated as
  "no value" in the min/max helpers and skips the narrower-scope
  comparison. (c) An endpoint that ran but established nothing (wrote 0,
  holed 0, verified 0) preserves the prior record verbatim instead of
  "self-healing" holes nothing re-attempted.

## Invariant (the acceptance bar)

After any completed run, coverage + holes together reflect the REAL state of
every `(ticker, year)` unit in the scanned scope: a unit is either current
through its expected end, or recorded as a hole. "Manifest says complete but
some ticker's file stops at an old date" is impossible within the scanned
scope, and the scope rules guarantee the final year is always scanned and
past years are scanned at least once (first run / no-watermark / sweep).

## Reconciliation with P3-4b merge semantics (checked, no conflict)

The self-heal premise — "an endpoint present in current re-attempted every
missing unit" — is STRENGTHENED: stale files now re-attempt wherever scanned,
and force-retry still pierces unscanned years for recorded holes. The
narrower-scope refusal is unchanged (now minus the "" false positive). The
"coverage reflects what was actually fetched" rule (codex P1-B) is unchanged;
skipped-because-fresh units genuinely satisfy the requested range, which is
exactly what the old exists-skip could not guarantee.

## Non-Goals

- No trading-calendar integration (the last-weekday floor accepts bounded
  re-fetch churn during CN holiday weeks; it converges when the next bar
  lands and never skips real data).
- No per-unit coverage bookkeeping in the manifest (the freshness rule reads
  the files themselves; the manifest stays endpoint-granular).
- No change to the build/recommend gates (P3-4c) or the orchestrator's stage
  flow (P3-6a) beyond the documented `--refresh-current` narrowing.
