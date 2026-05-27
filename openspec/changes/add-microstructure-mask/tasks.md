## 1. New module ``src/core/microstructure_mask.py``

- [ ] 1.1 Define ``MicrostructureMaskResult`` frozen dataclass
  with fields ``masked: frozenset[tuple[str, str]]``,
  ``n_suspended: int``, ``n_one_price_days: int``.
- [ ] 1.2 Define ``MicrostructureMaskError(RuntimeError)`` for
  fail-closed callers.
- [ ] 1.3 Implement ``compute_unavailable_mask(instruments,
  start_date, end_date, *, pit_provider=None) ->
  MicrostructureMaskResult``. Routes OHLCV fetch through PIT when
  supplied; falls back to direct ``D.features`` (allow-listed
  under audit P0-6).
- [ ] 1.4 Implement ``apply_mask_to_predictions(predictions, mask)
  -> tuple[predictions, n_dropped]``. Drops rows; returns
  unchanged Series + 0 when mask is empty.
- [ ] 1.5 Pit-bypass-ok marker + WARN copy referencing
  ``Audit P0-3`` and ``Audit P0-6``.

## 2. ``BacktestRunner.run`` integration

- [ ] 2.1 After ``shifted_predictions = cls._apply_lag(...)`` and
  BEFORE constructing ``TopkDropoutStrategy``, call
  ``compute_unavailable_mask(...)`` on the instrument universe in
  predictions.
- [ ] 2.2 Apply the mask via ``apply_mask_to_predictions``;
  rebind ``shifted_predictions``.
- [ ] 2.3 If ``mask_result.masked`` is non-empty, emit a single
  WARN with the per-regime counts.

## 3. PIT-bypass allowlist update

- [ ] 3.1 Add ``src/core/microstructure_mask.py`` to
  ``PIT_FEATURES_BYPASS_ALLOWLIST`` in
  ``tests/governance/test_pit_provider_is_sole_qlib_features_caller.py``
  with the expected ``D.features`` call count.
- [ ] 3.2 Add the ``pit-bypass-ok`` marker to the enclosing
  function so the marker-aware scanner passes.

## 4. Unit tests in ``tests/logic/test_microstructure_mask.py``

- [ ] 4.1 ``compute_unavailable_mask`` with all-trading universe
  → empty mask + zero counts.
- [ ] 4.2 Single-instrument suspension day (volume=0) → masked,
  ``n_suspended==1``.
- [ ] 4.3 Single-instrument one-price day (high==low, volume>0)
  → masked, ``n_one_price_days==1``.
- [ ] 4.4 Combined: same instrument has a suspension day AND a
  one-price day in the window → 2 mask entries, counts match.
- [ ] 4.5 Both regimes on the same day for the same instrument
  → counted as suspended (volume<=0 takes precedence).
- [ ] 4.6 NaN-OHLC pre-listing rows → counted as suspended,
  not one-price.
- [ ] 4.7 ``apply_mask_to_predictions`` drops exactly the masked
  rows; returns ``n_dropped`` count.
- [ ] 4.8 ``apply_mask_to_predictions`` with empty mask returns
  the Series unchanged (same object, ``n_dropped==0``).

## 5. BacktestRunner integration tests in
``tests/logic/test_backtest_runner.py``

- [ ] 5.1 New test class
  ``MicrostructureMaskIntegrationTests``. Reuses the existing
  qlib-data sys.modules mock pattern.
- [ ] 5.2 ``test_mask_dropped_predictions_before_strategy`` —
  fake OHLCV containing one suspension day and one one-price
  day; assert the predictions Series passed to qlib's
  ``TopkDropoutStrategy`` constructor has those rows removed.
- [ ] 5.3 ``test_warn_summarises_per_regime_counts`` — assert
  the WARN log line contains ``"suspended"``, ``"one-price"``,
  the integer counts, and ``"Audit P0-3"``.
- [ ] 5.4 ``test_empty_mask_no_warn`` — fake OHLCV with all
  normal trading days; assert no ``"masked"`` WARN fires AND
  predictions reach qlib unchanged.

## 6. Governance test

- [ ] 6.1 New
  ``tests/governance/test_backtest_runner_applies_microstructure_mask.py``:
  AST-parse ``src/core/backtest_runner.py``; assert
  ``BacktestRunner.run`` source contains a Call node to
  ``compute_unavailable_mask``. Test fails CI if a future
  refactor removes the integration.

## 7. Regression baseline tolerance bump

- [ ] 7.1 In ``tests/regression/test_fold0_baseline.py`` bump
  ``annualized_return_absolute`` default from ``0.005`` to
  ``0.010``. Add docstring note linking audit P0-3 and the
  precedent from P0-1 / P0-4.

## 8. Spec + OpenSpec validation

- [ ] 8.1 Spec delta at
  ``openspec/changes/add-microstructure-mask/specs/v2-canonical-backtest-contract/spec.md``
  with ADDED Requirements.
- [ ] 8.2 ``openspec validate --specs --strict`` if available in
  CI; otherwise smoke-check the spec file parses.

## 9. Manual verification

- [ ] 9.1 ``pytest tests/logic/ tests/governance/`` → 0
  unexpected failures.
- [ ] 9.2 ``ruff check src/ tests/ scripts/`` → clean.
