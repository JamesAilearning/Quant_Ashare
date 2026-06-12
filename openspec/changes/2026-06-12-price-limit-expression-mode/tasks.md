# Tasks: price-limit-expression-mode

## 0. Step 0 — reproduction
- [x] Probe through the real builder→provider→qlib→BacktestRunner path on
      unfixed code: a top-scored signal FILLED at its +10% limit-up close
      and a rotated-out name SOLD at its -10% limit-down close (both limit
      days non-one-price, so the microstructure mask correctly stayed out
      of the way — the limit check was the only line of defence and it was
      dead). Verified the qlib mechanics in the installed source:
      float mode = `quote_df["$change"].ge/le(threshold)`; expression mode
      = boolean qlib expressions auto-added to quote fields and OR'd with
      suspension.

## 1. Implementation
- [x] Runner translates the contract float magnitude into qlib's
      expression tuple `("$close/Ref($close,1)-1 > thr", "... < -thr")`;
      float mode never reaches qlib.
- [x] `$factor` preflight: loud warning when the provider cannot support
      round-lot simulation (qlib silently degrades to fractional fills in
      adjusted-price mode); diagnostic only, never blocks.
- [x] `CanonicalExchangeConfig.limit_threshold` docstring rewritten
      (magnitude semantics + enforcement note); numeric validation
      unchanged.

## 2. Tests
- [x] Permanent probes: `test_limit_up_buy_is_blocked`,
      `test_limit_down_sell_is_blocked` (rotation sell on the -10% day —
      the name must remain held on and after it).
- [x] Runtime pin: `test_limit_threshold_reaches_qlib_as_expression_tuple`
      asserts the kwargs that reach `qlib.backtest` carry the expression
      tuple (captured off the mocked backtest call).

## 2b. Codex round 1 (PR #242)
- [x] P2: the $factor preflight probes the ACTUAL candidate universe plus
      the benchmark (not the benchmark alone — a factor-bearing benchmark
      must not suppress the warning when traded names lack factor), with
      the strict any-NaN condition mirroring qlib's own trade_unit
      degradation rule.

## 2c. Codex round 2 (PR #242)
- [x] P2: the exchange's quote universe is bounded to the candidate set +
      benchmark (`exchange_kwargs["codes"]`) — without it qlib loads the
      ENTIRE provider and a missing $factor anywhere disables round lots
      for the whole run, making any candidate-scoped preflight untruthful.
      With codes bounded, qlib's degradation scope and the preflight scope
      are provably identical (and the quote load shrinks). Runtime pin
      extended to assert `codes`.

## 2d. Codex round 3 (PR #242)
- [x] P3: the preflight mirrors qlib's EXACT degradation condition —
      ``$factor`` NaN on a row whose ``$close`` is present. A NaN factor on
      suspended/delisted rows (close also NaN) no longer false-fires the
      round-lot warning.

## 3. Verification
- [x] Step-0 ritual: with the runner fix stashed, both limit probes FAIL
      (fills happen); with the fix, the full probe file + runner suite is
      green.
- [x] Full fast suite green; mypy --strict + ruff clean.

## 4. Docs
- [x] docs/audit_rebase_20260611.md A2 closed; A4 (per-board refinement)
      remains open as the documented backlog.
