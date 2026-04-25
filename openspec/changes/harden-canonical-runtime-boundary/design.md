## Context

The current runtime has two separate facts that should be one auditable
boundary:

- `QlibRuntimeConfig` records which qlib provider and region were initialized.
- `CanonicalBacktestInput.adjust_mode` records which price adjustment convention
  official metrics claim to use.

Because qlib provider data is pre-built outside this repository, the canonical
runtime cannot safely transform `pre_adjusted` into `post_adjusted` or
`unadjusted` at backtest time. The provider already embodies one adjustment
convention. The safe boundary is therefore: initialize qlib with an explicit
provider adjustment convention, then allow official backtests only when the
request asks for that same convention.

## Goals / Non-Goals

**Goals:**

- Make direct `BacktestRunner.run()` calls fail unless canonical qlib init has
  completed.
- Make `adjust_mode` execution-relevant by checking it against initialized
  provider semantics before official metrics are produced.
- Make runtime singleton idempotency include provider adjustment mode.
- Make WalkForward pass through canonical execution controls rather than
  hard-code them.
- Keep official metrics on the existing anchored qlib-native path.

**Non-Goals:**

- Do not add a second official backtest path.
- Do not implement price-adjustment conversion logic.
- Do not add canonical risk constraints.
- Do not relabel experimental or research behavior as official.
- Do not fix unrelated review findings in this change.

## Decisions

1. **Record provider adjustment convention in `QlibRuntimeConfig`.**
   - Decision: add a required `data_adjust_mode: str` (or equivalent explicit
     field name) to `QlibRuntimeConfig`.
   - Rationale: qlib global state is not self-describing. Without this metadata,
     official outputs cannot prove which adjusted data convention they used.
   - Trade-off: every init call site must be updated. This is intentional; a
     default would reintroduce implicit semantics.

2. **Reuse the canonical adjustment enum.**
   - Decision: the runtime config accepts only the same values as
     `CanonicalBacktestInput.adjust_mode`: `pre_adjusted`, `post_adjusted`, and
     `unadjusted`.
   - Rationale: one vocabulary prevents `pre`, `pre_adjust`, and
     `pre_adjusted` from drifting into competing meanings.
   - Implementation note: avoid duplicating the enum in two unrelated modules.
     A small shared contract-level constants module is acceptable if needed to
     avoid circular imports.

3. **BacktestRunner checks canonical init before official execution.**
   - Decision: after contract validation and before importing/calling qlib
     backtest, `BacktestRunner.run()` checks
     `is_canonical_qlib_initialized()` and retrieves
     `get_canonical_qlib_config()`.
   - Rationale: direct callers must not be able to produce official metrics from
     arbitrary pre-existing qlib global state.

4. **Adjustment-mode mismatch is a hard error.**
   - Decision: if `request.adjust_mode != runtime_config.data_adjust_mode`,
     `BacktestRunner.run()` raises `BacktestRunnerError`.
   - Rationale: silently accepting a mismatch would make `adjust_mode` a
     provenance decoration rather than an execution boundary.
   - Trade-off: callers with multiple adjustment conventions need separate
     provider directories or a future explicitly-approved conversion layer.

5. **WalkForward carries the same canonical controls as Pipeline.**
   - Decision: add fields to `WalkForwardConfig` for the canonical controls it
     currently hard-codes:
     `execution_price_kind`, `adjust_mode`, `signal_to_execution_lag`,
     `min_cost`, and `limit_threshold`.
   - Rationale: walk-forward official metrics must not be semantically different
     from one-shot pipeline metrics just because the request came through a
     different orchestrator.

6. **Keep validation close to the boundary.**
   - Decision: reuse existing canonical dataclass validation where possible.
     WalkForward only needs enough validation to reject obviously bad local
     fields before a fold starts.
   - Rationale: avoids inventing parallel bounds logic outside the canonical
     backtest contract.

## Risks / Trade-offs

- [Risk] Updating `QlibRuntimeConfig` is a broad mechanical change because many
  tests and e2e helpers instantiate it.
  - Mitigation: include a dedicated task to update every call site and add a
    regression test that mismatched `data_adjust_mode` breaks idempotent re-init.
- [Risk] The provider's real adjustment convention may be undocumented.
  - Mitigation: require callers to declare the convention explicitly. This
    change does not try to infer it from qlib internals.
- [Risk] Future conversion support may be desired.
  - Mitigation: this change leaves conversion as a separate decision-first
    runtime change. Until then, mismatch fails loudly.

## Migration Plan

1. Add explicit provider adjustment metadata to canonical qlib init.
2. Update all local init call sites and tests to pass that metadata.
3. Harden `BacktestRunner.run()` with init and adjustment checks.
4. Add WalkForward passthrough fields and use them when constructing
   `CanonicalBacktestInput`.
5. Run targeted governance and logic tests, then broader tests where practical.

Rollback: revert the change. No schema migration is required because this only
changes Python runtime/config construction.

## Open Questions

- Resolved during implementation: the runtime config field is named
  `data_adjust_mode`.
- Should output provenance include both requested and initialized adjustment
  modes? Deferred to a later run-artifact/provenance change to keep this scope
  focused on runtime boundary enforcement.
