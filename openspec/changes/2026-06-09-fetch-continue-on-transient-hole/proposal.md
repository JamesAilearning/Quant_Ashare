# Proposal: fetch-continue-on-transient-hole

## Why

`TushareFetcher.fetch` (the production raw-data fetch, driven by
`scripts/data_pipeline/01_fetch_tushare.py`) aborts the ENTIRE run the moment
any single call exhausts its retryable retries: `_safe_call` raised
`TushareFetcherError` on rate-limit / network / 5xx exhaustion, and that
propagated through the per-`(ticker, year)` loops up to the CLI. A full backfill
is tens of thousands of per-`(ticker, year)` calls over many hours; a single
transient blip on call 40 000 discarded the run's forward progress and forced a
restart. Resume re-skips already-written files, but the operator still has to
notice the abort and re-launch.

Ops Phase 3 drives this fetch unattended on an incremental-daily cadence (P3-6),
where a mid-run abort that needs a human to re-launch is exactly the failure mode
to remove. But resilience must NOT cost loudness: a fetch that silently dropped
some units and exited `0` would let a HOLEY dump masquerade as complete, and the
downstream builder would bake a survivorship-corrupted bundle from it.

## What Changes

- `src/data/tushare/fetcher.py`:
  - `FetchHoleError` — raised by `_safe_call` on retryable EXHAUSTION (replacing
    the old `TushareFetcherError` abort); the recoverable signal a per-endpoint
    loop turns into a recorded hole. A NON-retryable `TushareClientError`
    (token / permission / param) is still re-raised and aborts the run fast — it
    would fail identically on every remaining unit.
  - `FetchHole` (dataclass: endpoint / unit / reason_class / attempts /
    last_error) + `TushareFetcher.holes` — the in-memory hole ledger, reset per
    `fetch()`. `last_error` is bounded + token-free (the client is the secrets
    boundary). In-memory only; persistence to a fetch manifest is P3-4b.
  - Every per-endpoint loop (per-`(ticker, year)` `daily` / `adj_factor` /
    `daily_basic`, per-status `stock_basic`, single-call `namechange` /
    `suspend_d`, per-index `index_weight`) catches `FetchHoleError`, records the
    unit, and continues. `index_weight` leaves the index file unwritten on a
    holed year-chunk (a partial one-file-per-index would be skipped by
    file-existence resume and never filled).
  - A per-`(ticker, year)` endpoint whose prerequisite `stock_basic` holed THIS
    run (incomplete ticker universe) skips with a recorded `prerequisite` hole
    instead of hard-aborting on `_load_ticker_universe` — otherwise a transient
    `stock_basic` blip in the all-endpoints run would take the hard-abort path
    and the continue-on-hole promise would not hold for `stock_basic`. A
    `stock_basic` never fetched at all (no hole this run) still hard-aborts (a
    real usage error).
  - The retry / backoff schedule and the `_is_retryable_error` classifier are
    UNCHANGED — only the terminal action on exhaustion changes (abort → hole).
- `scripts/data_pipeline/01_fetch_tushare.py`: after the summary, a non-empty
  `fetcher.holes` prints a per-endpoint hole report (extracted as
  `_log_hole_report`) and returns exit code `3`; a clean run still returns `0`.
  The same report is emitted on the hard-abort path too, so holes accumulated
  before an abort are never silently lost. Continue-on-error is always on; the
  non-zero exit (plus the P3-4c downstream gate) is what keeps a holey dump from
  being trusted.

## Non-Goals

- No hole PERSISTENCE — the on-disk `fetch_manifest.json` is P3-4b. This step
  keeps holes in memory and surfaces them via exit code + log only.
- No downstream consumer gating (the builder / daily-list refusing a holey
  dump) — that is P3-4c.
- No change to resume (still per-file existence), to the retry / backoff
  schedule, or to the retryable / non-retryable classifier.
- No `--allow-holey-fetch` escape hatch yet — kept + stamped in P3-4c, where
  "build-with-holes" and "publish-list-with-holes" are two separate, conscious
  opt-ins.
