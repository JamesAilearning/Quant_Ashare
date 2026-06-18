# v2-ashare-survivorship-correction Specification

## Purpose
TBD - created by archiving change 2026-06-08-guard-corrupt-adj-factor-in-bin-builder. Update Purpose after archive.
## Requirements
### Requirement: Bin builder SHALL reject a non-finite or non-positive adj_factor

The qlib bin builder SHALL refuse to write bins when any `adj_factor` in its RAW
source that would scale a price is non-finite (`inf` / `NaN`) or non-positive
(`<= 0`), because such a factor multiplies silently into the OHLC columns —
`inf` produces `inf` prices, `0` zeroes them, a negative factor sign-flips them,
and a `NaN` yields a wrong / unadjusted price — corrupting the production bundle
in a way that only surfaces much later as nonsensical features. The check SHALL
validate the RAW `adj_factor` source BEFORE any forward-fill / fill-to-`1.0`, so
that a present row whose factor is corrupt (including a raw `NaN`) is caught
rather than masked by the fill. A date that is simply ABSENT from the source (no
row) is NOT corrupt: it SHALL fall through to the no-adjustment default (`1.0`)
and SHALL pass. On violation the builder SHALL raise an explicit error naming the
ticker, the offending trade date(s), and the bad value(s), and SHALL write no
bundle. A source whose factors are entirely finite and strictly positive SHALL
build unchanged.

#### Scenario: a corrupt adj_factor aborts the build
- **WHEN** a ticker's raw `adj_factor` source contains a non-finite (`inf` /
  `NaN`), zero, or negative value on a present row
- **THEN** the builder raises an explicit error and writes no bundle
- **AND** the error names the ticker, the offending trade date, and the value

#### Scenario: a date absent from the adj source fills to 1.0 and is accepted
- **WHEN** a trading day has NO row in the `adj_factor` source and is filled to
  `1.0` (no adjustment)
- **THEN** the guard does not raise and the price is left unadjusted for that day

#### Scenario: a clean adj_factor builds unchanged
- **WHEN** every `adj_factor` value is finite and strictly positive
- **THEN** the guard does not raise and the bins are identical to the pre-guard
  output

### Requirement: Raw fetch SHALL continue past a transient hole and exit non-zero

The Tushare raw-data fetch SHALL NOT abort the whole run when a single unit
exhausts its retryable retries — where a unit is a per-`(ticker, year)` `daily` /
`adj_factor` / `daily_basic` call, a per-status `stock_basic` call, a
`namechange` / `suspend_d` call, or a per-index `index_weight` index, and the
fetch is `scripts/data_pipeline/01_fetch_tushare.py` → `TushareFetcher.fetch`. It
SHALL instead record that unit as a hole — naming the endpoint, the unit, the
retry-failure class, the attempt count, and a bounded token-free last-error
string — and continue to the next unit. A NON-retryable error (token / permission
/ malformed parameter) SHALL still abort the run fast, WITHOUT recording holes,
because it would fail identically on every remaining unit. When the run finishes
with one or more holes, the CLI SHALL print a per-endpoint hole report and exit
NON-ZERO, so a holey dump is never mistaken for a complete one; a run with no
holes SHALL exit zero. Resume SHALL remain per-file-existence so a re-run fills
only the missing units. The retry / backoff schedule and the retryable /
non-retryable classifier SHALL be unchanged — only the terminal action on
retryable exhaustion changes.

A per-`(ticker, year)` endpoint whose prerequisite `stock_basic` holed THIS run
(leaving the ticker universe incomplete) SHALL skip with a recorded prerequisite
hole rather than hard-abort on the missing universe — otherwise the prerequisite
failure would take the hard-abort path and the continue-on-hole behavior would
not actually hold for `stock_basic`. A `stock_basic` that was simply never
fetched at all (no hole this run) SHALL still hard-abort, so a skipped
prerequisite remains a loud usage error. Holes accumulated before ANY hard abort
SHALL still be reported, so no recorded hole is silently lost.

#### Scenario: a transient-exhausted unit is recorded and the run continues
- **WHEN** one unit's call exhausts all retryable retries (rate-limit / network /
  5xx) while other units in the same endpoint succeed
- **THEN** that unit is recorded as a hole (endpoint, unit, class, attempts,
  bounded token-free last error) and `fetch()` does not raise
- **AND** the remaining units are still fetched and written

#### Scenario: a non-retryable error aborts the run fast
- **WHEN** a call fails with a non-retryable error (token / permission / param)
- **THEN** the run aborts fast (the error propagates), no hole is recorded, and
  no further retries are spent

#### Scenario: a finished-with-holes run exits non-zero
- **WHEN** the fetch reaches the end of its endpoints with one or more recorded
  holes
- **THEN** the CLI prints a per-endpoint hole report and returns a non-zero exit
  code

#### Scenario: a clean run exits zero
- **WHEN** the fetch completes with no holes
- **THEN** the CLI returns exit code zero and the dump is complete

