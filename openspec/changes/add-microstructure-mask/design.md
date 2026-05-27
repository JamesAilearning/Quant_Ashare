## Context

qlib's ``TopkDropoutStrategy`` consumes a predictions ``pd.Series``
indexed by ``(datetime, instrument)`` and picks the top-K at each
rebalance. qlib's ``Exchange`` has a ``limit_threshold`` knob that
caps per-day price moves but does NOT know about per-day
suspension or one-price-lock state. So a strategy can pick a
suspended ticker (zero-volume day) or a one-price-locked ticker
(``high == low``) and qlib will report a phantom fill at the
day's reported close.

Both regimes are CN-specific and well-defined. ``is_trade`` in
qlib bin terms maps to ``volume > 0`` AND ``$close`` is non-NaN;
one-price lock maps to ``high == low`` AND ``volume > 0``
(volume > 0 disambiguates real lock days from pre-listing
zero-data rows that also have equal-but-NaN OHLC).

We can prevent the phantom fill upstream of qlib by dropping the
``(date, instrument)`` rows from the predictions ``pd.Series``
before it reaches ``TopkDropoutStrategy``. The strategy then
never picks those rows — qlib still picks top-K but from the
unmasked subset.

## Goals / Non-Goals

**Goals:**
- Detect every ``(date, instrument)`` in the eval window that is
  in suspension or one-price-lock state, BEFORE qlib's strategy
  rebalances.
- Drop those rows from the predictions Series; ``TopkDropoutStrategy``
  picks top-K from what's left.
- Report a single WARN summarising the per-regime counts so the
  operator sees the magnitude of the correction.
- Reuse existing call patterns: route OHLCV fetch through
  ``PITDataProvider`` when supplied; fall back to direct
  ``qlib.data.D`` (allow-listed under audit P0-6 PIT-bypass
  governance) when not.

**Non-Goals:**
- Do NOT model held-during-suspension or held-during-limit-lock
  behaviour. Once a stock is in the portfolio and then suspends,
  qlib's existing executor handles the carry. The mask prevents
  NEW entries, not stuck exits.
- Do NOT separately handle limit-up vs limit-down (asymmetric
  fillability). A real trader can sometimes sell into an
  upper-limit queue (as a liquidity provider) or buy into a
  lower-limit queue, but the magnitude is operator-dependent
  and not estimable from daily bars alone. Conservative blocking
  on either kind of one-price day is the honest default; an
  operator who wants a more permissive model can extend the
  helper in a follow-up.
- Do NOT add a per-instrument override (e.g. "always allow ST
  stocks even on one-price days"). That's an operator-mode knob,
  not a contract-level decision.
- Do NOT extend the mask to intraday data — the canonical
  backtest is daily-frequency.

## Decisions

1. **Mask runs upstream of qlib's strategy, not inside it.**
   - Decision: ``BacktestRunner.run`` calls
     ``compute_unavailable_mask`` after ``_apply_lag`` and BEFORE
     constructing ``TopkDropoutStrategy``. The predictions ``Series``
     is then filtered with ``apply_mask_to_predictions``.
   - Rationale: qlib's Exchange / Executor doesn't expose a
     per-day-per-instrument blocklist; modifying it would fork qlib.
     Pre-filtering predictions achieves the same outcome with no
     qlib changes and stays within the canonical
     ``qlib.backtest.backtest`` callable contract.
   - Trade-off: a stock that was suspended but later returned and
     was held by the strategy from before the suspension still
     produces a "stuck" position in qlib until the stock resumes
     — that part of the silent-fill problem is delegated to qlib's
     existing executor (which respects volume / NaN). The mask
     only prevents NEW entries.

2. **Detection rules.**
   - ``Suspended``: ``volume <= 0`` OR ``close`` is NaN.
   - ``One-price lock``: ``volume > 0`` AND ``high == low``.
   - Rationale: ``volume > 0`` for one-price disambiguates from
     pre-listing / post-delist rows where all four OHLC are NaN
     (already handled by the suspended branch). For "stuck on one
     real trade" days that are NOT limit-locked, the conservative
     block is still correct: an operator with a topk=50 portfolio
     cannot realistically place a meaningful order on a stock that
     transacted once.
   - Trade-off: a stock that traded once at 10am and then froze
     could in principle be filled if you happened to be at the
     bid. We model this as still-unfillable; the loss of optionality
     is in the conservative direction (smaller universe at decision
     time, no false fills).

3. **OHLCV fetch routes through PIT when available.**
   - Decision: ``compute_unavailable_mask(..., pit_provider=None)``
     fetches via ``PITDataProvider.get_features`` when supplied,
     else direct ``qlib.data.D.features``. Symmetric to
     ``BacktestRunner._compute_equalweight_baseline``.
   - Rationale: same audit P0-6 contract — when a PIT provider is
     present, every consumer should use it; when not, the bypass
     site is allow-listed.
   - Trade-off: the post-delist mask layer adds value here too —
     a delisted ticker whose final-day OHLCV is forward-filled by
     qlib would otherwise pass the one-price check (high == low)
     and look like a limit lock. Routing through PIT zeros out
     post-delist rows so the lock check evaluates on real data.

4. **One WARN per run, not per day.**
   - Decision: a single ``_logger.warning(...)`` after the mask
     is computed, with format
     ``"%d (date, instrument) pairs masked (%d suspended,
     %d one-price-day) — predictions cleaned before qlib strategy.
     Audit P0-3."``.
   - Rationale: per-day WARN floods logs (a 4-year walk-forward
     hits hundreds of suspension days). Per-run WARN matches the
     pattern set by stamp-tax cross-period and PIT-bypass.
   - Trade-off: the operator doesn't see per-day detail in the
     log; they get a count. For deeper inspection the helper
     could expose the full mask, but that's a future enhancement
     (research-friendly artifact). The current contract is "did
     this happen and how often".

5. **Governance test asserts the integration on the canonical path.**
   - Decision: a new ``tests/governance/`` test AST-parses
     ``src/core/backtest_runner.py`` and asserts a Call node to
     ``compute_unavailable_mask`` exists inside ``BacktestRunner.run``.
   - Rationale: a future refactor that "simplifies" the run() by
     removing the mask integration must trip an explicit guard,
     just as the PIT-bypass governance test guards every
     ``D.features`` call. Source-AST grep is robust against
     reformatting / line-number shifts.
   - Trade-off: the test couples to the function name. A
     deliberate rename has to update the test as part of the PR;
     that's the desired friction.

## Risks / Trade-offs

- **Fixture regeneration**. Fold-0 baseline expected metrics
  pre-date this mask; the masked run produces slightly different
  numbers. Same pattern as audit P0-4 (stamp tax) and audit P0-1
  (risk constraints) — bump
  ``annualized_return_absolute`` tolerance from ``0.005`` to
  ``0.010`` once with a clear changelog entry. The fixture itself
  stays as-is.

- **Sparse-trading universes**. ST-tagged stocks routinely have
  one-price-lock days. A strategy that explicitly targets ST
  recovery plays will see its universe shrink after the mask.
  That's the right behaviour — a strategy that depends on filling
  at limit prices is not a strategy a real operator can execute.

- **PIT-bypass governance compliance**. The new module's direct
  ``D.features`` call site MUST be added to
  ``PIT_FEATURES_BYPASS_ALLOWLIST`` in the PIT-bypass governance
  test (audit P0-6, PR #177). This PR ships that update together
  with the new module.
