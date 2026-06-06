# Design — Walk-forward embargo gap in fold generation

## The bug, precisely

`_generate_windows` (pure calendar arithmetic):
```
train_e = train_s + train_months - 1 day
valid_s = train_e + 1 day            # ADJACENT → 0 trading-day gap
valid_e = valid_s + valid_months - 1 day
test_s  = valid_e + 1 day            # ADJACENT → 0 trading-day gap
```
The guard (`validate_segment_embargo`, unchanged) requires
`trading_days_between(train_end, valid_start) >= LABEL_LOOKAHEAD_DAYS`
(strictly-between count). With adjacent boundaries that count is 0 < 2,
so `FeatureDatasetBuilder.build` raises before training on every fold.

## The fix: pull segment tails back, keep anchors

We keep the month-aligned **start** anchors (`train_s`, `valid_s`,
`test_s`) and `test_e` exactly as today (preserves the quarter grid and
the documented 23-fold layout), and move only the two **end** boundaries
back onto the trading calendar so each adjacent pair has the required
gap:

```
gap = LABEL_LOOKAHEAD_DAYS                       # read from the guard
valid_s_eff = first trading day >= valid_s       # qlib's effective start
train_e = the trading day `gap`+1 positions before valid_s_eff
          (⇒ exactly `gap` trading days strictly between train_e and valid_s)
test_s_eff  = first trading day >= test_s
valid_e = the trading day `gap`+1 positions before test_s_eff
```
Concretely, with the calendar as a sorted list and `iv = index of
valid_s_eff`: `train_e = calendar[iv - (gap+1)]`, so
`calendar[iv-gap .. iv-1]` (exactly `gap` days) sit strictly between
`train_e` and `valid_s` — and belong to no segment. Same for `valid_e`
vs `test_s`.

Net effect: the train and valid segments each lose ~`gap` (=2) trading
days off their tail; `test` is untouched; folds stay quarter-aligned.

### Why pull the *end* back rather than push the *start* forward

Pushing `valid_s`/`test_s` forward would drift every fold off the month
grid (re-introducing the misalignment codex flagged on #211). Pulling
the *ends* back keeps `valid_s`/`test_s` on the 1st-of-quarter anchors,
so the fold layout still matches `empirical_results_b_std.md`'s 23-fold
setup. The discarded gap days are the last 1-2 days of train/valid,
which is exactly where the leaking labels lived.

## Why this is leak-free (and the test that proves it)

Alpha158's label at day *t* uses `close` at *t+1, t+2*. The only rows
whose labels could reach past `train_end` are the last `LABEL_LOOKAHEAD_DAYS`
trading rows of train. After the fix, the `gap` trading days immediately
after `train_end` belong to **no segment**, and `valid_start` is `gap`
trading days later. So:

> the label-lookahead window of the last train row (`train_end` +1, +2
> trading days) lands entirely inside the discarded gap, never inside
> `[valid_start, valid_end]`.

**Test** (`tests/logic/`, synthetic calendar, no qlib):
1. Build windows from a synthetic daily calendar.
2. For every fold assert
   `trading_days_between(train_end, valid_start, cal) >= LABEL_LOOKAHEAD_DAYS`
   and likewise `valid_end→test_start` — i.e. `validate_segment_embargo`
   returns `[]` for every generated fold (the guard accepts them).
3. **Leakage assertion**: for the last train row, the set of trading days
   `{train_end+1 … train_end+LABEL_LOOKAHEAD_DAYS}` (the label's price
   reads) has empty intersection with `[valid_start, valid_end]`.
4. Anchors preserved: `valid_start`/`test_start` unchanged vs the
   month-aligned nominal (still quarter-aligned).

The guard's own `validate_segment_embargo` is reused in the test as the
oracle — if the generator and guard ever disagree, the test fails.

## Calendar injection (testability)

`_generate_windows(config, calendar)` gains a `calendar: Sequence[date]`
parameter. `run()` already requires qlib initialized before calling it,
so it passes `[date.fromisoformat(...) for d in D.calendar()]`. Tests
pass a synthetic continuous-business-day calendar. No qlib import is
added to the pure path.

## Edge cases

- **Anchor on a non-trading day** (e.g. `valid_s = 2020-01-01`): map to
  the first trading day `>= valid_s` before counting back — matches how
  qlib clips the query.
- **Not enough calendar history before an anchor** (degenerate tiny
  windows): if `iv - (gap+1) < index(train_s)`, the fold is too short to
  embargo; skip it (same spirit as the existing "partial last fold"
  guard) rather than emit an invalid fold.
- **gap = 0** (a future handler with no lookahead): reduces to today's
  adjacent behavior — no gap inserted.

## Out of scope / unchanged

`_segment_embargo.py`, `LABEL_LOOKAHEAD_DAYS`, `_validate_embargo`,
`FeatureDatasetBuilder` wiring, model params, recommendation, configs.
