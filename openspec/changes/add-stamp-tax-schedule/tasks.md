## 1. Schema in `src/core/canonical_backtest_contract.py`

- [ ] 1.1 Add frozen `StampTaxScheduleEntry` dataclass with
  `effective_from: date`, `bps: float`, `__post_init__` rejecting
  wrong types and out-of-range bps.
- [ ] 1.2 Add `CN_STAMP_TAX_SCHEDULE_DEFAULT: tuple[StampTaxScheduleEntry, ...]`
  with entries `(2008-09-19, 10.0)` and `(2023-08-28, 5.0)`.
- [ ] 1.3 Add `compute_effective_stamp_tax_bps(schedule,
  period_start, period_end, *, calendar=None) -> EffectiveStampTaxBps`
  helper returning the trading-day-weighted scalar AND the list of
  in-period transitions. Reject `period_start <
  schedule[0].effective_from` with `CanonicalBacktestContractError`.
- [ ] 1.4 Replace `CanonicalExchangeCostModel.stamp_tax_bps: float`
  with `stamp_tax_schedule: tuple[StampTaxScheduleEntry, ...]`. Add
  validation: non-empty, entries sorted by `effective_from` ascending,
  no duplicate dates, all bps in `[0, STAMP_TAX_BPS_MAX]`.
- [ ] 1.5 Remove the scalar `stamp_tax_bps` field reference from
  `CanonicalExchangeCostModel._check_numeric` enumeration loop.

## 2. Runtime in `src/core/backtest_runner.py`

- [ ] 2.1 Replace `stamp_tax_fraction = cost.stamp_tax_bps / 10000.0`
  with a call to `compute_effective_stamp_tax_bps(
  cost.stamp_tax_schedule, request.evaluation_start,
  request.evaluation_end)`.
- [ ] 2.2 When the returned `transitions` list is non-empty, emit a
  single `_logger.warning(...)` listing each transition's
  `effective_from`, the pre-transition rate, the post-transition
  rate, and the trading-day-weighted scalar that was used. Wording
  cites audit P0-4 + the proposal name.

## 3. PipelineConfig + WalkForwardConfig migration

- [ ] 3.1 In `src/core/pipeline.py`:
  - Remove `stamp_tax_bps: float = 10.0` from `PipelineConfig`.
  - Add `stamp_tax_schedule: Sequence[Mapping[str, Any]] | None = None`.
  - Update `_check_numeric` enumeration to drop `stamp_tax_bps`.
  - In `Pipeline.run` (around the `CanonicalExchangeCostModel`
    construction), convert the YAML-shaped schedule (or `None`) to
    `tuple[StampTaxScheduleEntry, ...]`. Helper:
    `_resolve_stamp_tax_schedule(config_value)`.
- [ ] 3.2 In `src/core/walk_forward/config.py` mirror the same
  migration so walk-forward and pipeline accept the same schema.
- [ ] 3.3 In `src/core/walk_forward/engine.py`, propagate the
  resolved schedule into the per-fold `CanonicalExchangeCostModel`.
- [ ] 3.4 Both configs' YAML loaders detect the legacy
  `stamp_tax_bps` key and raise with a precise migration error.

## 4. YAML config migration

- [ ] 4.1 `config.yaml`: drop `stamp_tax_bps`, no replacement
  (defaults to `CN_STAMP_TAX_SCHEDULE_DEFAULT` since the window
  spans the reform).
- [ ] 4.2 `config_walk.yaml`: same.
- [ ] 4.3 `config_smoke.yaml`: drop `stamp_tax_bps`, no
  replacement. The smoke window (2024-2025) is entirely
  post-reform; the default still produces the right
  per-segment rate.

## 5. Regression baseline migration

- [ ] 5.1 In `tests/regression/test_fold0_baseline.py`, change the
  `CanonicalExchangeCostModel(...)` construction in the test from
  `stamp_tax_bps=10.0` to `stamp_tax_schedule=CN_STAMP_TAX_SCHEDULE_DEFAULT`.
- [ ] 5.2 If the fold-0 fixture window crosses 2023-08-28: bump
  `tolerance.annualized_return_absolute` from `0.005` to `0.010`
  to absorb the one-shot rate correction. Document the bump in the
  test docstring. (The fixture remains; only the construction
  arguments change.)
