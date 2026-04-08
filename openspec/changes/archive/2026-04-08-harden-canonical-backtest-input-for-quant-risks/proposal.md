## Why

`CanonicalBacktestInput` currently accepts `account_config` and `exchange_config` as free-form `Mapping[str, Any]`, and has no fields at all for the four highest-risk knobs in a quant system:

1. **Price adjustment mode** (pre/post/unadjusted). A signal trained on post-adjusted prices and backtested on unadjusted prices silently mis-reports returns on every split/dividend day, and the bug is invisible in aggregate metrics.
2. **Signal-to-execution lag.** If a decision made with `T` close data is allowed to execute at `T` close, the backtest is look-ahead-biased by one day. The contract currently has no field to forbid this.
3. **Execution price kind** (open / close / vwap). Whether a `T+1` order fills at `T+1` open vs `T+1` close is worth 10–50 bps on many strategies; silently changing the default rewrites official metrics.
4. **Commission / stamp tax / slippage / min-cost.** All four are required to reproduce official metrics. CN-market stamp tax (sell-side only, ~10 bps) is a V1-style silent default waiting to happen.

These are the exact "looks like it runs, silently wrong" risks the V2 governance baseline was meant to prevent, yet the canonical backtest contract currently has no typed field for any of them. Once a future change wires in a real `qlib.backtest.backtest` call, adding these fields after the fact is dramatically harder because every caller will already assume defaults.

## What Changes

- Replace `account_config: Mapping[str, Any]` with a frozen `CanonicalAccountConfig` dataclass.
- Replace `exchange_config: Mapping[str, Any]` with a frozen `CanonicalExchangeConfig` dataclass that embeds a frozen `CanonicalExchangeCostModel`.
- Add a required `adjust_mode` field to `CanonicalBacktestInput` with an explicit enumeration of allowed values.
- Add a required `signal_to_execution_lag` integer field with a minimum of 1.
- Add a required `execution_price_kind` field to `CanonicalExchangeConfig` with an explicit enumeration.
- Extend `CanonicalBacktestContract.validate_input` to bound-check every new field and reject instances of the wrong type.
- Update `tests/governance/test_canonical_backtest_contract.py` to use the new strict shape and add regression tests for each new rejection path.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `v2-canonical-backtest-contract`: canonical input boundary is tightened with typed dataclasses, explicit price-adjustment mode, explicit signal-to-execution lag, explicit execution price kind, and an explicit cost model. The canonical official-metrics path anchor and runtime-execution placeholder are unchanged.

## Impact

- Modified files:
  - `src/core/canonical_backtest_contract.py` (new dataclasses, tightened validation)
  - `tests/governance/test_canonical_backtest_contract.py` (migrated to new shape, new rejection tests)
- No runtime trading behavior is implemented; `CanonicalBacktestContract.run_placeholder` remains `NotImplementedError`.
- No change to the canonical official-metrics path anchor: `CANONICAL_OFFICIAL_BACKTEST_CALLABLE is qlib.backtest.backtest`.
- No change to benchmark, universe, taxonomy, run-artifact, or operator-workflow contracts.
