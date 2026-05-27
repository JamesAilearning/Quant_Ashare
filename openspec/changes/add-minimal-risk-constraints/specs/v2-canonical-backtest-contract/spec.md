## ADDED Requirements

### Requirement: The runtime SHALL expose a minimal risk-constraint engine

``src/core/risk_constraints.py`` SHALL expose a
``MinimalRiskConstraints`` frozen dataclass carrying at least
four constraints:

* ``max_per_name``: float in ``[0, 1]``, default ``0.05``.
* ``max_per_board``: float in ``[0, 1]``, default ``0.40``.
* ``cash_buffer_min``: float in ``[0, 1]``, default ``0.01``.
* ``max_leverage``: float in ``[0, 10]``, default ``1.00``.

The class SHALL also expose:

* ``mode: RiskConstraintMode`` with values ``RAISE`` and
  ``WARN_AND_CLIP``, default ``RAISE``.
* ``apply(positions_map: Mapping[str, Mapping[str, float]]) ->
  RiskConstraintsApplyResult`` method.

#### Scenario: defaults match the documented conservative profile
- **WHEN** a caller constructs ``MinimalRiskConstraints()``
- **THEN** ``max_per_name == 0.05`` AND
  ``max_per_board == 0.40`` AND
  ``cash_buffer_min == 0.01`` AND
  ``max_leverage == 1.00`` AND
  ``mode == RiskConstraintMode.RAISE``

#### Scenario: out-of-range constraint values are rejected
- **WHEN** a caller passes ``max_per_name=-0.05`` or
  ``max_per_name=1.5``
- **THEN** ``CanonicalBacktestContractError`` (or equivalent
  ``RiskConstraintError``) is raised at construction
- **AND** the message names the offending field and the bound

### Requirement: apply() SHALL produce structured violation records

``RiskConstraintsApplyResult`` SHALL carry a tuple of
``RiskConstraintViolation`` records, each with:

* ``date`` — string ISO date of the violating positions snapshot,
* ``constraint_name`` — one of ``"max_per_name"``,
  ``"max_per_board"``, ``"cash_buffer_min"``, ``"max_leverage"``,
* ``instrument_or_bucket`` — the instrument code for per-name,
  the board id for per-board, ``"__cash__"`` for cash buffer,
  ``"__portfolio__"`` for leverage,
* ``actual`` — the offending numeric value,
* ``limit`` — the constraint's limit value,
* ``details`` — optional mapping with constraint-specific extra
  data (e.g. the contributing instruments for a per-board violation).

#### Scenario: a per-name violation is fully described
- **WHEN** apply() processes a positions snapshot with
  ``{"SH600000": 0.08, "SH600001": 0.04}`` and
  ``max_per_name=0.05``
- **THEN** the result carries exactly one violation
- **AND** the violation has ``constraint_name="max_per_name"``,
  ``instrument_or_bucket="SH600000"``, ``actual==0.08``, and
  ``limit==0.05``

### Requirement: RAISE mode SHALL surface ALL violations in a single error

In ``RAISE`` mode, when one or more violations are found,
``apply()`` SHALL collect every violation across every day and
raise ``RiskConstraintError`` whose message includes every
violation. The error SHALL NOT short-circuit on the first
violation.

#### Scenario: multiple violations across days
- **WHEN** apply() processes a positions map with violations
  on three distinct days
- **THEN** ``RiskConstraintError`` is raised
- **AND** the error message names all three days

### Requirement: WARN_AND_CLIP mode SHALL return clipped positions and log per violation

In ``WARN_AND_CLIP`` mode, ``apply()`` SHALL NOT raise. Instead:

* Emit a WARN log for each violation,
* Return ``RiskConstraintsApplyResult`` whose
  ``clipped_positions`` field carries the positions map with
  per-name caps applied, per-board scaled-down, cash-buffer
  top-up, and leverage scaled-down per the constraint values.
  The excess weight SHALL be redistributed to cash, NOT to
  other instruments.

#### Scenario: per-name clip
- **WHEN** apply() processes ``{"SH600000": 0.08}`` with
  ``max_per_name=0.05`` in ``WARN_AND_CLIP`` mode
- **THEN** ``RiskConstraintError`` is NOT raised
- **AND** ``clipped_positions`` for that day contains
  ``"SH600000"`` with weight ``0.05``
- **AND** the difference ``0.03`` is added to the
  ``"__cash__"`` (or equivalent) cash entry
- **AND** exactly one WARN log is emitted

### Requirement: BacktestRunner SHALL accept an optional risk_constraints kwarg

``BacktestRunner.run`` SHALL accept
``risk_constraints: MinimalRiskConstraints | None = None``.

* When ``None``: ``run`` SHALL emit a single WARN log
  ("no risk constraints active") and proceed.
* When supplied: after the official ``return_series`` and
  ``risk_analysis`` are computed, ``run`` SHALL call
  ``risk_constraints.apply(positions_map)`` and behave
  according to the mode:
  - ``RAISE`` → raise ``BacktestRunnerError`` (wrapping
    ``RiskConstraintError``) on any non-empty violations.
  - ``WARN_AND_CLIP`` → preserve qlib's executed positions on
    ``CanonicalBacktestOutput.positions`` (so it stays
    consistent with ``return_series`` / ``risk_analysis``),
    AND expose the constraint-respecting allocation on the
    sibling field ``positions_clipped`` (populated only when
    at least one clip happened).

#### Scenario: None-default emits a WARN
- **WHEN** ``BacktestRunner.run(...)`` is called without
  ``risk_constraints``
- **THEN** a single WARN log is emitted referencing
  "no risk constraints active"

#### Scenario: RAISE mode surfaces violations as an error
- **WHEN** ``BacktestRunner.run(..., risk_constraints=
  MinimalRiskConstraints(mode=RAISE))`` is called with a
  predictions / positions combination that produces a
  per-name violation
- **THEN** ``BacktestRunner.run`` raises (either
  ``BacktestRunnerError`` wrapping ``RiskConstraintError``
  or ``RiskConstraintError`` directly)
- **AND** the error message names the offending instrument
  and date