#### Scenario: holes from multiple endpoints accumulate in one run
- **WHEN** more than one endpoint records a hole within a single fetch run
- **THEN** the end-of-run hole ledger contains every endpoint's holes — an
  earlier endpoint's hole is NOT wiped by a later endpoint, so the CLI's single
  end-of-run report and exit code reflect the whole run, never just the last
  endpoint

#### Scenario: a stock_basic hole skips dependents instead of hard-aborting
- **WHEN** `stock_basic` holes (its universe is left incomplete) in a run that
  also includes a dependent per-ticker endpoint (`daily` / `adj_factor` /
  `daily_basic`)
- **THEN** the dependent endpoint is recorded as a prerequisite hole and skipped
  (not hard-aborted), so the run completes-with-holes and the CLI exits non-zero
  (`3`), never the hard-abort code (`1`)
- **AND** a `stock_basic` prerequisite that was never fetched at all (no hole
  this run) still hard-aborts, so a skipped prerequisite stays a loud usage error

### Requirement: Bundle build SHALL refuse a holey fetch and stamp the bundle's fetch integrity

The qlib bin build SHALL refuse to build from an INCOMPLETE tushare fetch unless
explicitly overridden. Incomplete means ANY of: a HOLEY fetch (the P3-4b
`fetch_manifest.json` records a hole on any endpoint); a MISSING manifest (which
cannot confirm completeness, and which P3-4b deliberately leaves when it
invalidates the manifest on a hard abort); OR a manifest that does not record the
endpoints the builder consumes (`stock_basic` / `daily` / `adj_factor`) as fetched
WITH ESTABLISHED (non-empty) coverage — a required endpoint that is absent, OR
present but recorded with EMPTY coverage (a partial `01_fetch_tushare --endpoints …`
run, or a first manifest skipped over a pre-existing dump, which P3-4b records with
empty coverage precisely so this gate can catch it), has no confirmed fetch, so
absence-of-holes alone SHALL NOT be read as complete. On any of these,
`QlibBinBuilder.build` SHALL raise
(an explicit `QlibBinBuilderError` naming the reason) and write no bundle, unless
the operator passes `allow_holey_fetch` (`--allow-holey-fetch`) to build a
research / inspection bundle from partial data. Whether the build is clean or
overridden, it SHALL write a fetch-integrity stamp into the bundle (atomically,
promoted with the bins) recording `built_from_holey_fetch` and, when holey, the
recorded holes — so the downstream recommend boundary can gate on it. This build
override is build-only: it SHALL NOT sanction recommending from the bundle.

#### Scenario: a holey fetch refuses the build
- **WHEN** the fetch manifest in the tushare dir records a hole on any endpoint
  and `allow_holey_fetch` is not set
- **THEN** `build()` raises and writes no bundle

#### Scenario: a missing manifest refuses the build
- **WHEN** there is no `fetch_manifest.json` in the tushare dir and
  `allow_holey_fetch` is not set
- **THEN** `build()` raises (completeness cannot be confirmed)

#### Scenario: a partial fetch missing a required endpoint refuses the build
- **WHEN** the manifest has no holes but a required endpoint is absent (e.g. only
  `stock_basic` was fetched, never `daily` / `adj_factor`), OR a required endpoint
  is present but recorded with EMPTY coverage (skipped over a pre-existing dump),
  and `allow_holey_fetch` is not set
- **THEN** `build()` raises — absence of holes is not completeness when the
  bundle's core inputs were never fetched or their coverage was never established

#### Scenario: a corrupt manifest fails loud as a builder error
- **WHEN** the fetch manifest exists but is unreadable (malformed / non-UTF-8 /
  unknown schema)
- **THEN** `build()` raises a `QlibBinBuilderError` (so the CLI's fail-loud path
  catches it, not an escaping `FetchManifestError`), REGARDLESS of
  `allow_holey_fetch` — corruption is not the partial data the override accepts

#### Scenario: a complete fetch builds and stamps clean
- **WHEN** the fetch manifest is present with no holes
- **THEN** the bundle is built and stamped `built_from_holey_fetch = false`

#### Scenario: an overridden holey build is stamped holey with its holes
- **WHEN** the fetch is holey and `allow_holey_fetch` is set
- **THEN** the bundle is built and stamped `built_from_holey_fetch = true` with the
  recorded holes, so the recommend boundary can still refuse it

### Requirement: Fetch runs SHALL persist coverage + holes to a manifest and self-heal precisely

