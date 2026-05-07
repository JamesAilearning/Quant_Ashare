## Context

The canonical backtest path currently treats `signal_to_execution_lag=1` as a
no-op and asks callers to pass `2` for a one-day delayed execution. That is
counter to the common A-share workflow where signals produced after the close
on T execute on T+1. The contract also rejects `0`, leaving no explicit way to
request same-day execution.

Because this changes official metric semantics for default configs, the change
must be narrow, explicit, and covered by regression tests.

## Goals / Non-Goals

**Goals:**

- Make `signal_to_execution_lag=0` the only no-op/same-day execution mode.
- Make `signal_to_execution_lag=1` shift predictions one trading row.
- Preserve default `signal_to_execution_lag=1` so default configs now model T+1
  execution.
- Update validation and tests to make the migration visible.

**Non-Goals:**

- No change to qlib's anchored backtest callable.
- No attempt to model intraday signal generation time.
- No automatic rewrite of user configs; same-day users must set `lag=0`.

## Decisions

1. **Use integer lag as number of trading-row shifts.**

   `lag=0` returns predictions unchanged. `lag=N` shifts each instrument's
   signal by N rows on the datetime axis. This matches the literal reading and
   removes the old off-by-one mental model.

2. **Keep defaults at `1`.**

   Changing defaults to `0` would preserve historical metric numbers but keep
   the look-ahead-prone behavior as the easiest path. The new default should
   represent the common T+1 A-share workflow.

3. **Treat the change as a breaking semantic migration.**

   The proposal and tests call out that same-day users must configure
   `signal_to_execution_lag=0`. This is safer than preserving hidden backward
   compatibility in the official path.

## Risks / Trade-offs

- **Historical official metrics change** -> Mitigation: mark as breaking in the
  proposal and make same-day execution explicit with `lag=0`.
- **Short backtest windows lose first-day signals** -> Mitigation: this is the
  intended result of delayed execution; tests assert the shifted shape.
- **Different users mean calendar vs trading-day lag** -> Mitigation: shifting
  by prediction datetime rows preserves qlib provider trading-day semantics.

## Migration Plan

1. Update contract validation to accept `lag=0` and reject negative/bool values.
2. Update `_apply_lag` to shift by exactly `lag`.
3. Update pipeline/walk-forward validation text and tests.
4. Existing configs requiring old no-op behavior should set
   `signal_to_execution_lag: 0`.
