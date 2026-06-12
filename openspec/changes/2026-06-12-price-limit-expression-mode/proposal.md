# Proposal: price-limit-expression-mode

## Why

Audit A2 (docs/audit_rebase_20260611.md), reproduced through the real
backtest path in PR-D Step 0: price-limit enforcement was silently DEAD on
the production PIT bundle, in the return-inflating direction.

The canonical contract's `limit_threshold` float reached qlib's Exchange as
FLOAT mode, which keys the limit checks on the STORED `$change` field
(`quote_df["$change"].ge(threshold)`). The PIT bin builder deliberately does
not produce change bins ("Out of scope", qlib_bin_builder.py), and qlib
returns an all-NaN column for a missing field without complaint — `NaN >=
x` is False for every row, so `limit_buy`/`limit_sell` degraded to
suspension-only. The Step-0 probe (synthetic provider, real
builder→qlib→BacktestRunner path) confirmed both legs on unfixed code: a
top-scored signal FILLED at its +10% limit-up close, and a rotated-out name
SOLD at its -10% limit-down close. The microstructure mask only catches
one-price (high==low) days; a limit day that opened off the limit slips it.

## What Changes

- **Expression-mode translation** (`src/core/backtest_runner.py`): the
  contract's float magnitude is translated into qlib's expression tuple
  `("$close/Ref($close,1)-1 > thr", "$close/Ref($close,1)-1 < -thr")` —
  computed from `$close` history (always present), evaluated by qlib on the
  EXECUTION day's quote row, and OR'd with suspension. Float mode never
  reaches qlib.
- **Round-lot preflight warning**: when the provider has no usable
  `$factor` field, qlib silently switches to adjusted-price mode and
  DISABLES trade_unit (100-share round lots → fractional fills). The runner
  now probes `$factor` and emits a LOUD warning naming the degradation
  (diagnostic only — never blocks the official path).
- **Contract docs** (`CanonicalExchangeConfig`): `limit_threshold` stays a
  float MAGNITUDE knob (validation unchanged); its docstring now states the
  expression-mode enforcement and why float mode is forbidden.
- **Permanent probes** (`tests/logic/test_backtest_execution_timing.py`):
  limit-up buy blocked; limit-down sell blocked (name stays in the book on
  and after its -10% day). Both limit days are deliberately NOT one-price,
  so qlib's limit check is the only protection being tested.
- **Runtime pin** (`tests/logic/test_backtest_runner.py`): the
  exchange_kwargs that reach `qlib.backtest` carry the expression tuple —
  a refactor reverting to float mode fails loudly.

## Non-Goals

- Per-board / per-instrument thresholds (688/300 ±20%, ST ±5%): the uniform
  magnitude is a documented CONSERVATIVE bias (blocks slightly more than
  reality for 20%-band boards) — refinement is backlogged as audit A4.
- No change bins in the builder (the expression mode removes the need).
- No volume-limit (`volume_threshold`) modelling.
- No open-price limit semantics (deal_price stays close; limit checked on
  the close-to-close move of the fill day).
