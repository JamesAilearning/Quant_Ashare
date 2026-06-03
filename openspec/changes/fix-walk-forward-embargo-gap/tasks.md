## 1. Fold generation (src/core/walk_forward/engine.py)

- [x] 1.1 Add `calendar: Sequence[date]` parameter to
  `_generate_windows`; `run()` passes the qlib trading calendar
  (`D.calendar()`), which is available because `run()` already requires
  qlib initialized before calling it.
- [x] 1.2 Import the gap size from the guard
  (`from src.data._segment_embargo import LABEL_LOOKAHEAD_DAYS`) — never
  hardcode it.
- [x] 1.3 In window generation, pull `train_end` and `valid_end` back to
  the trading day that leaves exactly `LABEL_LOOKAHEAD_DAYS` trading days
  strictly before `valid_start` / `test_start`. Keep `train_start`,
  `valid_start`, `test_start`, `test_end` on their month anchors. The
  gap days belong to no segment.
- [x] 1.4 Edge cases: map non-trading anchors to the first trading day
  `>= anchor` before counting back; skip a fold whose train/valid would
  become non-positive after the pull-back; `gap == 0` reduces to current
  adjacent behavior.

## 2. Guard untouched (red line)

- [x] 2.1 Make NO change to `src/data/_segment_embargo.py`,
  `LABEL_LOOKAHEAD_DAYS`, `_validate_embargo`, or the
  `FeatureDatasetBuilder` embargo wiring. (Verify via `git diff` that
  these files are not in the diff.)

## 3. Tests (tests/logic/, synthetic calendar, no qlib)

- [x] 3.1 For every generated fold, assert
  `validate_segment_embargo(...)` (the guard, reused as oracle) returns
  `[]` — i.e. both boundaries satisfy the embargo.
- [x] 3.2 **Leakage assertion**: the last train row's label-price reads
  `{train_end+1 … train_end+LABEL_LOOKAHEAD_DAYS}` (trading days) have
  empty intersection with `[valid_start, valid_end]`.
- [x] 3.3 Anchors preserved: `valid_start` / `test_start` stay on the
  month-aligned nominal (quarter grid intact).
- [x] 3.4 `gap == 0` path reduces to adjacent (regression guard for
  future zero-lookahead handlers).

## 4. End-to-end re-run (Step 2, separate from the code change)

- [x] 4.1 Run `config_walk.yaml` 23-fold WF on the PIT bundle (GPU);
  confirm folds no longer fail at build; record rolling OOS IC/IR/etc.
- [ ] 4.2 (If time) re-run GP under the same fixed WF for a reproducible
  GP-vs-Alpha158 comparison on current main. — **DEFERRED to C2**: the GP
  miner's `config/factor_mining/default.yaml` fails `load_config` on main
  (dead `features` key), and a fair GP-vs-Alpha158 comparison must run GP
  on the same embargo-gapped clean folds. Both are C2 work; see
  `docs/phase_c1_result.md` §5.
- [x] 4.3 Write `docs/phase_c1_result.md`: root cause, fix (guard NOT
  weakened), embargo-gap test, real 23-fold baseline, GP-vs-Alpha158 (if
  run), and the verdict on whether the old empirical result is credible.

## 5. Validation

- [x] 5.1 `pytest tests/logic tests/governance` green. (2110 passed / 0 failed)
- [x] 5.2 `openspec validate fix-walk-forward-embargo-gap --strict` green.
