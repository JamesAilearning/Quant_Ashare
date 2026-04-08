## 1. Typed Input Dataclasses

- [x] 1.1 Add `CanonicalAccountConfig` frozen dataclass with `init_cash: float` and bounds check (`> 0`).
- [x] 1.2 Add `CanonicalExchangeCostModel` frozen dataclass with `commission_rate`, `stamp_tax_bps`, `slippage_bps`, `min_cost`, all bounds-checked in `__post_init__`.
- [x] 1.3 Add `CanonicalExchangeConfig` frozen dataclass with `freq`, `execution_price_kind`, `cost_model`, validated against supported enumerations.

## 2. Input Boundary Changes

- [x] 2.1 Replace `CanonicalBacktestInput.account_config: Mapping[str, Any]` with `CanonicalAccountConfig`.
- [x] 2.2 Replace `CanonicalBacktestInput.exchange_config: Mapping[str, Any]` with `CanonicalExchangeConfig`.
- [x] 2.3 Add required `adjust_mode: str` field with enumerated allowed values.
- [x] 2.4 Add required `signal_to_execution_lag: int` field with `>= 1` constraint.
- [x] 2.5 Extend `CANONICAL_INPUT_REQUIRED_FIELDS` to include `adjust_mode` and `signal_to_execution_lag`.

## 3. Validation

- [x] 3.1 `CanonicalBacktestContract.validate_input` rejects wrong-type `account_config` / `exchange_config` with a clear error.
- [x] 3.2 `validate_input` rejects unknown `adjust_mode` values with an enumeration error.
- [x] 3.3 `validate_input` rejects `signal_to_execution_lag < 1` with a look-ahead error.
- [x] 3.4 Bound-check errors produced in `__post_init__` surface as `CanonicalBacktestContractError` (either raised directly or re-wrapped).

## 4. Test Migration and Additions

- [x] 4.1 Update `tests/governance/test_canonical_backtest_contract.py` `_valid_request` to construct the new strict shape.
- [x] 4.2 Add test: rejects dict-shaped `account_config` / `exchange_config`.
- [x] 4.3 Add test: rejects unknown `adjust_mode`.
- [x] 4.4 Add test: rejects `signal_to_execution_lag == 0` with a look-ahead-style error.
- [x] 4.5 Add test: rejects `commission_rate > 0.01`.
- [x] 4.6 Add test: rejects unknown `execution_price_kind`.
- [x] 4.7 Add test: `CANONICAL_INPUT_REQUIRED_FIELDS` includes the two new fields.

## 5. Quality Gates

- [x] 5.1 Run full unittest discovery. All tests pass.
- [x] 5.2 Confirm no other contract or runtime behavior was changed.
- [x] 5.3 Confirm the canonical backtest path anchor is unchanged (`qlib.backtest.backtest`).
