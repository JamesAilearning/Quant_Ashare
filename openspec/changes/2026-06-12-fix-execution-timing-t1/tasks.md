# Tasks: fix-execution-timing-t1

## 0. Step 0 — reproduction (read-only diagnosis)
- [x] Full-path synthetic probe (real QlibBinBuilder provider → real
      `BacktestRunner.run` → real qlib backtest): on pre-fix main the single
      day-T signal produced NO position at all — the external restamp shifts
      within the prediction's own date set, so single-day (and every
      fold-final-day) signals evaporate; multi-day signals filled on T+2.
- [x] Touchpoint / IC-consumer / spec-history / test-blast-radius maps
      (3-agent survey; SignalLagTests survive because `_apply_lag`'s
      row-shift mechanics are kept and only the call-site mapping changes).

## 1. Implementation
- [x] Call-site remap: external restamp = `signal_to_execution_lag - 1`
      (lag=1 → none; lag=0 → -1 with loud look-ahead WARNING; `rows < -1`
      rejected). `_apply_lag` docstring rewritten around qlib's built-in
      shift; mechanics unchanged.
- [x] Execution-day mask keying: masked (execution_day, inst) pairs
      translated back to stamps via the qlib trading calendar for BOTH the
      microstructure and ST masks; ST pairs built on execution days
      (attribution now records fill-day dates); stale "EXECUTION date"
      comments rewritten.
- [x] Equal-weight baseline earns dt+1→dt+2 (matches actual fills).
- [x] `EXECUTION_TIMING_SEMANTICS = "lag_total_v2"` in backtest provenance
      AND walk-forward resume fingerprint (no cross-semantics resume).
- [x] Headline IC label-aligned via `entry_offset=1`
      (`close.shift(-(period+1))/close.shift(-1) - 1`); legacy stamp-day
      mean kept as `mean_ic_stamp_day`; `convention` tag; returns fetch
      extended by the entry offset; decay curve documented as
      stamp-anchored; UI aggregate card relabelled (T+1对齐 + tooltip).
- [x] Live-path pin: `_scores_to_inst_map(..., expected_date=as_of_date)`
      rejects stale stamps; recommend timing documented as identical to
      backtest lag=1.

## 2. Tests
- [x] Permanent full-path probes (child-process isolated so the parent
      suite's qlib stays pristine): lag=1 fills exactly T+1; top-scored
      ticker suspended on T+1 never fills.
- [x] `_apply_lag` additions: rows=-1 backward restamp; rows<-1 rejected.
      (Existing SignalLagTests survive unchanged — function semantics kept.)
- [x] Mask integration recalibrated: masking execution day 03-15 drops rows
      STAMPED 03-14; 9 rows reach the strategy with an empty mask (no
      restamp at lag=1).
- [x] IC convention pins: crafted panel reads +1 label-aligned vs -1
      stamp-day through both `analyze()` and `_compute_daily_ic`.
- [x] Live-path date-pin: stale stamp rejected, matching stamp passes.

## 3. Verification
- [x] Full fast suite green in the worktree: 2481 passed, 29 skipped
      (pytest, CI-equivalent), including the recalibrated mask integration
      and all surviving lag/contract/config pins.
- [x] mypy --strict clean on all changed src/web files; ruff clean.
- [x] E2E regression fixtures intentionally NOT regenerated (REGEN batch
      owns it; the ±5% drift gate is RUN_E2E-only so CI stays green).

## 3b. Codex rounds (PR #241)
- [x] Round 1 P2: mask-remap calendar padded 20 calendar days before
      evaluation_start — a prediction stamped on the trading day before the
      window is consumed on the FIRST evaluation day, and that day's mask
      entries must translate back to the pre-window stamp (regression:
      `test_pre_window_stamp_masked_on_first_evaluation_day`).
- [x] Round 2 P1: lag=0 REJECTED at contract + pipeline + walk-forward
      layers (and UI min_value=1) — same-day fills require a backward
      restamp (look-ahead) while the runner stamps every output official;
      `_apply_lag` refuses negative rows as defence in depth; the four
      zero-accept pins rewritten to rejection tests; spec delta scenario
      flipped accordingly.
- [x] CI root cause (exposed by the probe — the first fast test to
      genuinely import qlib.backtest in CI): the unconstrained qlib install
      resolved numpy-2-era wheels, then the project's numpy<2 pin downgraded
      numpy and left scipy>=1.16 importing numpy.lib.array_utils →
      ModuleNotFoundError on 3.11/3.12. Fixed by inlining the numpy/scipy
      bounds into the workflow's qlib install and declaring the
      scipy>=1.10,<1.14 window in pyproject (kept in lockstep with the
      numpy pin).

## 4. Docs
- [x] docs/audit_rebase_20260611.md A1/A3 rows marked fixed by this change.
- [x] Correction record for the archived
      2026-05-08-clarify-signal-execution-lag-semantics premise (this
      proposal's Why; archive untouched).