- [ ] 5.3 If the fold-0 fixture window does NOT cross the reform:
  leave tolerances untouched; document that the default schedule
  produces the same per-segment rate as the legacy 10.0 scalar for
  that window.

## 6. Unit tests in `tests/logic/`

- [ ] 6.1 New `tests/logic/test_stamp_tax_schedule.py`:
  - Construction: valid schedule round-trips through asdict.
  - Construction rejection: empty schedule, non-monotone dates,
    duplicate dates, bps out of range, wrong types.
  - `compute_effective_stamp_tax_bps` single-entry coverage →
    scalar matches the entry's bps.
  - `compute_effective_stamp_tax_bps` crosses one transition →
    weighted scalar between the two rates, transitions list has
    one element.
  - `compute_effective_stamp_tax_bps` crosses two transitions →
    weighted scalar across three segments.
  - `compute_effective_stamp_tax_bps` period before schedule
    start → raises `CanonicalBacktestContractError` naming both
    dates.
  - `compute_effective_stamp_tax_bps` calendar mode default
    (calendar-day weighting when no trading calendar passed) vs
    explicit trading-day calendar — both produce numerically close
    results on a clean monthly window, differ on a long-holiday
    window.

- [ ] 6.2 Update `tests/logic/test_pipeline.py`:
  - Wherever `PipelineConfig(stamp_tax_bps=...)` was constructed,
    replace with `stamp_tax_schedule=[...]` or rely on default.
  - Add a test: legacy `stamp_tax_bps` key in YAML raises a
    `ConfigError` whose message contains both the old key, the new
    key, and a YAML snippet.

- [ ] 6.3 Update `tests/logic/test_walk_forward.py` parallel to 6.2.

- [ ] 6.4 Update `tests/logic/test_backtest_runner.py`:
  - Existing test fixtures migrate to schedule.
  - New test: a request whose period crosses the reform emits a
    single WARN log containing both pre/post rates and the
    weighted scalar.
  - New test: a request entirely on one side of the reform does
    NOT emit the WARN.

## 7. Contract tests in `tests/governance/`

- [ ] 7.1 Update `tests/governance/test_canonical_backtest_contract.py`:
  - `_valid_request` uses the new schedule field.
  - Add a test: `CanonicalExchangeCostModel` rejects a
    `stamp_tax_schedule` whose bps is `> STAMP_TAX_BPS_MAX`.
  - Add a test: rejects a schedule with non-monotone dates.
  - Add a test: `CANONICAL_INPUT_REQUIRED_FIELDS` still
    references the cost model (sanity).

- [ ] 7.2 New `tests/governance/test_stamp_tax_schedule_no_scalar_regression.py`:
  - Asserts `CanonicalExchangeCostModel` has NO field named
    `stamp_tax_bps` (AST inspection).
  - Asserts `PipelineConfig` has NO field named `stamp_tax_bps`.
  - Asserts `WalkForwardConfig` has NO field named `stamp_tax_bps`.
  - Asserts no shipped YAML (`config.yaml`, `config_walk.yaml`,
    `config_smoke.yaml`) contains the literal `stamp_tax_bps:` key
    at top-level.

## 8. Spec + OpenSpec validation

- [ ] 8.1 Write the spec delta at
  `openspec/changes/add-stamp-tax-schedule/specs/v2-canonical-backtest-contract/spec.md`
  with MODIFIED + ADDED Requirements.
- [ ] 8.2 Run `openspec validate --specs --strict` if available
  in CI; otherwise smoke-check the spec file parses.
- [ ] 8.3 Update `openspec/specs/v2-canonical-backtest-contract/spec.md`
  with the migrated requirements once the apply phase is done
  (per the OpenSpec workflow — this is the "archive" step).

## 9. Manual verification

- [ ] 9.1 `pytest tests/logic/ tests/governance/` → 0 unexpected
  failures.
- [ ] 9.2 `ruff check src/ tests/ scripts/` → clean.
- [ ] 9.3 Spot-check: load `config_walk.yaml`, construct
  `CanonicalBacktestInput`, run a tiny synthetic backtest, observe
  the WARN log fires exactly once with both rates and the weighted
  scalar.
