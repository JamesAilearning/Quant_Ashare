## Context

The reviewed failures cluster around two themes: official walk-forward
predictions must be reproducible from recorded artifacts, and runtime-adjacent
helpers should reject malformed numeric/data shapes close to their boundary.
The canonical qlib backtest path remains unchanged; the work is focused on
what is allowed to reach that path and what provenance is persisted beside it.

## Goals / Non-Goals

**Goals:**

- Make `ensemble_window > 1` an explicit, spec-approved walk-forward behavior
  with materialized prediction provenance.
- Remove the walk-forward CLI's machine-local provider fallback.
- Reject duplicate prediction indexes, non-finite position values, non-finite
  attribution inputs, and malformed CSV header/index mappings close to source.
- Preserve completed backtest reports when optional factor/chart steps fail.
- Archive completed OpenSpec changes after validating their specs.

**Non-Goals:**

- No new official metric calculation path.
- No change to qlib's backtest callable or official risk metric helper.
- No automatic promotion of Tushare data to default provider.
- No attempt to make optional factor analysis or chart generation canonical.

## Decisions

1. **Persist predictions, not just model references.**

   Walk-forward will write the exact post-ensemble prediction series used for
   signal analysis and backtest to a per-fold artifact. `predictions_ref` will
   point at that artifact, while the fold report records current/prior model
   refs and the prediction artifact hash.

2. **Skip mismatched ensemble priors loudly.**

   Prior model predictions must have exactly the same `(datetime, instrument)`
   index as the current fold predictions. Mismatched priors are rejected from
   the ensemble with metadata and a warning, preventing pandas union alignment
   from changing the traded signal universe.

3. **Fail early for operator config shape, degrade only optional post-steps.**

   Missing `provider_uri`, invalid dates, and invalid limit thresholds are
   configuration errors. Factor analysis and chart rendering happen after the
   official backtest; their failures should be logged/reported while preserving
   the completed backtest result.

4. **Use finite checks rather than coercive fallbacks.**

   NaN/Inf values in returns, weights, amount/price, and drawdown inputs are
   malformed boundary values. The implementation rejects or drops them
   explicitly instead of relying on pandas/numpy defaults.

## Risks / Trade-offs

- **Risk: additional prediction artifacts increase disk usage.** Mitigation:
  one pickle per fold is small relative to model artifacts and is required for
  reproducibility.
- **Risk: skipping mismatched priors reduces ensemble size.** Mitigation:
  metadata records attempted, loaded, and index-mismatched priors; current-fold
  predictions still run.
- **Risk: optional analysis failures no longer fail the whole pipeline.**
  Mitigation: skipped reasons are written to the report/log, while official
  backtest output remains intact.

## Migration Plan

1. Add OpenSpec deltas for the approved runtime and reliability boundaries.
2. Implement the runtime/data hardening changes.
3. Add targeted regression tests.
4. Archive completed legacy OpenSpec changes and validate all specs.
5. Run targeted tests plus `openspec validate --all --strict`.
