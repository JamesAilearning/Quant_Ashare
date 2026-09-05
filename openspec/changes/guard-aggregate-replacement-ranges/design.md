## Context

The fetcher replaces namechange/file, suspend_d/file, index_weight/index={code}
and trade_cal/file before the CLI merges `fetch_manifest.json`. A clean previous
endpoint can retain broad min/max coverage after a narrow replacement. A holey
endpoint can reject the later merge, but the file has already been shortened.
The manifest reader, schema version and EndpointCoverage fields already exist;
no inferred vendor event-date interpretation is needed to protect declared ranges.

## Goals / Non-Goals

**Goals:** refuse unsafe selected replacements before their API calls; protect
both CLI and direct fetcher users; preserve the existing stable-unit and failure
conventions; prevent unknown provenance from being laundered by a failed retry.

**Non-Goals:** row merging, implicit wider downloads, repairs to production,
per-file digest/schema design, retrospective validation of inaccurate manifests,
new namechange date semantics, vendor completeness checks, or per-year freshness.

## Decisions

1. Add one shared fetcher guard after no-write skip/dry-run decisions and before
   each selected existing aggregate's data call. Stock-basic snapshots are not
   date-scoped replacements; ticker-year files retain their separate guard.
2. Lazily load one previous manifest snapshot per `fetch()` through the existing
   reader. Direct endpoint calls use the same guard. Reset the snapshot at each
   new orchestrated fetch. Do not use `assume_verified_ranges`, which deliberately
   excludes endpoints with holes. Preserve the on-disk manifest; only the CLI's
   existing successful completion path writes it.
3. Validate the consumed provenance boundary: integer schema version, known
   endpoint status and ordered, nonempty, real ASCII YYYYMMDD coverage strings.
   A present file with absent/unreadable/malformed manifest, missing endpoint or
   unusable coverage raises `TushareFetcherError` before that unit's API/write.
   This is a hard refusal, not an ordinary hole: a hole-only run records its
   requested range, which could otherwise become false authorization on retry.
4. For known coverage, require the replacement request to contain the previous
   endpoint interval. Use the actual calendar start `TRADE_CAL_START_DATE` for
   trade_cal, not the user-clipped start. Clamp its previous declared start to
   that fixed floor too: manifests record CLI bounds even for a pre-exchange
   start, while actual calendar requests never started earlier. An excluded bound creates an existing
   `unsafe_overwrite` hole with attempts=0 and the existing stable unit; no
   written/verified count, no replacement, no implicit widening. Other units
   may continue. Same/covering intervals keep existing acquisition and response
   validation unchanged, including whole-index monthly publication.
5. First-time missing targets, no-write resume and dry runs remain unchanged.
   Empty existing files are still existing aggregate artifacts and require
   provenance for replacement. No shape inference or silent empty-file exception.
6. Document compatibility: a reset removes metadata, not the old raw files, so
   it cannot authorize replacing provenance-unknown files. Back up/inspect and
   rebuild into a separate empty staging directory; no automated repair here.

## Risks / Trade-offs

- An endpoint-level manifest is not bound to individual file bytes → explicitly
  protect only declared coverage; copied files or already inaccurate provenance
  need a separate acquisition-provenance design, not an invented guarantee.
- Legacy direct callers refreshed without writing a manifest → intentional
  hard refusal; migrate tests/callers to real prior evidence or a fresh staging
  directory, never fabricate production coverage to bypass the guard.
- A narrow known-range refusal can still make the later manifest merge fail
  under its existing hole/disjoint rules → preserve those rules; zero target
  writes before the refusal is the required property, not a forced exit code.
- This is not an all-endpoint transaction or concurrent-writer lock → previous
  safe units may complete before a later hard failure, as in the existing API.

## Migration Plan

Land synthetic direct/CLI regressions, add the guard, migrate refresh fixtures
to explicit prior manifests and update recovery docs. Run targeted data-pipeline
and required logic/governance tests serially, imports, lint/type and OpenSpec,
then independent review. No schema migration or live data action is performed.
