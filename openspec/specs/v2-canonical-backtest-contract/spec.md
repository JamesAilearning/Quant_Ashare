# v2-canonical-backtest-contract Specification

## Purpose
TBD - created by archiving change define-v2-canonical-backtest-contract. Update Purpose after archive.
## Requirements
### Requirement: V2 SHALL define exactly one canonical official-metrics backtest path

The system SHALL expose exactly one canonical backtest contract for official metrics, based on qlib-native execution semantics, and SHALL NOT define competing official paths.

#### Scenario: official metrics source is declared
- **WHEN** maintainers inspect canonical backtest contract documentation
- **THEN** exactly one official metrics source is defined
- **AND** the source is explicitly labeled canonical
- **AND** no alternative path is labeled official

### Requirement: Canonical backtest contract SHALL define a strictly typed input boundary

The canonical contract SHALL declare required and optional inputs using frozen, typed dataclasses for account and exchange configuration. Free-form dictionaries for those fields SHALL be rejected at the validation boundary. Non-canonical control inputs remain explicitly out of scope.

#### Scenario: canonical input schema is reviewed
- **WHEN** contributors review canonical input definitions
- **THEN** required canonical inputs are clearly listed
- **AND** optional canonical inputs are clearly listed
- **AND** unsupported experimental/research controls are explicitly out of scope

#### Scenario: dict-shaped account_config is rejected
- **WHEN** a caller supplies `account_config` as a `dict`
- **THEN** `CanonicalBacktestContract.validate_input` raises `CanonicalBacktestContractError`
- **AND** the error message identifies `account_config` as the offending field

#### Scenario: dict-shaped exchange_config is rejected
- **WHEN** a caller supplies `exchange_config` as a `dict`
- **THEN** `CanonicalBacktestContract.validate_input` raises `CanonicalBacktestContractError`
- **AND** the error message identifies `exchange_config` as the offending field

### Requirement: Canonical backtest input SHALL require an explicit price-adjustment mode

The canonical backtest input SHALL require an `adjust_mode` field whose value is
one of `pre_adjusted`, `post_adjusted`, or `unadjusted`. There SHALL be no
default. Runtime execution SHALL treat the field as an execution boundary, not
only as provenance: an official backtest SHALL run only when the request's
adjustment mode matches the initialized qlib provider adjustment mode.

#### Scenario: unknown adjust_mode is rejected
- **WHEN** a caller supplies `adjust_mode="auto"`
- **THEN** `CanonicalBacktestContract.validate_input` raises `CanonicalBacktestContractError`
- **AND** the error message lists the allowed values

#### Scenario: requested adjustment mode differs from provider adjustment mode
- **WHEN** canonical qlib runtime is initialized with provider adjustment mode
  `pre_adjusted`
- **AND** a canonical backtest request supplies `adjust_mode="unadjusted"`
- **THEN** `BacktestRunner.run()` raises `BacktestRunnerError`
- **AND** `qlib.backtest.backtest` is not called
- **AND** no official metric output is produced

#### Scenario: requested adjustment mode matches provider adjustment mode
- **WHEN** canonical qlib runtime is initialized with provider adjustment mode
  `pre_adjusted`
- **AND** a canonical backtest request supplies `adjust_mode="pre_adjusted"`
- **THEN** `BacktestRunner.run()` may proceed to the anchored qlib backtest
  callable after all other contract checks pass

### Requirement: Canonical exchange config SHALL require an explicit execution price kind

The `CanonicalExchangeConfig` SHALL require an `execution_price_kind` whose value is one of `open`, `close`, or `vwap`. There SHALL be no default.

#### Scenario: unknown execution_price_kind is rejected
- **WHEN** a caller constructs `CanonicalExchangeConfig(..., execution_price_kind="limit", ...)`
- **THEN** a `CanonicalBacktestContractError` is raised during construction
- **AND** the error message lists the allowed values

### Requirement: Canonical exchange config SHALL bound-check cost-model fields