The Tushare fetch SHALL persist each run's per-endpoint coverage and holes to
`{output_dir}/fetch_manifest.json` — a document carrying `schema_version`,
`fetched_at`, and, per endpoint, `status` / `coverage_start_date` /
`coverage_end_date` / `units_written` / `holes[]` (each hole keeping the P3-4a
`reason_class` / `attempts` / `last_error`). The manifest SHALL be written
ATOMICALLY (temp file + rename) so a crash mid-write never exposes a half-written
manifest — the prior file stays intact until the complete new one is swapped in.
Reading SHALL treat a MISSING manifest as a fresh start (not an error) and SHALL
fail loud on an unknown `schema_version`, a MISSING required field (e.g. the
`endpoints` member or any per-endpoint / per-hole key), a non-object JSON document
(e.g. `[]`), a non-UTF-8 / corrupt-encoding file, or malformed JSON, rather than
parsing an unrecognized / partial shape (which the next merge could treat as "no
prior holes" and erase recorded ones). Every manifest read / merge / write
failure (including a refused narrower-scope merge) SHALL surface through the CLI
as a clean non-zero exit, not an escaping traceback.

Each run SHALL be merged onto the prior manifest: for an endpoint that ran this
run, a hole whose exact `(endpoint, unit)` was re-attempted-and-succeeded SHALL
be REMOVED (self-healed); a unit that still failed SHALL keep its hole with its
attempt count ACCUMULATED across runs; and an endpoint that did NOT run this run
SHALL be preserved untouched. Because the merge matches holes by exact
`(endpoint, unit)`, each hole's `unit` SHALL be STABLE across runs and SHALL NOT
embed the run's date range or first-failing year (which vary run-to-run and would
make a re-failure look like a different unit, so the merge would reset attempts or
drop the prior un-healed hole): an `index_weight` hole identifies the whole index
(`index={code}`), and a single-file endpoint (`namechange` / `suspend_d`) uses a
stable `file` unit — the range each covers lives in the coverage fields, not the
unit. Coverage SHALL reflect what was ACTUALLY fetched,
not what was requested: a run that wrote nothing for an endpoint (every file
skipped by resume — e.g. a wider run that skips a prior narrow aggregate file
like `namechange` / `suspend_d` / `index_weight`) SHALL NOT advance that
endpoint's coverage to its requested range; only a run that wrote data advances
coverage (to the widest range seen). When such a skipped endpoint has NO prior
coverage to keep — the FIRST manifest built over a pre-existing dump — its
coverage SHALL be recorded EMPTY (not the requested range), so a gate cannot
mistake a stale narrow dump for the requested range. The merge SHALL NOT remove a hole that did
not self-heal (that would be a silent partial) and SHALL NOT retain a hole that
did self-heal (that would be a false alarm). A full `clear` SHALL be available
for a fresh rebuild. The manifest SHALL be written on the completed-run path
(with or without holes) and SHALL be skipped under `--dry-run`. Whenever the
fetch has mutated the output dir but the completed-run manifest update did NOT
land — ANY HARD abort (a non-retryable error; e.g. `stock_basic` writes
`active_stocks` then aborts on the delisted call), OR a manifest read / merge /
write failure on the success path — the manifest SHALL be INVALIDATED, so a stale
"complete" manifest never covers a possibly-partial dir; a re-run rebuilds it. The
invalidation is itself fail-loud but non-fatal (if the manifest file cannot be
removed, the CLI warns and still returns non-zero rather than escaping). Self-heal
assumes full-scope runs: a NARROWER-scope re-run of a date-scoped endpoint that still has
full-scope runs: a NARROWER-scope re-run of a date-scoped endpoint that still has
UNRESOLVED holes (one whose `[coverage_start_date, coverage_end_date]` no longer
covers the recorded coverage) does NOT re-attempt every prior hole, so the merge
SHALL REFUSE it (fail-loud) rather than silently drop the out-of-range holes
(`stock_basic` is exempt — it re-fetches the whole universe regardless of
date). This capability SHALL only record — it SHALL NOT gate any consumer (P3-4c)
and SHALL NOT itself drive narrower incremental fetches (P3-6).

#### Scenario: a run writes the manifest atomically with an injectable timestamp
- **WHEN** a fetch run completes and builds its manifest (with an injected
  timestamp for determinism)
- **THEN** the manifest records each endpoint's status / coverage_end_date /
  units_written / holes and is written via temp-file + rename
- **AND** a write whose rename fails leaves the PRIOR manifest intact and valid
  (no half-written file)

#### Scenario: a missing manifest is fresh and a malformed one fails loud
- **WHEN** the manifest file does not exist
- **THEN** reading returns a fresh/empty result, not an error
- **AND** WHEN the manifest carries an unknown or missing `schema_version`, is
  missing a required field (the `endpoints` member or any per-endpoint / per-hole
  key), or is malformed JSON, reading raises rather than silently parsing a
  partial shape

#### Scenario: a self-healed hole is removed and an un-healed hole is kept
- **WHEN** the prior manifest recorded a hole for `(endpoint, unit)` and this run
  re-ran that endpoint
- **THEN** if the unit succeeded this run its hole is removed (self-healed)
- **AND** if the unit still failed its hole is kept with its attempt count
  accumulated, and coverage advances (only when data was actually written)

