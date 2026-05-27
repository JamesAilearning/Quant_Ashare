## ADDED Requirements

### Requirement: Canonical backtest SHALL drop suspended and one-price-locked candidates from predictions before qlib strategy

``BacktestRunner.run`` SHALL compute a per-day microstructure
mask over the instrument universe in the predictions Series for
the evaluation window, and SHALL drop every
``(date, instrument)`` row in that mask from the predictions
Series BEFORE constructing the qlib strategy.

A ``(date, instrument)`` SHALL be on the mask iff EITHER:

* Suspended: ``$volume <= 0`` OR ``$close`` is NaN on that day.
* One-price-locked: ``$volume > 0`` AND ``$high == $low`` on
  that day.

The runtime SHALL emit a single WARN-level log per
``BacktestRunner.run`` invocation summarising the total mask
count and the per-regime breakdown (suspended count,
one-price-day count). When the mask is empty, the WARN SHALL NOT
fire.

#### Scenario: suspended day is dropped before strategy sees it
- **WHEN** the predictions Series contains a row for instrument
  ``SH600000`` on date ``2024-03-15``
- **AND** the qlib OHLCV for ``(SH600000, 2024-03-15)`` is
  ``$volume == 0``
- **THEN** the row is absent from the predictions Series passed
  to ``TopkDropoutStrategy``
- **AND** the WARN log line lists at least one suspended entry

#### Scenario: one-price-locked day is dropped before strategy sees it
- **WHEN** the predictions Series contains a row for instrument
  ``SH600000`` on date ``2024-03-15``
- **AND** the qlib OHLCV for ``(SH600000, 2024-03-15)`` is
  ``$volume > 0`` AND ``$high == $low``
- **THEN** the row is absent from the predictions Series passed
  to ``TopkDropoutStrategy``
- **AND** the WARN log line lists at least one one-price-day entry

#### Scenario: empty mask produces no WARN
- **WHEN** the predictions universe + evaluation window has no
  suspension and no one-price days
- **THEN** the WARN about microstructure masking is NOT emitted
- **AND** the predictions Series reaches qlib unchanged

### Requirement: The microstructure mask helper SHALL route OHLCV fetch through PIT when available

``compute_unavailable_mask`` SHALL accept an optional
``pit_provider`` argument. When supplied, OHLCV fetch SHALL go
through ``PITDataProvider.get_features``. When omitted, the
fetch falls through to direct ``qlib.data.D.features``; that
call site SHALL be on the PIT-bypass allowlist (audit P0-6).

#### Scenario: PIT-bypass governance test recognises the new site
- **WHEN** maintainers inspect
  ``PIT_FEATURES_BYPASS_ALLOWLIST`` in
  ``tests/governance/test_pit_provider_is_sole_qlib_features_caller.py``
- **THEN** ``"src/core/microstructure_mask.py"`` is present
  with its expected call count
- **AND** the enclosing function contains the marker substring
  ``"pit-bypass-ok"``

### Requirement: Governance SHALL guard the mask integration on the canonical backtest path

A test under ``tests/governance/`` SHALL AST-parse
``src/core/backtest_runner.py`` and SHALL assert that
``BacktestRunner.run``'s source contains a Call node referring
to ``compute_unavailable_mask``. A future refactor that removes
the integration SHALL fail the test.

#### Scenario: governance test catches a regression that removes the mask call
- **WHEN** a future change deletes the
  ``compute_unavailable_mask(...)`` call from
  ``BacktestRunner.run``
- **THEN** the governance test fails with a message naming the
  expected call and the file path
