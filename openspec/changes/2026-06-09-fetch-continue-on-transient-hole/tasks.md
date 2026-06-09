# Tasks: fetch-continue-on-transient-hole

## 1. Implementation
- [x] Add `FetchHoleError` (raised by `_safe_call` on retryable exhaustion) +
      `FetchHole` dataclass + `TushareFetcher.holes` ledger (reset per `fetch()`).
- [x] `_safe_call`: retryable-exhausted → `FetchHoleError` (was
      `TushareFetcherError`); non-retryable → re-raise unchanged (fast abort).
      `_sanitize_error` bounds the stored `last_error` (token-free).
- [x] Each per-endpoint loop catches `FetchHoleError`, records the unit, and
      continues; `index_weight` leaves a holed index unwritten (resume re-fetches).
- [x] A per-ticker endpoint whose prerequisite `stock_basic` holed this run skips
      with a `prerequisite` hole instead of hard-aborting on `_load_ticker_universe`
      (codex P1); a never-fetched `stock_basic` still hard-aborts. `_add_hole`
      factored out of `_record_hole` for the non-`FetchHoleError` hole source.
- [x] `01_fetch_tushare.main`: report holes (`_log_hole_report`) + return exit `3`
      when `fetcher.holes` is non-empty; `0` when clean; the same report is also
      emitted on the hard-abort path so pre-abort holes are never lost (codex P1).

## 2. Tests
- [x] A per-`(ticker, year)` unit that exhausts retries is recorded as a hole and
      the loop fetches the remaining tickers; `fetch()` does not raise.
- [x] A non-retryable error aborts fast: `fetch()` raises, NO holes recorded,
      no retries (one call).
- [x] `main` returns `3` when the run finished with holes, `0` when clean.
- [x] ANTI-RESET: two endpoints (namechange + daily) each hole in ONE `fetch()`
      run → `fetcher.holes` holds BOTH (the per-fetch reset does not wipe an
      earlier endpoint's holes); the first-recorded hole survives to the end.
- [x] PREREQUISITE (codex P1): a `stock_basic` hole → `daily` skips with a
      `prerequisite` hole (not hard abort), `fetch()` does not raise; via `main`
      the run exits `3`, not `1`. A never-fetched `stock_basic` still hard-aborts
      with no holes.
- [x] Updated the four existing tests that asserted the old abort-on-exhaustion
      (`_safe_call` now raises `FetchHoleError`; the token-leak guard now checks
      the recorded hole's `last_error`). `time.sleep` patched in retry tests.

## 3. Verification
- [x] `pytest tests/data_pipeline/test_fetcher.py` green (existing + new).
- [x] Full fast suite green (2310 passed); `ruff` + `mypy --strict` clean.