#### Scenario: merge red line — an endpoint that did not run keeps its holes (no wrong-removal)
- **WHEN** the prior manifest has holes in endpoint A and endpoint B, and this
  run ran ONLY endpoint A
- **THEN** endpoint A's holes are re-resolved from this run, while endpoint B's
  holes are preserved untouched — a hole that did not self-heal is never silently
  removed (no silent partial)

#### Scenario: merge red line — a healed hole does not linger (no false alarm)
- **WHEN** the prior manifest has two holes in one endpoint and this run heals
  one but the other still fails
- **THEN** the healed unit's hole is dropped while the still-failing unit's hole
  is kept — a healed hole never lingers, and a still-failing one is never lost

#### Scenario: merge red line — a narrower-scope re-run is refused (no scope-drop)
- **WHEN** the prior manifest covers a wider date range for a date-scoped endpoint
  that still has UNRESOLVED holes, and this run re-fetches that endpoint over a
  NARROWER range that no longer covers the recorded coverage
- **THEN** the merge raises rather than treating the never-re-attempted
  out-of-range holes as self-healed
- **AND** a same-or-wider range merges normally, a hole-free narrower run is
  allowed (no holes at risk), and `stock_basic` (date-agnostic) is not refused

#### Scenario: coverage reflects what was fetched, not what was requested
- **WHEN** a wider run SKIPS a prior narrow aggregate file (`namechange` /
  `suspend_d` / `index_weight`) because it already exists, writing nothing
- **THEN** the endpoint's coverage stays at the actually-fetched narrow range,
  not the wider requested one — so a downstream gate cannot mistake the skipped
  wider request for fetched data
- **AND** a run that actually wrote data advances coverage to the widest range

#### Scenario: clear resets the manifest for a fresh rebuild
- **WHEN** the manifest is cleared
- **THEN** it is removed and the next read reports a fresh start

### Requirement: The fetch SHALL support refreshing the units a daily update must bring current

The fetch SHALL, when `--refresh-current` (`TushareFetcherConfig.refresh_current`)
is given, ignore resume's exists-skip for exactly: `stock_basic` (both buckets),
the `namechange` / `suspend_d` aggregate files, and the FINAL year of the
requested range for the per-ticker endpoints (`daily` / `adj_factor` /
`daily_basic`). Past years SHALL stay resume-skipped (closed history), and
`index_weight` SHALL NOT be refreshed (one full-range file per index — its
refresh has its own cadence). Without the flag, resume semantics are unchanged.
Re-pulled files keep the atomic write (the old file stays until the new one
lands), so a re-pull that holes leaves yesterday's data on disk AND the hole in
the manifest — the build gate then refuses by default.

#### Scenario: the final year is re-pulled, past years stay skipped
- **WHEN** a refresh-current fetch runs over a dump where every per-ticker file
  exists
- **THEN** only the final-year files are re-fetched and written; earlier years
  are skipped untouched

#### Scenario: the snapshot and aggregates are re-pulled
- **WHEN** a refresh-current fetch runs with stock_basic / namechange /
  suspend_d present on disk
- **THEN** all of them are re-fetched (both stock_basic buckets), and the
  refreshed active_stocks carries TODAY's embedded snapshot_date

#### Scenario: index_weight is not refreshed
- **WHEN** a refresh-current fetch covers index_weight with its files present
- **THEN** they remain resume-skipped with zero API calls

#### Scenario: a prior-manifest hole forces its unit past the exists-skip
- **WHEN** the prior manifest records a hole for a unit whose (stale) file
  exists on disk — e.g. a refresh failure left yesterday's file, and after a
  year boundary the unit is no longer the final year
- **THEN** the fetch re-attempts EXACTLY that unit (the 01 CLI wires the prior
  manifest's holes into the fetcher as force-retry units) while untouched
  siblings stay resume-skipped — so the hole either heals for real or recurs,
  and the merge never wrongly drops a never-re-attempted hole as self-healed

#### Scenario: the snapshot stamp date is injectable per run
- **WHEN** the orchestrator passes `--snapshot-date` (its ONE frozen run date)
- **THEN** stock_basic's embedded snapshot_date carries that date rather than
  the wall-clock date at write time, so a fetch spanning midnight stamps the
  planned date

### Requirement: Fetch failures SHALL carry a structured classification that retry policy keys on

`TushareClient` SHALL classify every failure it wraps into a structured kind
(`rate_limit` / `network` / `server_error` / `auth` / `param` /
`environment` / `unknown`) derived from the RAW underlying failure — the
exception type name plus the vendor's own message — BEFORE any wrapper prose
is added, and SHALL preserve the original vendor error text verbatim in the
wrapped message (no generic "common causes" suffix). Classification
precedence SHALL rank Tushare's specific quota phrases (e.g.
"每分钟最多访问") above auth/permission tokens, because the genuine
rate-limit body also contains "权限" and misclassifying it as `auth` would
abort multi-hour runs on a routine transient.

