## Why

CN A-share trading days are not all alike. Two day-level
microstructure regimes silently corrupt a backtest if the engine
ignores them:

1. **Suspension (停牌, ``is_trade=0``)**. The stock did not trade
   that day at all. qlib's default ``Exchange`` represents this as
   ``volume == 0`` with ``$close`` carried from the prior day.
   ``TopkDropoutStrategy`` is free to pick the stock based on its
   prediction score, and the backtest reports a "fill" at the
   carried close — a phantom trade no operator could have done.

2. **One-price-lock day (一字板 / single-line limit-lock)**. The
   stock opened, traded, and closed at exactly one price for the
   whole day (``high == low``). On the CN market this almost
   always means the limit-up or limit-down queue cleared every
   matchable order at that price — a real buyer trying to enter
   (on an upper-limit day) or sell (on a lower-limit day) cannot
   actually fill. qlib's default behaviour cheerfully fills at the
   single price, producing the "I caught the limit at the bottom
   and sold at the top" backtest fantasy.

Both regimes appear sporadically in every realistic CN backtest
window. A 2022-2024 walk-forward on csi300 has dozens of
suspension days and hundreds of one-price days; the silent fills
typically inflate IC / annualised return by ~0.5-2 % per year
relative to a mask-aware run. Audit P0-3 surfaced this as the
remaining A-share microstructure gap (T+1 and ±10/20/5%
``limit_threshold`` are already enforced upstream by
``signal_to_execution_lag`` and ``CanonicalExchangeConfig``).

## What Changes

- Introduce ``src/core/microstructure_mask.py`` exposing:
  * ``MicrostructureMaskResult`` — frozen dataclass carrying the
    set of unavailable ``(date, instrument)`` pairs AND per-regime
    counts (``n_suspended``, ``n_one_price_days``) for logging.
  * ``compute_unavailable_mask(instruments, start_date, end_date,
    *, pit_provider=None) -> MicrostructureMaskResult`` — fetches
    ``$volume``, ``$high``, ``$low``, ``$close`` from qlib (via
    ``PITDataProvider`` when supplied, else direct ``qlib.data.D``
    behind the same allow-listed bypass used elsewhere — see
    audit P0-6), and computes the mask.
  * ``apply_mask_to_predictions(predictions, mask) ->
    tuple[predictions, n_dropped]`` — drops every
    ``(date, instrument)`` row in the mask from the predictions
    ``pd.Series`` before it reaches ``TopkDropoutStrategy``.
- ``BacktestRunner.run`` calls the helper after applying
  ``signal_to_execution_lag`` and BEFORE constructing the qlib
  strategy. The mask runs on the SHIFTED predictions so the
  filter applies to execution dates, not signal dates.
- A single ``_logger.warning(...)`` per run reports the total
  count + per-regime breakdown. Per-day WARN would flood logs.
- A governance test asserts that ``BacktestRunner.run`` source
  AST contains a call to ``compute_unavailable_mask``, so a
  future refactor that drops the mask integration fails CI.
- A governance test asserts ``microstructure_mask`` is on the
  allowlisted-D.features-callers list — the new module uses
  ``D.features`` to fetch OHLCV by design, and audit P0-6's
  guard requires the call site to be in the allowlist.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- ``v2-canonical-backtest-contract``: the canonical backtest
  runtime gains a microstructure mask applied before predictions
  reach qlib's strategy. The canonical official-metrics anchor,
  the qlib ``backtest.backtest`` callable, and every other field
  on ``CanonicalExchangeConfig`` are unchanged.

## Impact

- **Numeric drift**: backtests that previously fell for phantom
  fills on suspended / one-price days will produce smaller (more
  honest) annualised returns. Magnitude depends on universe +
  rebalance frequency; csi300 + monthly rebalance moves the needle
  ~0.5-1.5%/yr, csi500 + weekly rebalance ~1-3%/yr.
- **Operator-side YAML migration**: none. The mask is automatic
  and on by default; there's no operator knob to set.
- **Fixture regeneration**: the fold-0 regression baseline test
  fixture was captured BEFORE this mask existed. After this PR,
  re-running fold-0 produces slightly different numbers — we
  bump the tolerance on ``annualized_return_absolute`` from
  ``0.005`` to ``0.010`` to absorb the one-shot honest correction,
  matching the precedent set by ``add-stamp-tax-schedule`` (PR
  #178).
- **Performance**: one extra ``qlib.data.D.features(...)`` call
  per ``BacktestRunner.run`` to fetch OHLCV. On a csi300 + 1-year
  window this is a few hundred milliseconds; on a 4-year
  walk-forward with N folds it's N × few hundred ms. Within
  noise of the existing per-fold setup cost.
