## Context

`_scan_year_freshness` credits an existing file when its maximum date reaches
the expected end. It neither checks the requested head nor validates all dates
or the year partition. The loop already clips requests, loads listing windows
and the exchange calendar, preserves holiday-only files, and sends refetches
through the year overwrite guard. The merged #482 delta is the latest year-file
contract even though it has not yet been folded into the baseline spec.

## Goals / Non-Goals

**Goals:** positively reuse only files spanning both expected session bounds;
avoid fabricated verification from malformed dates; preserve the existing guarded
refetch and calendar/window distinctions across all three per-year endpoints.

**Non-Goals:** internal missing-session checks, suspension classifications,
vendor-response certification, new leading-shortfall thresholds, provenance
schema changes, automatic historical repair, production writes or training.

## Decisions

1. Add a first-session expectation symmetric to the existing last-session helper.
   Clip by requested year slice and listing window, then ceil to the first actual
   calendar session. Only an unavailable calendar (`None`) uses the weekday
   approximation; an empty calendar remains authoritative no-session evidence.
   Do not infer placeholder truth from an absent first boundary alone.
2. Keep the freshness verdict and trailing recheck return contract unchanged.
   Add the expected first bound at its sole loop caller; the existing tail gives
   the requested partition year. Blind watermark, holiday-only and clean
   window-miss placeholder paths remain ahead of the content-bound check.
3. Replace the max-only reader with one min/max read that requires every date
   (after the existing raw-value string normalization) to be eight ASCII digits,
   a real calendar date and in the expected year. Empty, unreadable, missing or
   invalid dates cannot establish positive coverage; never drop bad rows.
   Do not demand equality: valid wider same-year history is reusable.
4. Select a guarded requested-slice refetch for either short edge or invalid
   dates. Keep the existing overwrite guard, API parameters, result schema and
   post-fetch trailing systemic checks unchanged. A short head followed by a
   valid last date is not a new build-blocking policy; a covering fetch may still
   return vendor-incomplete content, which this change does not certify.
5. Validate listing-date normalization before adding weekday ceiling arithmetic.
   Invalid individual dates become unknown; a reversed real-date pair becomes
   an entirely unknown window. This preserves the documented conservative
   fallback instead of crashing on a new `strptime` or inventing a no-data miss.
6. Migrate existing positive-reuse fixtures to include their synthetic calendar's
   real first session. Do not globally change the fake calendar or weaken old
   overwrite/tail-shortfall assertions merely to make tests green.
7. Before the per-year endpoint reads units or requests its calendar/data, reject
   non-ASCII or impossible requested start/end dates with TushareFetcherError.
   The new first-date arithmetic must not leak an uncaught ValueError through
   the CLI. Keep global config construction and other endpoint contracts unchanged.

## Risks / Trade-offs

- IPO/long suspension or closed weekdays can cause extra head-short refetches →
  listing clipping and calendar ceiling reduce false positives; unavailable
  calendar keeps the existing conservative approximation, not invented holidays.
- A shape-valid but truncated calendar or false prior watermark remains trusted →
  disclose the limit and retain explicit `--verify-all-years` scan control.
- Validating all unique dates adds bounded work per already-read annual file →
  read only trade_date once for positive verification; no additional API calls
  on wider valid reuse. Heavy validation remains serial.
- An earlier incomplete vendor response can still have a complete run manifest →
  distinguish reuse eligibility from post-fetch certification; no new official
  completeness claim or implicit leading-shortfall gate.

## Migration Plan

Record synthetic failures, implement the narrowed freshness contract and migrate
fixtures/current guidance. Run targeted data-pipeline and required full tests,
imports, pinned lint/type, strict OpenSpec and two independent final reviews.
Publish one PR and wait for latest-head Codex and all CI before merging. No live
data recovery is performed by this change.