`TushareFetcher` retryability SHALL key on the structured kind when present:
`rate_limit` / `network` / `server_error` are retryable; `auth` / `param` /
`environment` are non-retryable (operator action, not time); `unknown` is
non-retryable (an unrecognized failure aborts fast and loud rather than
burning the retry budget on every unit). Message-substring matching SHALL be
used ONLY for a `TushareClientError` that carries no kind (legacy / direct
constructions), with the pre-existing substring semantics unchanged.

A non-retryable failure SHALL abort the whole fetch run on the first failing
call — no retry attempts, no backoff sleeps, no hole recorded — preserving
the P3-4a fast-abort contract that the wrapper-prose substring matching had
made unreachable.

#### Scenario: invalid token aborts the run on the first call
- **WHEN** the client wraps a vendor failure whose raw text is a token /
  permission error (e.g. "token无效", "抱歉，您没有访问该接口的权限")
- **THEN** the wrapped error carries `kind=auth`, `fetch()` re-raises it on
  the FIRST call with zero retries, zero backoff sleeps, and zero holes

#### Scenario: the real quota message stays retryable despite containing 权限
- **WHEN** the raw vendor text is Tushare's rate-limit body
  ("抱歉，您每分钟最多访问该接口500次，权限的具体详情访问：…")
- **THEN** the wrapped error carries `kind=rate_limit` and the fetcher
  retries with backoff, recording a hole on exhaustion (P3-4a behavior)

#### Scenario: a legacy unclassified error falls back to substring matching
- **WHEN** `_is_retryable_error` receives a `TushareClientError` constructed
  without a kind
- **THEN** the original substring sets decide retryability, unchanged in both
  directions (bare "rate limit…" retries; bare "token无效" does not)

### Requirement: Benchmark indices SHALL be builder-adjacent staging products

The benchmark index series SHALL be ingested into the bundle as a build
step that writes into the SAME staging dir the rebuild promotes (after the
bin builder, before validation), so the atomic swap preserves them. Writing
benchmark bins POST HOC into the live bundle is prohibited — the daily
update's swap erases them. Each index SHALL be registered idempotently in a
SEPARATE `instruments/benchmark.txt` file, NOT in `instruments/all.txt`:
`all.txt` is the stock TRAINING universe, and a benchmark index there would
make the feature builder train on a non-tradable index and could re-enter
the exchange `codes` set the backtest excludes. The benchmark is read by
explicit `benchmark_code` via `D.features`, which resolves its bins
regardless of universe membership.

The canonical benchmark SHALL be the CSI 300 TOTAL-RETURN index
(`H00300.CSI`, dividends reinvested), because strategy returns include
dividends via adjusted closes and a price-index benchmark overstates excess
return by ~the index dividend yield. The price index (`000300.SH`) MAY be
ingested for reference. A total-return index that publishes close only SHALL
have its OHLC fields filled from close. Intra-span calendar days the index
does not publish SHALL be FORWARD-FILLED from the last published level, not
left NaN — qlib turns a NaN benchmark close into a fabricated 0% return and
drops the true cross-gap move, so ffill preserves a true 0% on the gap day
and the real move on the recovery day. The written series SHALL END at the
last published date and SHALL NOT extend (forward-fill) to the calendar
tail: when the index lags the calendar (its latest row not yet printed),
fabricating trailing closes would silently turn an incomplete fetch into 0%
benchmark returns over days it never published. No `$factor` bin SHALL be written for
a benchmark instrument (equity-symmetric; the benchmark read path uses
`$close` only).

#### Scenario: the benchmark survives a rebuild + swap
- **WHEN** the daily update rebuilds into staging and atomically swaps
- **THEN** the benchmark index instruments are present in the live bundle
  afterward

#### Scenario: the benchmark stays out of the training universe
- **WHEN** a benchmark index is ingested
- **THEN** it is registered in `instruments/benchmark.txt`, NOT
  `instruments/all.txt`, and `D.instruments("all")` does not contain it,
  while `D.features([benchmark_code], …)` still resolves its bins

#### Scenario: a total-return close-only series ingests
- **WHEN** the source frame carries close but no intraday OHLC
- **THEN** `$open`/`$high`/`$low` are written equal to `$close` and the
  instrument loads with a consistent level (no NaN on a published day)

#### Scenario: an intra-span gap is forward-filled
- **WHEN** the index does not publish a calendar trading day inside its
  active window
- **THEN** that day's bin carries the prior published level (a true 0%
  benchmark return), and the recovery day carries the real cross-gap move

#### Scenario: an index lagging the calendar tail ends at its last published day
- **WHEN** `index_daily` returns rows but its latest date is before the
  bundle calendar tail
- **THEN** the benchmark instrument ends at the last published date with no
  fabricated trailing closes, and its registry span reflects that date

#### Scenario: a later index failure leaves no partial benchmark write
- **WHEN** a multi-index ingest fetches the price index successfully but a
  subsequent index's fetch or transform fails fatally
