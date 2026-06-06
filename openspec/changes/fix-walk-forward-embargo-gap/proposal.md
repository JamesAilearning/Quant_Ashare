## Why

The walk-forward baseline is **completely broken on main**: every fold
fails before training. Root cause —

- `WalkForwardEngine._generate_windows()` builds fold boundaries with
  pure calendar arithmetic where each segment is **adjacent** to the
  next: `valid_start = train_end + 1 day`, `test_start = valid_end + 1
  day` (this has been the logic since the engine was introduced
  2026-04-12).
- The Alpha158 label-lookahead embargo guard
  (`src/data/_segment_embargo.py`, `LABEL_LOOKAHEAD_DAYS = 2`, wired into
  `FeatureDatasetBuilder.build` by #157 on 2026-05-25) **requires ≥ 2
  trading days strictly between** `train_end→valid_start` and
  `valid_end→test_start`, because the Alpha158 label
  `Ref($close,-2)/Ref($close,-1)-1` at day *t* consumes prices at *t+1*
  and *t+2*. Adjacent boundaries (0 trading days between) are rejected.

So since #157, every walk-forward fold is rejected by the embargo guard
at build time (audit Phase A confirmed: 23/23 folds fail, 0.0s each,
before any training).

### Why the old empirical result is suspect

`docs/factor_mining/empirical_results_b_std.md` (#180 2026-05-27, #200
2026-05-29) reports a 23-fold "GP underperforms Alpha158" comparison
with 22/23 valid folds. But the embargo guard (#157, 05-25) predates it
and would have rejected every adjacent fold — so that 23-fold run was
almost certainly executed **before the guard existed**, i.e. on
**adjacent folds with active label leakage** (train's trailing-row
labels peeked into the valid segment's first 2 days). The absolute
numbers (and the "GP loses" conclusion) are therefore **not
trustworthy** and must be re-run on leak-free folds. Producing that
clean baseline is the motivation for re-running after this fix.

## What Changes

- **`WalkForwardEngine._generate_windows()`** SHALL insert an **embargo
  gap of `LABEL_LOOKAHEAD_DAYS` trading days** between adjacent segments,
  by pulling `train_end` and `valid_end` back to the trading day that
  leaves exactly that many trading days before `valid_start` / `test_start`
  respectively. The gap days belong to **no segment** (discarded). The
  month-aligned `train_start` / `valid_start` / `test_start` / `test_end`
  anchors are unchanged, so fold alignment with the quarter grid is
  preserved; only the train/valid segment *tails* shrink by ~2 trading
  days.
- The gap size is **read from `LABEL_LOOKAHEAD_DAYS`** (the guard's own
  constant), never hardcoded — so guard and generator can never drift.
- `_generate_windows` takes the **trading calendar** as a parameter;
  `run()` passes `qlib.data.D.calendar()` (qlib is already initialized
  before this call), and unit tests pass a synthetic calendar — keeping
  the function pure/testable.

### Correctness red line (explicit)

This change **adds a gap; it does NOT weaken the guard.** It MUST NOT,
and does not, touch `src/data/_segment_embargo.py`, lower
`LABEL_LOOKAHEAD_DAYS`, skip/bypass `_validate_embargo`, or ignore the
embargo during fold generation. Weakening the embargo would re-introduce
exactly the label-lookahead leakage Phase B worked to eliminate — from a
different place. The only fix is to generate folds the guard already
accepts.

### Out of scope

Model hyperparameters, the daily-recommendation script, the
`config/factor_mining/default.yaml` dead `features:` key, and any
refactor unrelated to fold generation. This change fixes **only** the
WF embargo-gap fold-generation bug.

## Impact

- Affected spec: **`v2-canonical-runtime-orchestration`** (ADDED
  requirement: walk-forward folds embargo the Alpha158 label lookahead).
- Affected code: `src/core/walk_forward/engine.py` (`_generate_windows`
  + `run` passing the calendar). New tests under `tests/logic/`.
- **Not touched**: `src/data/_segment_embargo.py`, `FeatureDatasetBuilder`
  embargo wiring, model trainer, recommendation, configs.
- After this fix, `config_walk.yaml` can run walk-forward end-to-end on
  the clean PIT bundle, enabling a trustworthy 23-fold OOS baseline.
