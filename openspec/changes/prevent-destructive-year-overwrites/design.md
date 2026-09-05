## Context

`_fetch_per_ticker_per_year` clips a year to the configured dates and calls `_fetch_ticker_year`, which atomically replaces one parquet. Atomicity prevents partial files, not deletion of out-of-range history. Ordinary stale refetches, prior-hole forced retries, and dirty listing-window-miss placeholders all reach this shared write path.

## Goals / Non-Goals

**Goals:** Prevent clipped replacements from discarding existing history; expose an actionable failure through the existing hole/CLI/build-gate contracts; keep safe fetch/resume behavior.

**Non-Goals:** Interval merging, response completeness validation, freshness/head-coverage redesign, aggregate endpoint fixes, concurrency between independent fetch writers, production data migration, model or trading changes.

## Decisions

1. Check the sole shared ticker-year write path before requesting data. With no old file, allow the request. Read existing contents; a readable empty file (including an old schema-less placeholder) contains no history to lose. Check real YYYYMMDD dates against the request. Known out-of-year rows require manual partition repair, even on full-year requests; never discard them through the corrupt-file exception. Other unreadable/invalid dates refuse a partial-year request, while explicit whole-calendar-year repair retains existing behavior for those corrupt files. Read only one ticker file at a time.
2. Return a concrete refusal reason rather than silently merging old rows or expanding API bounds. Known bounds produce the union of old and requested intervals as recovery guidance, preserving both old history and the newly requested end. Unknown/corrupt dates require backed-up, explicit full-year repair. This is a write-safety rule, not proof that vendor data are complete or genuine.
3. Record `unsafe_overwrite` with the existing `ts_code=... year=...` unit and `attempts=0`; leave bytes and write/verified counts untouched and continue other units. The CLI's existing completed-with-holes exit and manifest mechanism remain authoritative. A pre-existing out-of-scope hole can still cause manifest merge refusal; that must preserve the prior ledger, not be hidden.
4. Keep cached no-write skips and dry-run behavior unchanged. The guard runs only once a refetch is selected, so it does not add scans across every historical ticker file. `force_retry_units` bypasses resume, not this guard.

## Risks / Trade-offs

- Some formerly successful narrow updates now fail explicitly → log the covering request needed; do not use `--reset-manifest` to conceal the hole.
- Invalid date schemas cannot safely establish old bounds → preserve the original and require explicit full-year repair after backup.
- Maximum-date freshness can still overstate coverage before this guard is reached → separate known follow-up; this PR only guarantees non-destructive replacement and does not certify coverage.
- Full-year vendor truncation and `namechange`/`suspend_d`/index aggregate replacement remain possible → explicitly outside this write-guard change and not advertised as fixed.

## Migration Plan

No schema migration or production artifact rewrite. Deploying the code enables the guard on subsequent fetches. An operator may retry an unsafe unit with bounds covering both existing and requested rows; previously lost history needs a separate backed-up rebuild. Reverting the change removes the guard and restores the destructive risk.

## Open Questions

None for this bounded protection. Full interval replacement/merge semantics will be decided separately.