- **THEN** no benchmark bins or registry rows are written for any index
  (validation of all indices precedes the write of any)

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

### Requirement: Delisted registry SHALL list one row per delisted ticker

The delisted registry SHALL contain exactly one row per ticker that has
ever delisted, sourced from Tushare `stock_basic(list_status='D')`. The
registry SHALL NOT contain currently-active tickers, and SHALL NOT model
entities, ticker reuse, or `entity_id` (those concepts do not apply to
A-share).

#### Scenario: registry is built from Tushare delisted bucket

- **WHEN** the registry builder consumes `stock_basic(list_status='D')`
- **THEN** every returned `ts_code` appears exactly once in the registry
- **AND** every row has a non-NULL `delist_date`
- **AND** no row's `ticker` appears in `stock_basic(list_status='L')`

#### Scenario: a currently-active stock is queried against the registry

- **WHEN** a ticker with `list_status='L'` (e.g. `SH600519` 贵州茅台) is
  looked up
- **THEN** the ticker is NOT present in the registry
- **AND** the lookup returns "active, no delist date"

### Requirement: Local bin SHALL contain NaN after a delisted ticker's delist_date

For any ticker in the delisted registry, the qlib bin storage SHALL
contain NaN values for OHLCV and derived fields on every trading date
strictly after `delist_date`. This prevents the "stale local bin"
failure mode where queries on delisted tickers return non-NaN values
from a forward-filled or mis-merged snapshot.

#### Scenario: query the last trading day of a delisted ticker

- **WHEN** a caller queries `D.features([ticker], ['$close'], …)` on the
  ticker's `delist_date`
- **THEN** the returned value is valid (non-NaN)
- **AND** matches the close price on that day

#### Scenario: query a date strictly after delist

- **WHEN** a caller queries `D.features([ticker], ['$close'], …)` on any
  date strictly greater than `delist_date`
- **THEN** the returned value is NaN
- **AND** the bin contains no forward-filled continuation past delisting

### Requirement: PIT contract SHALL forbid absolute adjusted prices as features

The capability SHALL restrict feature expressions to within-ticker ratios
and returns; absolute adjusted prices SHALL NOT be used as features. This
follows from Tushare's `adj_factor` endpoint returning today's snapshot
(non-PIT); within-ticker ratios cancel the as-of-date `adj_factor` in
numerator and denominator and are therefore safe.

#### Scenario: feature expression uses absolute adjusted price

- **WHEN** a feature expression evaluates to an absolute adjusted price
  level (e.g. raw `$close` consumed directly as a model input)
- **THEN** the feature is rejected by the contract
- **AND** the rejection message names within-ticker-ratio / return
  alternatives

#### Scenario: feature expression spans a ticker's delist boundary

- **WHEN** a time-series operator's window would otherwise consume rows
  from before and strictly-after a ticker's `delist_date`
- **THEN** the NaN-after-delist invariant causes the operator's result
  to be NaN at every position whose window crosses the boundary
- **AND** the contract treats any non-NaN result at such a position as
  a validation failure

### Requirement: qlib operator min_periods SHALL be validated against delist boundary

The Stage 6.D validation SHALL exercise real qlib operators
(`Mean($close, N)`, `Ref($close, N)`, `Corr(...)`) — not pandas
`rolling` — against a delisted ticker on days strictly after
`delist_date`. The operator MUST return NaN. Any qlib operator that
silently honours `min_periods < N` is either wrapped with explicit
`min_periods=N` in the expression layer or banned from feature
expressions.

#### Scenario: qlib Mean operator is tested after delisting

- **WHEN** `D.features([ticker], ['Mean($close, 20)'],
  start=delist_date + 1, end=delist_date + 10)` is executed on a
  delisted ticker
- **THEN** every returned value is NaN
- **AND** the test cites design §4.3.2 in the failure message if any
  value is non-NaN

### Requirement: PIT query layer SHALL expose universe-aware queries

`PITDataProvider` at `src/pit/query.py` SHALL expose
`get_universe(date, universe_name)`, `get_universe_range(...)`, and
`get_features(fields, start, end, universe_name, align)`. Queries
SHALL be PIT-correct: no future-listed ticker appears in
`get_universe(date)`; no past-delisted ticker appears; time-series
operations respect the delist boundary via the NaN-after-delist
invariant. The query layer SHALL NOT expose a `resolve_entity` method
(no entity model).

#### Scenario: universe at a past date excludes future listings

- **WHEN** `get_universe(date_X, universe_name)` is called
- **THEN** every returned ticker has `list_date <= date_X`
- **AND** no returned ticker has a populated `delist_date <= date_X`

#### Scenario: a delisted ticker is queried after its delist_date

- **WHEN** `get_universe(date_X, "all")` is called and `date_X` is
  strictly after a ticker's `delist_date`
- **THEN** that ticker is NOT in the returned set

#### Scenario: query layer surface is inspected for resolve_entity