The `CanonicalExchangeCostModel` SHALL enforce bounds on
`commission_rate`, `slippage_bps`, and `min_cost`. The CN-market
stamp tax SHALL be carried by `stamp_tax_schedule` (see "ADDED
Requirements" below), NOT by a single scalar `stamp_tax_bps`. Out-
of-bound values SHALL be rejected at construction.

#### Scenario: commission_rate above cap is rejected
- **WHEN** a caller supplies `commission_rate=0.5`
- **THEN** a `CanonicalBacktestContractError` is raised during construction
- **AND** the error message names the offending field and the cap

#### Scenario: negative min_cost is rejected
- **WHEN** a caller supplies `min_cost=-1.0`
- **THEN** a `CanonicalBacktestContractError` is raised during construction

#### Scenario: legacy stamp_tax_bps kwarg is rejected at construction
- **WHEN** a caller supplies `stamp_tax_bps=10.0` to
  `CanonicalExchangeCostModel(...)`
- **THEN** a `TypeError` is raised (the field no longer exists)
- **AND** the error message guides the caller toward
  `stamp_tax_schedule` and the default constant

### Requirement: Canonical input required-field list SHALL include the quant-risk fields

`CANONICAL_INPUT_REQUIRED_FIELDS` SHALL include `adjust_mode` and `signal_to_execution_lag`.

#### Scenario: required-field list is inspected
- **WHEN** maintainers read `CanonicalBacktestContract.input_boundary()["required"]`
- **THEN** the returned tuple contains `adjust_mode` and `signal_to_execution_lag`

### Requirement: Canonical backtest contract SHALL define required outputs for official reporting

The canonical contract SHALL define required output fields for official reporting, including return series, risk-analysis payload, and provenance fields that identify canonical path usage.

#### Scenario: canonical output schema is reviewed
- **WHEN** contributors inspect canonical output definitions
- **THEN** required metric outputs are explicitly listed
- **AND** canonical provenance/status fields are explicitly listed
- **AND** output schema supports auditable official reporting

### Requirement: Canonical contract SHALL keep experimental execution non-official

Experimental execution paths SHALL remain explicitly non-canonical and SHALL NOT be mixed into official metric outputs.

#### Scenario: experimental logic is present in project
- **WHEN** an experimental backtest or risk-control path exists
- **THEN** it is labeled non-canonical
- **AND** official metrics remain sourced only from canonical outputs

### Requirement: Canonical contract SHALL keep research artifacts outside production execution

Research artifacts under `research/factor_lab/` SHALL be treated as non-production and SHALL NOT be consumed by canonical runtime unless promoted through explicit spec-approved changes.

#### Scenario: research boundary is checked
- **WHEN** contributors inspect canonical contract boundaries
- **THEN** research/factor_lab is marked non-production and non-canonical
- **AND** direct runtime coupling from research to canonical execution is disallowed by contract

### Requirement: Canonical contract SHALL forbid implicit fallback semantics

The canonical contract SHALL require explicit behavior for missing dependencies
and unsupported output shapes, and SHALL NOT allow hidden fallback paths that
change official metric meaning without explicit labeling. Official backtest
execution SHALL require the canonical qlib runtime to be initialized through
the approved runtime entry point before any official output can be produced.
Official backtest return-series payloads SHALL remain structured mappings of
date string to numeric value; unknown series shapes SHALL raise a typed runtime
error instead of being wrapped as raw display text.

#### Scenario: missing canonical qlib initialization occurs
- **WHEN** `BacktestRunner.run()` is called before
  `src.core.qlib_runtime.init_qlib_canonical(...)` has completed
- **THEN** a typed `BacktestRunnerError` is raised
- **AND** `qlib.backtest.backtest` is not called
- **AND** no official metric output is produced

#### Scenario: missing canonical dependency occurs
- **WHEN** a required canonical dependency is unavailable
- **THEN** contract behavior is explicitly defined
- **AND** no implicit hidden fallback changes official metric semantics

#### Scenario: return-series serialization fails
- **WHEN** the qlib report return, benchmark, or cost series cannot be
  iterated as date/value pairs or contains non-numeric values
- **THEN** `BacktestRunner.run()` raises `BacktestRunnerError`
- **AND** `CanonicalBacktestOutput.return_series` is not populated with a
  `{"raw": ...}` fallback envelope

### Requirement: Canonical backtest input SHALL validate `evaluation_start` / `evaluation_end` as ISO dates with start <= end

`CanonicalBacktestContract.validate_input` SHALL parse
`evaluation_start` and `evaluation_end` as strict ISO `YYYY-MM-DD`
dates AFTER the existing non-empty check, using the shared
`_shared_validators.parse_iso_date` helper with
`error_cls=CanonicalBacktestContractError`. It SHALL additionally
verify that the parsed `evaluation_start` is less than or equal to
the parsed `evaluation_end` and SHALL raise
`CanonicalBacktestContractError` otherwise. A single-day window
(`evaluation_start == evaluation_end`) SHALL be accepted.

#### Scenario: evaluation_start is not a valid ISO date
- **WHEN** a caller supplies `evaluation_start="banana"`
- **THEN** `CanonicalBacktestContract.validate_input` raises
  `CanonicalBacktestContractError`
- **AND** the error message contains the offending string `banana`

#### Scenario: evaluation_end uses a non-ISO separator
- **WHEN** a caller supplies `evaluation_end="2026/02/27"`
- **THEN** `CanonicalBacktestContract.validate_input` raises
  `CanonicalBacktestContractError`
- **AND** the error message contains the offending string `2026/02/27`

#### Scenario: evaluation_start is after evaluation_end
- **WHEN** a caller supplies `evaluation_start="2026-02-27"` and
  `evaluation_end="2026-02-01"`
- **THEN** `CanonicalBacktestContract.validate_input` raises
  `CanonicalBacktestContractError`
- **AND** the error message names both `evaluation_start` and
  `evaluation_end`

#### Scenario: single-day evaluation window is accepted
- **WHEN** a caller supplies `evaluation_start="2026-02-27"` and
  `evaluation_end="2026-02-27"`
- **THEN** `CanonicalBacktestContract.validate_input` returns the
  validated input without raising

### Requirement: Canonical contract SHALL define minimum validation and regression expectations

The canonical contract SHALL require minimum validation coverage, including boundary regressions that protect canonical-vs-experimental separation and official-metrics source integrity.

#### Scenario: canonical contract validation baseline is reviewed
- **WHEN** maintainers inspect required validation expectations
- **THEN** minimum regression categories are explicitly defined
- **AND** canonical source integrity checks are part of required validation
- **AND** boundary regressions are required before archive/merge

### Requirement: Canonical backtest input SHALL define explicit signal lag semantics

The canonical backtest input SHALL define `signal_to_execution_lag` as the
TOTAL number of trading days between a signal's stamp and its fill,
INCLUSIVE of qlib's built-in one-day consumption shift
(`TopkDropoutStrategy` consumes, on trade day D, the signal stamped D-1).
The external restamp applied by the runner SHALL therefore be `lag - 1`
trading rows: `1` (the default) SHALL apply no external restamp and fill on
T+1; values above `1` SHALL restamp by exactly `lag - 1` rows. `0` SHALL be
REJECTED on the canonical path: same-day execution requires restamping
signals backward — look-ahead — and the canonical runner stamps every
output `metric_status=official`, so a look-ahead run could masquerade as
official. Negative values and booleans SHALL likewise be rejected.

#### Scenario: default lag fills on the next trading day
- **WHEN** a caller supplies `signal_to_execution_lag=1` and a signal
  stamped day T
- **THEN** `BacktestRunner` applies no external restamp
- **AND** the position first exists on T+1 through the real qlib path

#### Scenario: zero lag is rejected as look-ahead
- **WHEN** a caller supplies `signal_to_execution_lag=0`
- **THEN** `CanonicalBacktestContract.validate_input` raises
  `CanonicalBacktestContractError`
- **AND** the pipeline and walk-forward config layers reject it identically

#### Scenario: lag two restamps one row
- **WHEN** a caller supplies `signal_to_execution_lag=2`
- **THEN** `BacktestRunner` restamps predictions by exactly one trading row

#### Scenario: negative lag is rejected
- **WHEN** a caller supplies `signal_to_execution_lag=-1`
- **THEN** `CanonicalBacktestContract.validate_input` raises `CanonicalBacktestContractError`

#### Scenario: boolean lag is rejected
- **WHEN** a caller supplies `signal_to_execution_lag=True`
- **THEN** `CanonicalBacktestContract.validate_input` raises `CanonicalBacktestContractError`

### Requirement: Price limits SHALL be enforced via close-derived expressions

The canonical backtest SHALL pass `limit_threshold` to qlib as the
EXPRESSION-mode tuple computed from `$close` history in Not-form
(`Not(move <= magnitude)` / `Not(move >= -magnitude)` where `move` is
`$close/Ref($close,1)-1`), never as a float. Float mode keys on the stored
`$change` field, which the PIT bundle does not produce, and qlib silently
disables the limit checks when that field is empty — allowing buys at
limit-up and sells at limit-down. The Not-form SHALL block fills whose
move is UNVERIFIABLE (NaN previous close — resumption days after a
suspension gap, a ticker's first bundle day): unverifiable ⇒ untradeable,
since the bare comparison form would silently permit them.

The adjusted-close ratio is the exchange-correct limit test, not an
approximation: tushare's adj_factor derives from the exchange-published
previous close (the rounded 除权除息参考价), so on ex-dividend/ex-rights
days the ratio equals the exchange's own move against its limit reference
(verified empirically across all 2021-2025 corporate-action days: zero
missed main-board limit closes). Raw-price ratios would diverge by the
full event magnitude on every ex-date and SHALL NOT be used. The contract
field remains a float MAGNITUDE (uniform across boards — a documented
conservative bias covering 688/300 ±20%, BJ ±30%, ST ±5%; per-board
refinement is backlogged).

The runner SHALL bound the exchange quote universe (`codes`) to the
post-mask tradable signal set, benchmark excluded, and SHALL refuse to run
when that set is empty (qlib would silently substitute the full provider
universe and emit zero-position official metrics). When the bounded
universe lacks a usable `$factor` (NaN factor on a row whose close is
present), the runner SHALL emit a loud warning that qlib trades in
adjusted-price mode with round lots disabled (diagnostic; the official
path proceeds). The price-limit semantics version SHALL be folded into
backtest provenance and the walk-forward resume fingerprint so pre- and
post-enforcement runs are never conflated.

#### Scenario: a limit-up fill day blocks the buy
- **WHEN** a top-scored signal's fill day closes +10% versus the prior
  close and the day is not one-price (high != low)
- **THEN** the canonical backtest holds no position in that name

#### Scenario: a limit-down fill day blocks the sell
- **WHEN** a held name is rotated out and its fill day closes -10%
- **THEN** the name remains in the book on (and after) that day

#### Scenario: float mode never reaches qlib
- **WHEN** `BacktestRunner.run` constructs qlib exchange kwargs
- **THEN** `limit_threshold` is the two-element Not-form expression tuple
  derived from the contract magnitude

#### Scenario: an unverifiable move blocks conservatively
- **WHEN** a signal's fill day follows a suspension gap (no previous bar,
  so the close move cannot be verified)
- **THEN** the fill is blocked even though numpy NaN comparisons would
  otherwise report "not at limit"

#### Scenario: a non-limit day fills normally (vacuity control)
- **WHEN** the same limit-up ticker is signalled for a 0%-move fill day
- **THEN** the position fills — proving the limit block above is
  attributable to the limit, not to an unrelated defect

#### Scenario: a fully-masked signal fails loud
- **WHEN** every prediction row is removed by the availability masks
- **THEN** the runner raises instead of emitting zero-position official
  metrics over qlib's silently-substituted full universe

### Requirement: The official backtest paths SHALL require ST exclusion

The official backtest paths SHALL require a non-empty `namechange_path`.
The single-fold pipeline and the walk-forward engine — the OFFICIAL backtest
paths — must carry it so the PIT historical ST/*ST exclusion is active,
consistent with the live recommend path. When an official path is invoked
without a usable `namechange_path`, the run SHALL raise rather than silently
produce official metrics over an ST-included universe. The raw
`BacktestRunner.run` entry MAY still run an ST-included universe with a
warning for research/unit callers that explicitly opt out of the requirement.

Every shipped backtest config (single-fold or walk-forward) SHALL resolve a
non-empty `namechange_path` — directly (`config.yaml`, `config_smoke.yaml`) or
via `extends` inheritance (`config_walk_n*.yaml` → `config_walk.yaml`). A
governance test sweeps all root `config*.yaml` backtest configs (skipping
ingest configs that never reach `BacktestRunner.run`) so YAML drift fails at
review time.

Any tool that GENERATES an official backtest config SHALL populate a non-empty
`namechange_path`. In particular the Operator UI, which emits a STANDALONE job
config (no `extends`, not expanded through the `${VAR:-default}` YAML loader),
SHALL inject an env-defaulted `namechange_path` for both the pipeline and
walk-forward modes so a UI-launched official run does not RAISE after a full
train.

#### Scenario: an official single-fold run without namechange_path fails loud
- **WHEN** the pipeline (or walk-forward engine) runs a backtest with
  `require_st_mask=True` and no `namechange_path`
- **THEN** `BacktestRunner.run` raises rather than running ST-included

#### Scenario: a raw research call keeps the warn-pass
- **WHEN** `BacktestRunner.run` is called with `require_st_mask=False` and no
  `namechange_path`
- **THEN** it warns that ST is included and proceeds (backward compatible)

#### Scenario: a UI-generated job config carries the ST source
- **WHEN** the Operator UI builds a pipeline or walk-forward job config
- **THEN** that standalone config includes a non-empty `namechange_path`
  (env-overridable via `QUANT_NAMECHANGE_PATH`), so the official run it
  launches excludes ST rather than raising

### Requirement: The walk-forward regression baseline SHALL be replay-anchored

The committed walk-forward regression baseline SHALL be reproducible by a
DETERMINISTIC frozen-score replay — replaying fixed per-fold prediction Series
through the canonical `BacktestRunner` at the official semantics (T+1 execution,
close-derived price limits, PIT ST exclusion) — WITHOUT retraining any model or
rebuilding the bundle. The replay SHALL reproduce the committed aggregate AND
per-fold metrics to machine precision ON THE PROJECT'S CANONICAL DEPENDENCY STACK
(the pyproject pin: `numpy<2`, `scipy<1.14`, `pandas<2.3`), and the regression test
SHALL hold that tolerance in TEST SOURCE (not in the fixture) so a tampered fixture
cannot widen its own gate. The baseline SHALL be GENERATED on that canonical stack —
a gen-env==canonical-pin assertion SHALL fail generation loud off-pin — because a
degenerate fold's top-k tie-break is numpy-major-sensitive (a baseline baked on an
off-pin stack would not reproduce in CI).

The committed baseline JSON SHALL carry, alongside the numbers, the corrected
semantics, the statistical caveat (the headline is within cross-fold noise — NOT a
strategy improvement and not predictive of live performance), and the BENCHMARK
BASIS: the canonical baseline measures excess against the **SH000300TR total-return**
index. The total-return benchmark is APPLIED (it supersedes the SH000300 price-index
basis used by the preserved REGEN-A control; the prior "deferral" is CLOSED). A
CI-runnable governance test SHALL pin that this framing — including the total-return
basis — is committed with the value.

#### Scenario: a deterministic replay reproduces the committed baseline
- **WHEN** the frozen-score replay runs against the same bundle on the canonical
  dependency stack
- **THEN** every committed aggregate and per-fold metric reproduces within the
  in-source tight tolerance, else the regression test fails

#### Scenario: the corrected value cannot be committed without its framing
- **WHEN** the committed baseline JSON holds the canonical headline IR
- **THEN** a CI-runnable pin requires the corrected-semantics provenance, the
  within-noise statistical caveat, and the total-return-benchmark basis (excess
  measured against SH000300TR) to be present, else CI fails

#### Scenario: no single-fold anchor
- **WHEN** the walk-forward regression suite runs
- **THEN** the anchor is the full multi-fold deterministic replay, not a single
  fold (the most volatile, sign-flipping, within-noise fold is not used alone)

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

### Requirement: Availability masks SHALL filter by the true execution day

The microstructure (suspension / one-price-lock) and ST masks SHALL drop a
prediction row when its EXECUTION day — the trading day after its
post-restamp stamp — is masked, not when its stamp day is. ST attribution
records SHALL carry the execution date. A signal stamped on the final
evaluation day has no in-window execution day and SHALL be treated as
untradeable-by-construction (neither masked nor filled).

#### Scenario: top score suspended on its execution day never fills
- **WHEN** a ticker carries the panel's highest day-T score and is
  suspended (volume 0) on T+1
- **THEN** the canonical backtest holds no position in that ticker on any
  day

### Requirement: Headline IC SHALL be label-aligned

`SignalAnalyzer`'s per-period headline IC (`mean_ic`, and the derived `ic_1d`/`ic_5d`/`mean_ic_1d` consumers) SHALL correlate day-T scores with
the T+1 → T+1+period return — the window the training label defines and a
lag=1 strategy actually earns. The legacy stamp-day window (T → T+period)
SHALL survive only as an explicitly named secondary metric
(`mean_ic_stamp_day`), and each period summary SHALL name its convention.

#### Scenario: conventions are sharply distinguishable
- **WHEN** prices are constructed so the T+1→T+2 window ranks exactly with
  the scores while the T→T+1 window ranks exactly against them
- **THEN** the headline `mean_ic` reads +1 and `mean_ic_stamp_day` reads -1

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

In ``RAISE`` mode, when one or more violations are found, ``apply()`` SHALL collect every violation across every day and
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

### Requirement: Stamp tax SHALL be represented as a time-ordered schedule

The `CanonicalExchangeCostModel.stamp_tax_schedule` field SHALL be a
non-empty tuple of `StampTaxScheduleEntry` instances, each carrying
an `effective_from: date` and a `bps: float`. Entries SHALL be
strictly monotone in `effective_from` (ascending, no duplicates).
Each `bps` value SHALL be in `[0, STAMP_TAX_BPS_MAX]`.

#### Scenario: well-formed schedule is accepted
- **WHEN** a caller constructs a schedule
  `((2008-09-19, 10.0), (2023-08-28, 5.0))`
- **THEN** `CanonicalExchangeCostModel(stamp_tax_schedule=...)`
  constructs cleanly

#### Scenario: empty schedule is rejected
- **WHEN** a caller passes `stamp_tax_schedule=()`
- **THEN** a `CanonicalBacktestContractError` is raised at construction
- **AND** the message identifies the field as the offender

#### Scenario: non-monotone schedule is rejected
- **WHEN** a caller passes a schedule whose dates are not strictly
  ascending — for example `((2023-08-28, 5.0), (2008-09-19, 10.0))`
- **THEN** a `CanonicalBacktestContractError` is raised
- **AND** the message names both the field and the offending pair

#### Scenario: duplicate effective_from is rejected
- **WHEN** the schedule contains two entries with the same date
- **THEN** a `CanonicalBacktestContractError` is raised

#### Scenario: bps above cap is rejected
- **WHEN** any schedule entry's `bps` exceeds `STAMP_TAX_BPS_MAX`
- **THEN** a `CanonicalBacktestContractError` is raised at
  construction, identifying the offending entry's date and bps

### Requirement: A default CN schedule SHALL be exposed for ergonomic configs

The module SHALL expose `CN_STAMP_TAX_SCHEDULE_DEFAULT` as a
module-level constant of type
`tuple[StampTaxScheduleEntry, ...]`. It SHALL include at minimum
the 2023-08-28 transition: an entry with `effective_from=2008-09-19,
bps=10.0` followed by an entry with `effective_from=2023-08-28,
bps=5.0`. Configs that do not opt into a custom schedule SHALL
resolve to this default.

#### Scenario: default schedule has the 2023-08-28 reform
- **WHEN** a caller reads `CN_STAMP_TAX_SCHEDULE_DEFAULT`
- **THEN** the returned tuple contains an entry with
  `effective_from == date(2023, 8, 28)` and `bps == 5.0`
- **AND** at least one earlier entry exists whose `bps == 10.0`

### Requirement: The runtime SHALL collapse a schedule into a per-run scalar
The backtest runtime SHALL resolve a `stamp_tax_schedule` into a
single scalar suitable for `exchange_kwargs["close_cost"]` by:

* When the backtest period is covered by exactly one schedule
  entry: the runtime SHALL use that entry's `bps`.
* When the period crosses one or more transitions: the runtime
  SHALL use the trading-day-weighted average of the per-segment
  rates, AND SHALL emit a single `WARN`-level log per
  `BacktestRunner.run` call. The log SHALL include each crossed
  transition's date, the pre-transition bps, the post-transition
  bps, AND the weighted scalar that was used.
* When the period starts before the schedule's first
  `effective_from`: the runtime SHALL raise
  `CanonicalBacktestContractError` instead of extrapolating. The
  error SHALL name both the period start and the schedule's
  earliest date.

#### Scenario: period within one schedule entry
- **WHEN** the period is `2024-01-01 → 2024-12-31` and the schedule
  is the default
- **THEN** the resolved scalar equals `5.0`
- **AND** no WARN log is emitted

#### Scenario: period crosses 2023-08-28 transition
- **WHEN** the period is `2022-01-01 → 2024-12-31` and the schedule
  is the default
- **THEN** the resolved scalar is strictly between `5.0` and `10.0`
- **AND** exactly one WARN log is emitted, mentioning
  `2023-08-28`, `10.0`, `5.0`, and the resolved scalar

#### Scenario: period precedes schedule start
- **WHEN** the period starts at `2005-01-01` and the schedule's
  first entry is `2008-09-19`
- **THEN** the runtime raises `CanonicalBacktestContractError`
- **AND** the message names both `2005-01-01` and `2008-09-19`

### Requirement: Config layers SHALL accept the schedule or its default

`PipelineConfig.stamp_tax_schedule` and the walk-forward equivalent SHALL accept either:

* `None` (interpreted as `CN_STAMP_TAX_SCHEDULE_DEFAULT`), OR
* a `Sequence[Mapping[str, Any]]` whose entries each have
  `effective_from` (ISO date string or `datetime.date`) and `bps`
  (real number).

The legacy scalar `stamp_tax_bps` field SHALL NOT exist on the
config dataclasses, AND the YAML loaders SHALL raise
`PipelineConfigError` / `WalkForwardConfigError` when the legacy
key is present in the input mapping. The error message SHALL
include a copy-pasteable migration snippet.

#### Scenario: legacy YAML key is rejected
- **WHEN** the YAML input contains `stamp_tax_bps: 10.0`
- **THEN** loading raises with a message that:
  * names the legacy key,
  * names the replacement `stamp_tax_schedule`,
  * shows a YAML snippet of the two-entry default,
  * references audit P0-4.

#### Scenario: schedule omitted defaults to the canonical CN schedule
- **WHEN** the YAML input does not set `stamp_tax_schedule`
- **THEN** the resolved `CanonicalExchangeCostModel` carries
  `CN_STAMP_TAX_SCHEDULE_DEFAULT` verbatim

### Requirement: A governance test SHALL forbid regression to a scalar field

A test under `tests/governance/` SHALL assert that:

* `CanonicalExchangeCostModel` has no public field named
  `stamp_tax_bps`,
* `PipelineConfig` has no field named `stamp_tax_bps`,
* the walk-forward config dataclass has no field named
  `stamp_tax_bps`,
* none of the shipped `config*.yaml` files contain the literal
  top-level key `stamp_tax_bps`.

#### Scenario: governance test catches a scalar field re-introduction
- **WHEN** a future change re-adds `stamp_tax_bps: float` to any of
  the three dataclasses above
- **THEN** the governance test fails, identifying the offending
  dataclass + field

