## 1. Constraint types + engine in `src/core/risk_constraints.py`

- [ ] 1.1 Add ``RiskConstraintMode`` enum / typed constants
  (``RAISE``, ``WARN_AND_CLIP``).
- [ ] 1.2 Add ``RiskConstraintViolation`` frozen dataclass
  ``(date, constraint_name, instrument_or_bucket, actual,
  limit, details)``.
- [ ] 1.3 Add ``RiskConstraintsApplyResult`` frozen dataclass
  ``(violations, clipped_positions, was_clipped)``.
- [ ] 1.4 Add ``MinimalRiskConstraints`` frozen dataclass with
  ``max_per_name`` (default 0.05), ``max_per_board`` (default
  0.40), ``cash_buffer_min`` (default 0.01),
  ``max_leverage`` (default 1.00), ``mode`` (default RAISE).
  ``__post_init__`` validates ranges + types.
- [ ] 1.5 Add ``MinimalRiskConstraints.apply(positions_map)`` method
  that walks per-day positions, computes violations against each
  of the four constraints, and either returns a result with
  violations (RAISE mode) or returns a result with the clipped
  positions map (WARN_AND_CLIP mode).
- [ ] 1.6 Leave ``RiskConstraintEngine`` (the existing fail-closed
  stub) unchanged. Document in module docstring that
  ``MinimalRiskConstraints`` is the new live surface and the
  stub is preserved for compat.

## 2. Runtime in `src/core/backtest_runner.py`

- [ ] 2.1 Add ``risk_constraints: MinimalRiskConstraints | None = None``
  kwarg to ``BacktestRunner.run``.
- [ ] 2.2 When ``None``: emit a single WARN ("backtest ran with
  NO risk constraints active"). Run otherwise unchanged.
- [ ] 2.3 When supplied: after ``positions_map`` is built, call
  ``risk_constraints.apply(positions_map)``. In RAISE mode,
  raise ``RiskConstraintError`` on non-empty violations. In
  WARN_AND_CLIP mode, log every violation and replace
  ``positions_map`` with the clipped one before constructing
  ``CanonicalBacktestOutput``.
- [ ] 2.4 Add new optional field ``positions_pre_clip`` to
  ``CanonicalBacktestOutput`` (mapping, same shape as
  ``positions``) carrying the unclipped map when clipping
  happened. Documented on the field that it is present iff
  ``risk_constraints`` was in WARN_AND_CLIP mode AND at least
  one violation was clipped.

## 3. Unit tests in `tests/logic/`

- [ ] 3.1 New ``tests/logic/test_minimal_risk_constraints.py`` —
  unit tests on the engine, no qlib needed.
  - Default-construction sanity.
  - Constructor rejects out-of-range values (negative,
    above 1.0) + wrong types.
  - ``max_per_name``: positive (within-cap) / negative (exceeds-cap
    RAISE) / negative (exceeds-cap WARN_AND_CLIP returns clip).
  - ``max_per_board``: positive / negative-RAISE / negative-
    WARN_AND_CLIP. Uses ``board_heuristic.classify_instrument``.
  - ``cash_buffer_min``: positive / negative-RAISE / negative-
    WARN_AND_CLIP.
  - ``max_leverage``: positive / negative (sum-of-weights >
    cap) RAISE / WARN_AND_CLIP.
  - Mixed violations: multiple constraints violated on the same
    day → RAISE mode collects all and raises with all listed.
  - Empty positions map (qlib produced no positions): apply
    returns empty violations, no-op.
- [ ] 3.2 ``RiskConstraintsApplyResult`` immutability + structure
  check.

## 4. Integration tests in `tests/logic/test_backtest_runner.py`

- [ ] 4.1 New ``BacktestRunnerRiskConstraintsTests`` class.
  - ``risk_constraints=None`` → WARN log fires; run completes.
  - ``risk_constraints=MinimalRiskConstraints(mode=RAISE)`` →
    a fake positions_map with an obvious violation produces a
    ``RiskConstraintError`` from ``BacktestRunner.run``.
  - ``risk_constraints=MinimalRiskConstraints(mode=WARN_AND_CLIP)``
    → same positions_map produces a successful output with the
    ``positions`` field clipped and ``positions_pre_clip``
    carrying the original.

## 5. Governance tests in `tests/governance/`

- [ ] 5.1 New ``tests/governance/test_minimal_risk_constraints_surface.py``:
  - ``MinimalRiskConstraints`` is importable from
    ``src.core.risk_constraints``.
  - All four constraint field names exist with the documented
    defaults.
  - ``RiskConstraintMode.RAISE`` and ``WARN_AND_CLIP`` exist.
  - ``RiskConstraintEngine`` stub still exists and still raises
    on apply (backwards-compat guard for the legacy fail-closed
    contract).

## 6. Spec delta

- [ ] 6.1 ``openspec/changes/add-minimal-risk-constraints/specs/v2-canonical-backtest-contract/spec.md``
  with ADDED Requirements describing the runtime surface +
  scenarios.

## 7. Manual verification

- [ ] 7.1 ``pytest tests/logic/test_minimal_risk_constraints.py
  tests/logic/test_backtest_runner.py
  tests/governance/test_minimal_risk_constraints_surface.py``
  → 0 unexpected failures.
- [ ] 7.2 ``pytest tests/logic/ tests/governance/`` → 0
  regressions vs main.
- [ ] 7.3 ``ruff check src/ tests/ scripts/`` → clean.
