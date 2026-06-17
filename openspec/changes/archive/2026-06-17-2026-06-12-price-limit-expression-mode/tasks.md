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

## 2e. Codex round 4 (PR #242)
- [x] P2: ``codes`` is built from the FINAL tradable signal universe
      (post-mask) with the benchmark EXCLUDED — an untraded index (close,
      no factor) or a fully-masked name inside the exchange universe would
      itself disable round lots globally. The benchmark reaches qlib via
      its own ``benchmark`` argument. Empty post-mask universe leaves
      ``codes`` unset (nothing can trade; an empty exchange universe errors
      inside qlib). Runtime pin updated (benchmark absent from codes).

## 2f. Pre-push adversarial self-review (3-skeptic workflow) + pending Codex P1
- [x] Codex P1 (adjusted closes) EMPIRICALLY REFUTED and closed by
      documentation: exchange pre_close ground truth across all 34,597
      adj-factor-jump days 2021-2025 — tushare's factor derives from the
      exchange reference (rounding included), zero missed main-board limit
      closes (243/243 up, 52/52 down), 99.9% divergence < 0.1pp vs the
      0.5pp buffer; 配股 marquee case (600030 2022-01-27) δ=-7.4e-5. Raw
      ratios would diverge by the full event magnitude on every ex-date.
      Residuals documented (ST 重整除权 ×3 — ST is masked; factor
      restatements ≤9, over-block direction only).
- [x] [P1] PRICE_LIMIT_SEMANTICS="close_expr_v1" folded into provenance
      AND the walk-forward resume fingerprint (same config bytes now yield
      different official metrics; cross-semantics resume must invalidate).
- [x] [P1] Governance bypass closed: the preflight imports D unaliased so
      the P0-6 scanner counts it; allowlist bumped to 2 with justification
      + the prescribed "Audit P0-6" WARN at the call site.
- [x] [P2] Resumption-gap hole closed by Not-form expressions
      (`Not(move <= thr)`): NaN previous close ⇒ blocked, where the bare
      `>` form would permit the fill (numpy NaN comparisons are False) —
      probe ticker with a real bar gap pins it.
- [x] [P2] Probe vacuity closed: same-ticker 0%-move control fill asserts
      the limit block is attributable to the limit; stale rotation
      docstring corrected.
- [x] [P2] Empty post-mask universe now fails loud (qlib silently
      substitutes the FULL provider for empty codes — comment corrected,
      raise added, test pinned). Equal-weight baseline's limit-blindness
      documented in Non-Goals.
- [x] [P2] BJ ±30% added to the bias enumerations (contract docstring,
      error text, spec delta).

## 3. Verification
- [x] Step-0 ritual: with the runner fix stashed, both limit probes FAIL
      (fills happen); with the fix, the full probe file + runner suite is
      green.
- [x] Full fast suite green; mypy --strict + ruff clean.

## 4. Docs
- [x] docs/audit_rebase_20260611.md A2 closed; A4 (per-board refinement)
      remains open as the documented backlog.
