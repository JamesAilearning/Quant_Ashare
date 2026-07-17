## MODIFIED Requirements

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

The canonical benchmark SHALL be PER UNIVERSE and always a TOTAL-RETURN
index (dividends reinvested): strategy returns include dividends via
adjusted closes, so a price-index benchmark overstates excess return by
~the index dividend yield, and measuring a universe against ANOTHER
universe's basket is a category error whose "excess" mixes alpha with the
basket spread. The canonical map SHALL be: `csi300` and `all` →
`H00300.CSI` (qlib `SH000300TR` — the REGEN-2 baseline basis; re-pointing
either entry is a basis change requiring its own REGEN, not a mapping
edit); `csi800` → `H00906.CSI` (`SH000906TR`); `csi500` → `H00905.CSI`
(`SH000905TR`). The in-code single source SHALL be the runtime map the
LOAD-time check consumes; tracked configs SHALL pair a mapped universe with
EXACTLY its canonical benchmark — the EFFECTIVE benchmark when
`benchmark_code` is omitted included — enforced by a static governance
guard. A config-driven run SHALL carry its universe to the LOAD-time check
so a MIS-PAIRED canonical code (one universe consuming another's canonical
benchmark) is surfaced loud; a caller with no universe falls back to
set-membership surfacing. Every per-universe canonical code SHALL ride the
benchmark ingest DEFAULT index map, so the orchestrated rebuild (which
passes no explicit map) preserves them in a fresh staging bundle; the
orchestrated rebuild SHALL treat EVERY canonical index as MANDATORY
(best-effort downgrade is a manual/standalone affordance only). The price
index (`000300.SH`) MAY be ingested for reference. A total-return index
that publishes close only SHALL have its OHLC fields filled from close.
Intra-span calendar days the index does not publish SHALL be
FORWARD-FILLED from the last published level, not left NaN — qlib turns a
NaN benchmark close into a fabricated 0% return and drops the true
cross-gap move, so ffill preserves a true 0% on the gap day and the real
move on the recovery day. The written series SHALL END at the last
published date and SHALL NOT extend (forward-fill) to the calendar tail:
when the index lags the calendar (its latest row not yet printed),
fabricating trailing closes would silently turn an incomplete fetch into 0%
benchmark returns over days it never published. No `$factor` bin SHALL be
written for a benchmark instrument (equity-symmetric; the benchmark read
path uses `$close` only).

#### Scenario: the benchmark survives a rebuild + swap
- **WHEN** the daily update rebuilds into staging and atomically swaps
- **THEN** the benchmark index instruments are present in the live bundle
  afterward

#### Scenario: every per-universe canonical code survives the default rebuild
- **WHEN** the orchestrated daily rebuild runs the benchmark ingest stage
  with no explicit index map
- **THEN** `SH000300TR`, `SH000906TR` and `SH000905TR` are all ingested
  into the staging bundle as MANDATORY indices (any fetch/entitlement
  failure aborts the update loudly instead of shipping a bundle missing a
  canonical code)

#### Scenario: the benchmark stays out of the training universe
- **WHEN** a benchmark index is ingested
- **THEN** it is registered in `instruments/benchmark.txt`, NOT
  `instruments/all.txt`, and `D.instruments("all")` does not contain it,
  while `D.features([benchmark_code], …)` still resolves its bins

#### Scenario: a tracked config pairing drift fails CI
- **WHEN** a tracked config declares `instruments: csi800` with
  `benchmark_code: SH000300TR`, or omits `benchmark_code` so the in-code
  default applies
- **THEN** the static governance guard fails, naming `SH000906TR` as that
  universe's canonical benchmark

#### Scenario: a mis-paired canonical benchmark warns at load time
- **WHEN** a config-driven run consumes `SH000300TR` for the `csi800`
  universe (e.g. an untracked personal preset)
- **THEN** the LOAD-time canonical-benchmark check warns loud, naming
  `SH000906TR` as the canonical benchmark for that universe, without
  blocking the run

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

#### Scenario: a missing best-effort total-return index does not block a MANUAL run
- **WHEN** a manual/standalone ingest hits a total-return index
  fetch/entitlement failure while the mandatory indices succeed
- **THEN** the run warns and continues with the successfully prepared
  indices — the ORCHESTRATED rebuild is exempt from this downgrade: it
  passes an empty best-effort list, so every canonical index failure
  aborts the daily update loudly

#### Scenario: an empty benchmark fetch fails loud
- **WHEN** `index_daily` returns no rows for a MANDATORY benchmark index
  (or every index fails)
- **THEN** the ingest stops with a non-zero exit rather than writing a
  zero-row benchmark instrument