- **WHEN** a maintainer inspects the public API of `PITDataProvider`
- **THEN** there is NO `resolve_entity` method
- **AND** ticker is the stable identifier across the API surface

### Requirement: PIT query layer SHALL use a bounded LRU cache

The PIT query layer SHALL cache feature query results with an LRU
policy and a bounded `cache_max_entries` parameter (default 256).
Unbounded dict caches SHALL NOT be used.

#### Scenario: cache eviction is triggered

- **WHEN** more than `cache_max_entries` distinct
  `(universe_name, start_date, end_date, frozenset(fields))` queries
  have been executed
- **THEN** the least-recently-used entry is evicted
- **AND** the cache size does not exceed `cache_max_entries`

### Requirement: Legacy provider SHALL be preserved untouched

The existing `D:/qlib_data/my_cn_data` provider SHALL NOT be deleted,
overwritten, or retroactively modified by any code under this
capability. The new corrected provider is written to a separate
directory. Both providers remain queryable indefinitely.

#### Scenario: a pipeline script attempts to modify the legacy provider

- **WHEN** any script under `scripts/data_pipeline/` writes to a path
  under the legacy provider root
- **THEN** the contract rejects the operation
- **AND** the script aborts before any byte is written

#### Scenario: a destructive finalization step is requested

- **WHEN** a future migration finalization script is invoked without
  `--confirm-destructive`
- **THEN** the script exits before any destructive action
- **AND** the script supports a `--dry-run` mode

### Requirement: Borrow-shell restructure SHALL NOT be modelled in the price layer

The capability SHALL NOT inject NaN gaps, split a ticker into multiple
"entities", or otherwise discontinue the price series at an A-share
borrow-shell restructure date. A borrow-shell restructure preserves
ticker continuity by exchange convention (reverse-merger asset injection
under the original ticker). Restructure events MAY be annotated for
attribution purposes via the existing `PURPOSE_ATTRIBUTION` enum in
`attribution_industry_loader.py`, but SHALL NOT influence price-series
PIT correctness.

#### Scenario: a borrow-shell ticker is queried across the restructure date

- **WHEN** `D.features([ticker], ['$close'], …)` is called spanning a
  date range that includes a known borrow-shell restructure event
- **THEN** the returned series is continuous (no NaN gap, no split)
- **AND** the close value before the restructure date matches the
  pre-restructure shell's last trade
- **AND** the close value after the restructure date matches the
  post-restructure (renamed, new-asset) entity's trade

#### Scenario: a feature consumer requests restructure annotation

- **WHEN** a feature consumer requests restructure event metadata
- **THEN** the metadata is available only via
  `PURPOSE_ATTRIBUTION` consumers
- **AND** `PURPOSE_TRAINING` consumers cannot access the annotation

### Requirement: Capability SHALL declare out-of-scope dimensions explicitly

The capability SHALL declare the following as Phase E+ backlog and
SHALL NOT silently extend them into Phase A-D scope: **entity model /
ticker reuse modelling** (excluded by construction — A-share has no
ticker reuse), industry classification (Shenwan L1/L2) PIT,
fundamentals (PE / PB / ROE / financial statements) PIT, outstanding
shares / market cap PIT, ST / *ST status snapshots within an active
listing, and risk-model factor exposures.

#### Scenario: a follow-up PR proposes an entity-model field

- **WHEN** any follow-up PR under Phases A-D adds an `entity_id`,
  `reuse_count`, or similar field that splits a ticker's price series
- **THEN** the reviewer rejects the PR
- **AND** cites this requirement and the A-share-no-ticker-reuse rule
  in `docs/pit/pit_universe_design.md`

#### Scenario: a follow-up PR proposes a Phase E+ dimension

- **WHEN** any follow-up PR under Phases A-D adds code dependent on
  historical industry reclassification, fundamentals publication
  dates, share-count snapshots, in-listing ST status, or risk-model
  exposures
- **THEN** the reviewer rejects the PR
- **AND** the work is moved to a dedicated PHASE-E.N ticket

### Requirement: Reference cases YAML SHALL be user-curated and cover the delisting era matrix

The seed entries of `tests/pit/reference_cases.yaml` SHALL be committed
by the user as Phase 0.2 and SHALL cover the delisting era coverage
matrix defined in `docs/pit/pit_universe_design.md`. The seed SHALL
NOT be agent-generated. The count target is a function of coverage
(~8 cases minimum, not a fixed ≥10). Agent additions in Phase A.3 or
later SHALL cite the Tushare API response (`stock_basic`,
`namechange`, or `index_weight` row) in the PR body, per row.

#### Scenario: a PR adds reference rows without per-row citation

- **WHEN** a PR adds new entries to `tests/pit/reference_cases.yaml`
- **AND** any entry lacks a cited Tushare API response in the PR body
- **THEN** the reviewer rejects the PR
- **AND** previously-cited rows on the same PR may stay

#### Scenario: the Phase 0.2 seed is missing

