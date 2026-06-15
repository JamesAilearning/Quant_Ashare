# Proposal: benchmark-total-return

## Why

Audit E2, now CONFIRMED by exact value match (PR-E Step 0, real tushare):
the backtest benchmark is the CSI 300 PRICE index. The bundle's `sh000300`
close on 2025-12-31 is 4629.939453, byte-identical to tushare `000300.SH`'s
4629.9395. Strategy returns include reinvested dividends (adjusted closes),
so benchmarking against a price index — which excludes them — overstates
excess return by roughly the index dividend yield. The CSI 300 TOTAL-RETURN
index `H00300.CSI` closed 6826.62 on the same day (ratio 1.474 over the
2018-2025 window ≈ 47% cumulative dividend drag); the overstatement is the
audit's predicted ~2-2.5pp annualized.

Two mechanism defects compound it:
- The benchmark bars came from a one-off xlsx via
  `scripts/ingest_sh000300_benchmark.py`, written POST HOC into the LIVE
  bundle — the daily-update atomic swap erases them on the next rebuild.
- That script targeted the legacy non-PIT bundle and carried the price
  index.

## What Changes

- **`src/data/pit/benchmark_index_ingest.py`** (new): pure transform +
  writer. A tushare `index_daily` frame → qlib bins (`close` load-bearing;
  OHLC fall back to close for total-return indices that publish close only)
  + an idempotent entry in a SEPARATE `instruments/benchmark.txt` (NOT
  `all.txt` — the benchmark must stay out of the stock training universe),
  calendar-aligned (intra-span gaps forward-filled, series ends at the last
  published date), written into a CALLER-PROVIDED bundle dir.
- **`scripts/data_pipeline/07_ingest_benchmark.py`** (new, operator-run):
  fetch the CSI 300 price (`000300.SH` → `SH000300`) and total-return
  (`H00300.CSI` → `SH000300TR`) series via tushare and ingest them into
  `--provider-dir`. Fail loud on an empty frame.
- **Orchestrator wiring** (`src/data_pipeline/daily_update.py`): a new
  `benchmark` stage runs after 05 against the SAME staging dir, so the
  benchmark instruments survive the atomic swap (rebuild order
  02→05→03→04→07→06).
- **Retire** `scripts/ingest_sh000300_benchmark.py` (+ its test): xlsx,
  legacy bundle, writes-into-live.
- **Config docs**: `benchmark_code` stays `SH000300` for now (flipping it
  before the bundle carries `SH000300TR` would break every backtest with
  zero benchmark rows); a comment documents that `SH000300TR` is the
  canonical benchmark, switched at REGEN.

## Deferred to REGEN (documented, not in this PR)

Flipping the default `benchmark_code` → `SH000300TR`, running the real
benchmark fetch+ingest against the rebuilt bundle, and re-baselining
(expected excess-return downward revision ~2-2.5pp, recorded). REGEN already
rebuilds the bundle and re-baselines after PR-C/D/E/F; the benchmark switch
folds into it atomically. PR-E ships the verified mechanism; REGEN activates
it.

**REGEN benchmark-switch checklist** (flip these ATOMICALLY so no path
keeps the price index):
- `config.yaml`, `config/presets/{default,production,smoke}.yaml`,
  `config_walk.yaml`, `config_smoke.yaml` — `benchmark_code` → `SH000300TR`.
- `src/core/walk_forward/config.py` dataclass default (consumed when a WF
  config omits the field).
- `src/core/canonical_backtest_contract.py` example default in the docstring.
- Regression baselines: `tests/regression/fixtures/
  walk_forward_baseline_config.yaml`, `tests/regression/test_fold0_baseline.py`
  — regenerate against `SH000300TR` (their numbers move).
- Move `H00300.CSI` from `07 --best-effort` to mandatory once it is the
  canonical benchmark (a missing total-return index must then block the
  swap, since backtests now depend on it).
- Quantify the 2 H00300 gap days against the actual fold windows before
  relying on the metric (now mooted by ffill, but re-confirm).

## Validation

The operator script was run against real tushare into a THROWAWAY copy of
the bundle calendar: `000300.SH` ingested 1942 days / 0 gaps (close 4629.939
exact), `H00300.CSI` 1942 days / 2 intra-span gaps (close 6826.62 exact; the
2 calendar days it does not publish are forward-filled, see below). Confirms
the schema, the close-only fallback, and the calendar alignment against live
data — the part mocks cannot prove.

**Source-flip is numerically inert (full-series diff).** Re-ingesting
`SH000300` from tushare `000300.SH` replaces the xlsx-sourced live bundle
`sh000300` under an UNCHANGED `benchmark_code`. Diffed over the WHOLE
2018-2025 window (1942 days, 0 missing on either side): max relative delta
**5.9e-8** (float32 ULP), max absolute delta 2.4e-4 on a ~5047 level, zero
dates above 1e-4 relative. The xlsx→tushare source flip does not move any
existing `SH000300` backtest number — it is float32-equivalent.

**Self-review fixes folded in (pre-push 3-skeptic pass):**
- Intra-span gap days are FORWARD-FILLED, not left NaN. A NaN benchmark
  close makes qlib fabricate a 0% return on the gap day AND drop the true
  cross-gap move on the recovery day (`report.py` `.fillna(0)` +
  `Ref($close,1)` pulling the NaN) — empirically 2.96% vs the true 3.96%
  on a one-gap series. ffill carries the level so the gap reads a true 0%
  and the recovery carries the real move (price index has 0 gaps → no-op).
- The benchmark stage is fail-loud but the TOTAL-RETURN index is
  best-effort (`07 --best-effort H00300.CSI`): its index entitlement is
  often separate from the equity endpoints, and the canonical benchmark is
  still the price index until REGEN, so a missing total-return index must
  not block the daily swap. The price index stays mandatory; zero indices
  ingested is still a loud failure.
- No `$factor` bin is written for the benchmark (equities carry none; the
  benchmark read path never adjusts by factor) — symmetric, and keeps the
  `backtest_runner` "benchmark has close but no factor" invariant true.
- `ohlc_degenerate` (renamed from the misleading `close_only`) is derived
  from the SOURCE before the OHLC-from-close fallback, so the real
  `H00300.CSI` (genuine OHLC except its last day) is not mislabelled.

## Non-Goals

- No default `benchmark_code` flip / no re-baseline (REGEN).
- No real fetch in CI (the operator/REGEN runs it; unit tests use synthetic
  frames + mocked tushare).
- No benchmark-artifact-contract (`benchmark_artifact_publisher`) rework —
  that orphaned CSV/manifest path is separate (audit H2).
