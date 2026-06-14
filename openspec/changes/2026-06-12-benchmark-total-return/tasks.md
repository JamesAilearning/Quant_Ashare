# Tasks: benchmark-total-return

## 0. Step 0 — confirmation (real tushare)
- [x] bundle `sh000300` 2025-12-31 close 4629.939453 == tushare `000300.SH`
      4629.9395 (exact) → benchmark IS the price index (audit E2 CONFIRMED).
- [x] `H00300.CSI` total-return reachable, 6826.62 same day; index_daily
      schema captured (close-only for the total-return last day).

## 1. Implementation
- [x] `benchmark_index_ingest.py`: index_daily frame → bins + idempotent
      all.txt, close-only fallback, calendar-aligned, fail-loud paths.
- [x] `07_ingest_benchmark.py`: fetch price + total-return via tushare,
      ingest into `--provider-dir`, fail loud on empty frame; default
      index map `000300.SH:SH000300, H00300.CSI:SH000300TR`.
- [x] daily_update orchestrator: new `benchmark` stage after 05 into staging
      (survives swap); dry-run + rebuild loop + docstring updated.
- [x] Retire `scripts/ingest_sh000300_benchmark.py` + its test.
- [x] Config comments: `SH000300TR` is canonical, switched at REGEN;
      default kept `SH000300` (no live breakage).

## 2. Tests
- [x] `test_benchmark_index_ingest.py` (9): full-OHLC, close-only fallback,
      gap→NaN alignment, registry idempotent-replace, 5 fail-loud paths.
- [x] `test_ingest_benchmark_cli.py` (3): default-map price+total-return
      ingest, empty-frame exit 1, index-map parse.
- [x] `test_daily_update.py`: STAGES includes `benchmark`; plan wires the
      staging `--provider-dir`.

## 2b. Pre-push adversarial self-review (3-skeptic workflow)
- [x] [P1] Intra-span gaps FORWARD-FILLED (was NaN): a NaN benchmark close
      makes qlib fabricate 0% on the gap day and drop the cross-gap move on
      recovery (`.fillna(0)` + Ref-pulls-NaN) — reproduced 2.96% vs true
      3.96%. ffill fixes both; regression asserts gap-day 0% + recovery move.
- [x] [P1] Full-series provenance: live `sh000300` vs tushare `000300.SH`
      diffed over all 1942 days — max rel delta 5.9e-8 (float32 ULP); the
      xlsx→tushare source flip is numerically inert. Documented.
- [x] [P1] Benchmark stage tolerance: `07 --best-effort H00300.CSI` —
      the total-return index (often a separate entitlement) skips+warns
      instead of blocking every daily swap; price index mandatory; zero
      ingested still fails.
- [x] [P2] `close_only` → `ohlc_degenerate`, derived from the SOURCE
      before fallback (real H00300 has OHLC except its last day).
- [x] [P2] No `$factor` bin for the benchmark (equity-symmetric; keeps the
      backtest_runner "benchmark has no factor" invariant true).
- [x] [P2] Annotated the missed defaults: `walk_forward/config.py`,
      `smoke.yaml`, `config_walk.yaml`, `config_smoke.yaml`; REGEN-switch
      checklist enumerated in the proposal.

## 2c. Codex round 1 (PR #243) + CI
- [x] [P1] Codex: ffill no longer extends past the last published date —
      the series ends there (no fabricated trailing closes when the index
      lags the calendar tail); intra-span gaps still ffilled. Regression
      `test_index_lagging_calendar_tail_ends_at_last_published_not_filled`.
- [x] CI (TypeError: must be real number, not dict): a latent
      `engine.py` aggregate-log bug (`%.4f` over the nested `timing` dict)
      surfaced when this PR's CLI test left an INFO handler attached under
      a pytest-randomly order. Fixed the log loop to format non-floats with
      `%s`; pinned with a re-raising-handler regression
      (`test_aggregate_logging_tolerates_nested_dict_with_emitting_handler`);
      neutralized the CLI test's `setup_logging` leak.

## 2d. Codex round 2 (PR #243)
- [x] [P2] best-effort downgrades FETCH-class failures ONLY; a
      transform/contract failure after a successful fetch (duplicate dates,
      null close, calendar mismatch, write bug) is always fatal — never
      ships a price-only benchmark (regression: CLI test with a duplicate-
      date best-effort total-return frame → exit 1).
- [x] [P2] a PUBLISHED row with null/non-numeric close fails loud (corrupt
      source ≠ calendar gap); only dates ABSENT from the source ffill
      (regression: `test_published_row_with_null_close_fails_loud`).

## 2e. Codex round 3 (PR #243)
- [x] [P1] benchmarks registered in `instruments/benchmark.txt`, NOT
      `all.txt` — keeps them out of the `instruments: all` training universe
      (else FeatureDatasetBuilder trains Alpha158 on the index / it re-enters
      exchange codes). Verified through real qlib: `D.features([SH000300])`
      resolves the bins while `D.instruments("all")` excludes it. all.txt is
      never touched by benchmark ingest (regressions updated).

## 2f. Codex round 4 (PR #243)
- [x] [P2] `--index-map` rejects an empty TUSHARE_CODE or QLIB_NAME side
      (e.g. `000300.SH:`) before any provider file is touched — an empty
      qlib name would write bins under `features/` and a blank-code row to
      `benchmark.txt`.

## 2g. Codex round 5 (PR #243)
- [x] [P2] standalone `--end-date` defaults to today (matches the
      orchestrator's run-date), not a stale `20251231` literal that would
      stop the benchmark short of a bundle whose calendar extends past it
      (regression: `test_default_end_date_is_today`).

## 3. Verification
- [x] Real fetch+ingest into a throwaway bundle copy: 000300.SH 1942d/0gap
      close 4629.939 exact; H00300.CSI 1942d/2gap close 6826.62 exact.
- [x] Full fast suite + mypy --strict + ruff.
- [x] docs/audit_rebase_20260611.md E2 closed (mechanism; REGEN activates).
