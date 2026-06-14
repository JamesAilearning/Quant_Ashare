# v2-ashare-survivorship-correction Specification (delta)

## ADDED Requirements

### Requirement: Benchmark indices SHALL be builder-adjacent staging products

The benchmark index series SHALL be ingested into the bundle as a build
step that writes into the SAME staging dir the rebuild promotes (after the
bin builder, before validation), so the atomic swap preserves them. Writing
benchmark bins POST HOC into the live bundle is prohibited — the daily
update's swap erases them. The ingest SHALL register each index in
`instruments/all.txt` idempotently (re-ingest replaces the row, updating its
date span, never duplicates).

The canonical benchmark SHALL be the CSI 300 TOTAL-RETURN index
(`H00300.CSI`, dividends reinvested), because strategy returns include
dividends via adjusted closes and a price-index benchmark overstates excess
return by ~the index dividend yield. The price index (`000300.SH`) MAY be
ingested for reference. A total-return index that publishes close only SHALL
have its OHLC fields filled from close. Intra-span calendar days the index
does not publish SHALL be FORWARD-FILLED from the last published level, not
left NaN — qlib turns a NaN benchmark close into a fabricated 0% return and
drops the true cross-gap move, so ffill preserves a true 0% on the gap day
and the real move on the recovery day. No `$factor` bin SHALL be written for
a benchmark instrument (equity-symmetric; the benchmark read path uses
`$close` only).

#### Scenario: the benchmark survives a rebuild + swap
- **WHEN** the daily update rebuilds into staging and atomically swaps
- **THEN** the benchmark index instruments are present in the live bundle
  afterward

#### Scenario: a total-return close-only series ingests
- **WHEN** the source frame carries close but no intraday OHLC
- **THEN** `$open`/`$high`/`$low` are written equal to `$close` and the
  instrument loads with a consistent level (no NaN on a published day)

#### Scenario: an intra-span gap is forward-filled
- **WHEN** the index does not publish a calendar trading day inside its
  active window
- **THEN** that day's bin carries the prior published level (a true 0%
  benchmark return), and the recovery day carries the real cross-gap move

#### Scenario: a missing best-effort total-return index does not block the swap
- **WHEN** the total-return index fetch fails (e.g. separate index
  entitlement) while the price index succeeds
- **THEN** the run warns and continues; the daily bundle still swaps with
  the mandatory price benchmark present

#### Scenario: an empty benchmark fetch fails loud
- **WHEN** `index_daily` returns no rows for a MANDATORY benchmark index
  (or every index fails)
- **THEN** the ingest stops with a non-zero exit rather than writing a
  zero-row benchmark instrument
