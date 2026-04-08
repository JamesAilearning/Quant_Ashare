## Context

The canonical backtest contract exists and its official path is anchored to `qlib.backtest.backtest`. But the input boundary is currently untyped for the fields that determine whether the backtest is even reproducible. This change hardens the input boundary before any runtime execution is wired in.

## Goals / Non-Goals

**Goals:**
- Make every quant-risk knob that changes official metrics explicitly required and typed.
- Reject wrong-type inputs at the contract boundary.
- Bound-check numeric fields to rule out nonsensical values (e.g. 100% commission).
- Preserve the canonical official-metrics anchor and the contract-only posture.

**Non-Goals:**
- Do not implement any runtime trading behavior.
- Do not call `qlib.backtest.backtest`.
- Do not define strategy, executor, or model interfaces.
- Do not alter other contracts.
- Do not introduce risk constraints (single-name cap, industry cap, turnover). Those belong to a separate spec change because they interact with strategy semantics, not just input boundaries.

## Decisions

1. **Replace free-form mappings with frozen dataclasses.**
   - Decision: `CanonicalAccountConfig` and `CanonicalExchangeConfig` become `@dataclass(frozen=True)` with `__post_init__` validation.
   - Rationale: V1 lesson "duplicate helper definitions and schema drift" was the direct consequence of allowing config to be "whatever dict you want". Frozen dataclasses make the official shape static and IDE-discoverable.
   - Trade-off: existing `test_canonical_backtest_contract.py` constructs `account_config={"init_cash": ...}`. Those call sites must be migrated in the same change. Impact is limited to one test file.

2. **`adjust_mode` is a required enum, not Optional.**
   - Decision: the caller MUST declare one of `pre_adjusted`, `post_adjusted`, `unadjusted`. There is no default.
   - Rationale: picking a default silently hides the single most common cause of look-correct-but-wrong backtests. Forcing the caller to write the word is worth the boilerplate.

3. **`signal_to_execution_lag` must be `>= 1`.**
   - Decision: the minimum value is 1. Zero is explicitly rejected at validate time.
   - Rationale: lag == 0 means "execute with the same bar that produced the signal", which is the canonical look-ahead bias. The contract's whole purpose is to make that impossible to write by accident.
   - Trade-off: intraday strategies with sub-bar lag models do not fit this contract. That is intentional: such strategies require a different, explicitly-labeled contract.

4. **`execution_price_kind` lives on `CanonicalExchangeConfig`, not on the input root.**
   - Decision: it is part of the exchange configuration because it describes how fills are priced, which is an exchange property.
   - Rationale: keeps the input root smaller and prevents split-brain between `exchange_config.freq` and a sibling `execution_price_kind` on the root.

5. **Cost model fields are bounded, not just typed.**
   - Decision: `commission_rate ∈ [0, 0.01]`, `stamp_tax_bps ∈ [0, 100]`, `slippage_bps ∈ [0, 200]`, `min_cost >= 0`. Violations raise `CanonicalBacktestContractError`.
   - Rationale: Type-only validation lets `commission_rate=1.5` through. Bounds catch orders-of-magnitude mistakes that would otherwise produce plausible-looking but catastrophically wrong PnL curves.
   - Trade-off: the bounds are opinionated. They can be relaxed later via a separate spec change if a real market case requires it.

6. **No implicit defaults for any new required field.**
   - Decision: every new field is positional-or-kwarg with no default value. A future "easy constructor" helper can layer on top, labeled experimental, outside the canonical contract.
   - Rationale: V1 lesson "avoid implicit fallback behavior without clear labels". The canonical boundary must force the caller to write the number.

## Risks / Trade-offs

- [Risk] Callers will find the new input shape verbose.
  - Mitigation: a future experimental-layer helper can construct a canonical input from a looser dict, as long as it is labeled experimental and does not ship inside `src/core/`.
- [Risk] Bounds may be wrong for some markets (e.g. 200 bps slippage is extreme even for small caps).
  - Mitigation: bounds are documented and change-controlled via a dedicated spec change.
- [Risk] Breaking the existing test shape could mask other regressions.
  - Mitigation: the test migration is mechanical, scoped to one file, and the new rejection tests cover every new field individually.

## Migration Plan

1. Land this change. Every existing test continues to pass because the only test file that constructs `CanonicalBacktestInput` is updated in the same change.
2. Future runtime change: `implement-canonical-backtest-runtime` can now call `qlib.backtest.backtest` with a well-defined input shape.
3. Future risk change: `define-canonical-risk-constraints-contract` adds single-name cap, industry cap, and turnover constraints as a SEPARATE typed sub-object on the input.

Rollback: revert the change. No published metrics exist yet.

## Open Questions

- Should `adjust_mode` also be recorded on the output/provenance side? Current design: input-only; provenance is a later concern owned by the run-artifact contract.
- Should `signal_to_execution_lag` be measured in bars or in calendar days? Current design: bars. A future intraday contract can use a different field name.