- **WHEN** any Phase A test that depends on `reference_cases.yaml` is
  executed before the user has committed the Phase 0.2 seed
- **THEN** the test fails with a message naming the missing seed file
- **AND** the failure message points to the Phase 0.2 task in
  `openspec/changes/add-ashare-survivorship-correction/tasks.md`

#### Scenario: the seed lacks coverage of a required era

- **WHEN** the committed seed lacks any case from a row of the
  coverage matrix (e.g. no 2024+ post-退市新规 case)
- **THEN** the reviewer of the Phase 0.2 PR rejects the seed as
  incomplete
- **AND** lists the missing era(s) by name

### Requirement: Per-(ticker, year) resume SHALL be content-fresh, not existence-based

An existing per-`(ticker, year)` file (daily / adj_factor / daily_basic) SHALL be resume-skipped ONLY when its `max(trade_date)` reaches the latest
date this run can expect of it: the last actual TRADING day (from the exchange
calendar — the last-weekday heuristic only when the calendar is unavailable) on
or before `min(requested end_date, Dec 31 of the year)`, further bounded by the
ticker's listing window — a slice the window misses entirely expects no data
(an empty placeholder is truthful), and a mid-slice delisting caps the
expectation at the delist date. A file that stops short, is
suspiciously empty (data possible per the listing window), or cannot be read
SHALL be re-pulled for the WHOLE year (one API call) and overwritten on
success. A failed re-pull SHALL keep the old file and record a hole; the
file remains stale, so the next run re-attempts it without extra
bookkeeping. Prior-manifest holes SHALL continue to pierce every skip.

The FINAL requested year SHALL be freshness-scanned on every run. A PAST
year MAY be skipped from scanning only when the previous manifest's
per-endpoint coverage watermark attests everything this run could expect of
it; with no watermark every year is scanned, and an explicit
`--verify-all-years` SHALL force the full sweep.

#### Scenario: a truncated boundary year backfills when the range extends
- **WHEN** a `(ticker, year)` file ends mid-year and a later run requests a
  wider end date
- **THEN** the year is re-pulled in one call and the file reaches the new
  expected end (no more frozen half-years shadowed by exists-skip)

#### Scenario: a complete year file preserves crash-rerun resume
- **WHEN** a re-run encounters a year file already current through its
  expected end (including a weekend end date flooring to Friday)
- **THEN** the unit is skipped with no API call

#### Scenario: tomorrow's run fetches the new day
- **WHEN** today's run wrote the current-year file through today and
  tomorrow's run requests end_date+1
- **THEN** the file is re-pulled and contains the new day's bar

### Requirement: The fetch manifest SHALL never be cleared by a failure path

`fetch_manifest.json` SHALL be deleted ONLY by an explicit operator
instruction (`--reset-manifest`). A refused merge, a manifest write failure,
a hard abort, and an unreadable manifest at run start SHALL each exit
non-zero with the manifest left byte-for-byte as it was, with an error
naming the explicit reset as the deliberate-fresh-start path.

#### Scenario: a refused merge preserves the hole ledger
- **WHEN** a narrower-scope (or disjoint-scope) run's merge is refused
- **THEN** the CLI exits 1 and the manifest bytes are identical to before
  the run

### Requirement: Coverage merge SHALL NOT fabricate or corrupt ranges

Merging coverage ranges separated by a never-fetched gap of more than one calendar day SHALL be refused (unioning them would claim the gap as
covered). The empty-string "coverage not established" sentinel SHALL never
win a min/max comparison nor trigger the narrower-scope refusal. An endpoint
that ran but established nothing (wrote no unit, holed no unit) SHALL
preserve the prior endpoint record verbatim — its holes are not
self-healed by a run that re-attempted nothing.

#### Scenario: disjoint ranges are refused
- **WHEN** the manifest covers [2000, 2010] and a run covers [2020, 2025]
- **THEN** the merge raises rather than recording "complete [2000, 2025]"

### Requirement: stock_basic snapshots SHALL embed their snapshot date

The Tushare fetch SHALL stamp every row of `active_stocks.parquet` and
`delisted_stocks.parquet` with a `snapshot_date` column (`YYYYMMDD`, exactly one
value per file) recording when the snapshot was taken. Downstream staleness /
consistency guards read THIS instead of file mtime, which copies and sync tools
silently rewrite. The stamp date SHALL be injectable for tests
(`TushareFetcherConfig.now`, value-injection) and default to the system date in
production. The column is additive: every existing reader checks required
columns as a subset, so the stamp breaks none of them.

#### Scenario: a fetched snapshot carries the stamp
- **WHEN** `stock_basic` is fetched (with an injected date for determinism)
- **THEN** both written buckets carry a `snapshot_date` column whose single
  distinct value is that date, and the embedded-date reader round-trips it

#### Scenario: existing readers are unaffected
- **WHEN** the builder / universe / registry / ST readers load a stamped file
- **THEN** their required-column subset checks pass unchanged

