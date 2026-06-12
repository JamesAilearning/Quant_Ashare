# Proposal: fix-execution-timing-t1

## Why

Audit A1 (docs/audit_rebase_20260611.md), reproduced through the real
backtest path in PR-C Step 0: the execution chain has TWO shifts, and the
canonical backtest applied BOTH. qlib's `TopkDropoutStrategy` consumes, on
trade day D, the signal stamped D-1 (`get_step_time(trade_step, shift=1)`)
— a built-in one-trading-day delay — while `BacktestRunner._apply_lag`
restamped signals forward by the FULL `signal_to_execution_lag` on top of
it. The default `lag=1` therefore filled on T+2, so:

- every official backtest traded a one-day-stale signal (systematically
  understating the deployed strategy — live `daily_recommend` enters T+1);
- the suspension/one-price and ST masks, applied to the restamped stamps,
  filtered one day BEFORE the true fill;
- because the restamp shifts within the prediction's own date set, the LAST
  test day's signals of every fold evaporated entirely (the Step-0 probe's
  single-day signal produced no position at all);
- the archived change `2026-05-08-clarify-signal-execution-lag-semantics`
  encoded the wrong premise ("lag=1 currently performs no shift"; "0 SHALL
  mean same-day execution") — under qlib's built-in shift, the unshifted
  default was ALREADY T+1, and same-day was unreachable without a backward
  restamp. This proposal is the correction record for that archive (the
  archive itself is never edited).

Audit A3, same root: the headline `mean_ic_1d` measured corr(score_T,
T→T+1) — a window no lag>=1 strategy can earn — misaligned with both the
Alpha158 training label (`Ref($close,-2)/Ref($close,-1)-1` = T+1→T+2) and
the (now-fixed) T+1 fill.

## What Changes

- **Lag remap** (`src/core/backtest_runner.py`): `signal_to_execution_lag`
  is the TOTAL signal→fill delay. The external restamp becomes `lag - 1`
  rows: lag=1 → no restamp (qlib's built-in shift IS the T+1); lag=N →
  restamp N-1; lag=0 → restamp -1, making "explicit same-day execution"
  REAL for the first time (loud WARNING; research-only look-ahead opt-in).
  `_apply_lag` keeps its row-shift mechanics (now accepting -1) — only the
  call-site mapping changes.
- **Execution-day mask keying**: both the microstructure mask and the ST
  mask now filter by the TRUE execution day (stamp + 1 trading day, per the
  qlib calendar). Masked (execution_day, instrument) pairs are translated
  back to the stamps that would fill on them before
  `apply_mask_to_predictions` (seam signatures unchanged; live-path mask
  semantics untouched). ST attribution records now carry execution dates.
- **Equal-weight baseline** aligned to the same timing (position keyed at
  stamp dt earns dt+1→dt+2, matching the strategy's actual fills).
- **Headline IC label-aligned** (`src/core/signal_analyzer.py`): per-period
  `mean_ic`/`std_ic`/`ir` now use the T+1-entry window (corr(score_T,
  T+1→T+1+period)); the legacy stamp-day mean survives as the
  explicitly-labelled secondary `mean_ic_stamp_day`, and each summary
  carries `convention: label_aligned_t1_entry`. Consumers (walk-forward
  ic_1d/ic_5d, run_catalog headline, Optuna objectives, UI) inherit the new
  meaning through the unchanged `mean_ic` key; the UI card is relabelled.
  The IC decay curve stays stamp-anchored (documented as a research
  diagnostic).
- **Semantics version** `EXECUTION_TIMING_SEMANTICS = "lag_total_v2"` folded
  into backtest provenance fingerprints AND the walk-forward resume
  fingerprint — a post-fix run can never be confused with, or resume from,
  a pre-fix run of the byte-identical config.
- **Live-path pin** (`src/inference/daily_recommend.py`): the score stamp
  must equal the as-of date (a stale `< T` stamp previously passed the
  no-look-ahead guard and would emit an older session's list as today's).
  Live semantics (day-T list, T+1 entry) now coincide with the backtest's
  lag=1 by construction.
- **Permanent full-path probes**
  (`tests/logic/test_backtest_execution_timing.py`, child-process isolated):
  lag=1 fills exactly on T+1 through the real
  builder→provider→qlib→BacktestRunner path, and a ticker suspended on its
  execution day never fills even with the top day-T score.

## Impact on recorded metrics

Backtest return/IR/drawdown move (fills shift T+2→T+1; fold-final-day
signals no longer evaporate) and headline IC values move (convention
change). The committed walk-forward baseline fixture
(`tests/regression/fixtures/walk_forward_baseline_metrics.json`) is
therefore STALE-by-design until the REGEN batch re-runs it under RUN_E2E
(per the master plan, regeneration is aggregated after PR-C/D/E/F); the
drift gate is E2E-gated, so CI stays green. Historical IC numbers quoted in
docs/research records remain labelled with the old convention.

## Non-Goals

- No change to `_apply_lag`'s row-shift mechanics or shape validation.
- No per-board price-limit work (PR-D) and no ST-config unification (PR-F).
- No regeneration of E2E regression fixtures (REGEN batch).
- No re-anchoring of the IC decay curve.
- The live recommend's tradability mask stays computed on T (the T+1 data
  does not exist at decision time — inherent, documented asymmetry).
