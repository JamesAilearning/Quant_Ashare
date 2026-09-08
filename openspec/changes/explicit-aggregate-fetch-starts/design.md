## Context

The fetcher replaces whole aggregate files, so its guard compares a requested
interval to the previous endpoint-level manifest. DailyUpdateConfig currently
provides one start to all endpoints and to benchmarks. Production keeps wider
aggregate history than price history. Changing the common start would widen
price acquisition and possibly the next provider calendar.

## Goals / Non-Goals

Goals: explicit independent aggregate starts; consistent guard/API/manifest
bounds; no price or benchmark expansion; offline tests of the actual CLI seam.

Non-goals: automatic provenance-derived widening, interval merging, new end
dates, new refresh cadence, provider identity changes, UI configuration, training,
performance claims or an automatic deployment mechanism.

## Decisions

1. Add optional `namechange_start_date`, `suspend_d_start_date` and
   `index_weight_start_date` fields at the end of both configurations, exposed as
   `--namechange-start-date`, `--suspend-d-start-date`, `--index-weight-start-date`.
   None explicitly means use the existing common start. Three fixed options
   avoid a general untyped mapping CLI, duplicate-key policy and unsupported
   endpoint overrides. Do not add a trade-calendar override: its existing fixed
   exchange start and legacy manifest behavior remain unchanged.
2. Validate supplied overrides centrally in a dependency-light data helper:
   real eight-digit ASCII dates, no later than the run's resolved end. Invalid
   new options fail at CLI/configuration boundaries before any client, metadata
   reset, lock, status or data write. The daily plan rechecks against its frozen
   execution end, including injected test dates. No prior-manifest read grants
   implicit range expansion.
3. A single per-endpoint effective-start resolver feeds aggregate guards and
   actual API calls/monthly windows. Daily plan forwards explicit flags only to
   fetch; the existing common start still drives ticker-year and benchmark work.
4. Extend `build_manifest` with a keyword-only optional endpoint-start mapping.
   The fetch CLI passes the same explicit starts used by the fetcher; default
   callers retain the old common-range behavior. Validate supplied mapping names
   and bounds; persisted schema v1 and outcome DTOs stay unchanged. Blind skips
   still establish no new coverage, and holes carry their actual requested scope.
5. Retain all overwrite/response guards. An explicit start that is still too
   late is refused; an explicit end that goes backward remains refused; unknown
   provenance remains a hard error. Index override does not force refresh.
6. Endpoint coverage cannot advance over retained index files. Before any index
   data call in a write-bearing run, inspect all retained parquet files (including
   unconfigured indices) and actual prior holes. Every old hole must be attempted;
   a retained-file mix is permitted only at the exact trusted prior interval.
   Those configured retained units count as manifest-attested `units_verified`,
   not fresh API or content verification. All-blind-skip remains a no-op; fresh
   all-index writes can establish the requested interval. The manifest builder
   rejects index results that establish coverage over remaining blind skips.
   Duplicate configured index targets are refused before execution.
7. Construct the complete fetch config before any manifest reset, rather than
   duplicating a subset of its validators in the CLI. After reading the
   post-reset manifest, replace only the immutable config's derived retry and
   verified-range evidence. Configuration failures preserve prior provenance.

## Risks / Trade-offs

- Request and manifest disagree → cover actual fetch CLI plus fake API and
  production-shaped previous records, not just isolated plan assertions.
- Invalid new options encounter reset/status writes first → validate before
  reset and at DailyUpdateConfig construction; test zero side effects.
- Operators assume broader history was fetched on resume → document that index
  remains no-write skipped unless explicitly selected by existing retry rules.
- Three new options do not repair raw automatically → perform the separately
  authorized backed-up migration after review; preserve provenance and guards.
- Date-scoped prior holes in other endpoints → retain existing merge refusal,
  never drop those holes to force a green run.
- Mixed per-index outcomes were missed by the original single-index fixture →
  cover three indices, partial writes/skips/failures, unconfigured retained files,
  unselected holes (including missing files) and duplicate targets together.
- A new config constraint was initially checked only after the destructive
  reset → use the actual complete config before reset and exercise invalid
  options with reset on/off and dry-run on/off using the real clear path.

## Migration Plan

Existing callers without overrides keep behavior. After review, deployment uses
the original price start plus explicit starts inspected from its own trusted
aggregate history. The observed layout needs namechange=19900101,
suspend_d=20151001 and index_weight=20000101. These are operational values, not
new repository defaults. Preserve end-date containment and verify the live
manifest again before deployment. Rollback restores the backed-up code/arguments
and matched data generation; it does not reset provenance.

## Open Questions

None for this PR. Formal provider reconstruction, cache/output generation
isolation and maintenance execution are separately validated deployment work.
