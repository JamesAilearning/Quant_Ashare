## Context

Walk-forward folds already isolate full fold failures with a NaN placeholder,
but optional post-backtest attribution should not be able to discard a fold
whose model, prediction, signal analysis, and canonical backtest have already
completed. Pipeline already follows a "skip + warn + keep report" pattern for
unexpected attribution errors; walk-forward should match that behavior.

The Tushare provider bundle publisher validates malformed staged OHLCV rows but
currently relies on `isna()` and signed bounds. Infinite values are numeric,
not missing, and can therefore slip through some malformed rows.

Walk-forward window generation intentionally supports overlapping and sparse
rolling configurations. The correct fix is diagnostics, not a broad
`step_months > train_months` rejection that would flag legitimate operator
choices.

## Goals / Non-Goals

**Goals:**

- Keep completed walk-forward backtest outputs when optional attribution fails
  unexpectedly.
- Fail Tushare provider publishing validation on any non-finite OHLCV value.
- Report test-window coverage mode, gap count, and overlap depth in
  walk-forward aggregate output.

**Non-Goals:**

- Do not change canonical backtest semantics.
- Do not reject walk-forward configurations solely because training windows do
  not overlap.
- Do not add a new provider, data source selection path, or dependency.

## Decisions

1. **Downgrade unexpected attribution engine errors only after backtest.**
   Walk-forward will mirror Pipeline's optional-step behavior: typed
   attribution errors and unexpected attribution exceptions both become a
   skipped attribution block with an explicit reason, while taxonomy artifact
   load failures remain hard configuration errors.

2. **Use finite checks over all OHLCV columns.**
   Validation will reject non-finite values in open, high, low, close, volume,
   and amount after numeric coercion. This is stricter than relying on
   comparisons because `inf` can otherwise satisfy positive-value checks.

3. **Expose window diagnostics rather than rejecting sparse windows.**
   The aggregate report will record whether test periods are continuous,
   gapped, or overlapping. This keeps advanced rolling setups valid while
   making the resulting aggregation caveat visible.

## Risks / Trade-offs

- Broad attribution catch-all could hide a genuine attribution bug. Mitigation:
  the skipped reason includes exception type/message and the warning states that
  only attribution is degraded.
- Finite checks can reject staged data that previously published. Mitigation:
  non-finite market data is invalid for downstream qlib training and should fail
  before final publication.
- Coverage diagnostics are informational. Mitigation: this avoids introducing
  new hidden runtime policy while still surfacing the operator-visible risk.
