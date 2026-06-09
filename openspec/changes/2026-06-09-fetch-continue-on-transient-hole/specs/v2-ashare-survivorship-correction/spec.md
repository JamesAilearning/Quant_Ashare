# v2-ashare-survivorship-correction Specification (delta)

## ADDED Requirements

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
