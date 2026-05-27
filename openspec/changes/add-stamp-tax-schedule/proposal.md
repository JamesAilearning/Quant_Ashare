## Why

`CanonicalExchangeCostModel.stamp_tax_bps: float` is a single scalar
applied to every sell across the entire backtest period. But CN
A-share stamp tax actually changed in the middle of the realistic
backtest window:

- 2008-09-19 onwards: **0.1%** sell-side only (10 bps)
- 2023-08-28 onwards: **0.05%** sell-side only (5 bps), halved by the
  MOF reform

Every config that ships in the repo (`config.yaml`, `config_walk.yaml`,
`config_smoke.yaml`, the regression baseline fixture) currently uses
`stamp_tax_bps: 10.0` throughout. The two walk-forward configs cover
`2022-01-01 → 2026-02-28`, which spans the reform date. The smoke
config covers `2024-01-01 → 2025-10-31`, which is entirely post-
reform but still uses the old 10.0 rate.

The systemic bias on a real walk-forward run:

- ~600 days at the pre-reform rate (~Jan 2022 → Aug 2023)
- ~880 days at the post-reform rate (~Aug 2023 → Feb 2026)
- ~5 bp delta on every sell that lands in the post-reform stretch
- Annualised: 5 bp × 1-3× turnover ≈ 5-15 bp/yr systematic
  understatement of post-Aug-2023 returns

This is the "looks right, silently wrong" cost-model failure the V2
governance baseline was meant to prevent (see the earlier
`add-canonical-backtest-input-for-quant-risks` change). Audit P0-4.

## What Changes

- Replace `CanonicalExchangeCostModel.stamp_tax_bps: float` with a
  required `stamp_tax_schedule: tuple[StampTaxScheduleEntry, ...]`
  field. Each entry is `(effective_from: date, bps: float)`;
  entries are sorted by date and the first entry's
  `effective_from` defines the start of coverage.
- Introduce a `StampTaxScheduleEntry` frozen dataclass (immutable +
  serialisable) and a module-level
  `CN_STAMP_TAX_SCHEDULE_DEFAULT` constant with the two known
  transitions (2008-09-19 → 10 bps, 2023-08-28 → 5 bps).
- Introduce a `compute_effective_stamp_tax_bps(schedule, period_start,
  period_end) -> EffectiveStampTaxBps` helper that returns a single
  scalar bps suitable for qlib's `exchange_kwargs["close_cost"]`
  AND a list of in-period transitions:
  - Period covered by exactly one schedule entry → return that
    entry's rate, transitions list empty.
  - Period crosses at least one transition → return the
    **trading-day-weighted average** of the per-segment rates, and
    populate the transitions list so the caller can emit a WARN.
  - Period starts before the schedule's first
    `effective_from` → raise `CanonicalBacktestContractError`. We do
    NOT silently extrapolate the earliest rate backwards (the user
    asked for an explicit pre-2008 backtest must opt in by extending
    their own schedule).
- `BacktestRunner` calls the helper, uses the returned scalar in
  `exchange_kwargs`, and emits a single `_logger.warning(...)`
  describing each transition crossed (date + pre-rate + post-rate +
  the weighted scalar used). The WARN is intentionally per-run, not
  per-day, so it does not flood logs.
- `PipelineConfig` and `WalkForwardConfig` replace
  `stamp_tax_bps: float = 10.0` with
  `stamp_tax_schedule: Sequence[Mapping[str, Any]] | None = None`
  (None means "use `CN_STAMP_TAX_SCHEDULE_DEFAULT`"). Both configs
  detect the legacy `stamp_tax_bps` key in YAML payloads and raise
  with a precise migration message instead of silently dropping it.
- All three shipped YAML configs (`config.yaml`, `config_walk.yaml`,
  `config_smoke.yaml`) and the fold-0 regression baseline are
  migrated to the new schema. The two walk-forward configs use the
  full default schedule (they cross 2023-08-28); the smoke config
  uses a single-entry schedule at 5 bps from 2008-09-19 (its window
  is entirely post-reform).
- Add a governance test asserting `stamp_tax_bps` is no longer a
  field name in `CanonicalExchangeCostModel` /
  `PipelineConfig` / `WalkForwardConfig` and not present as a YAML
  key in any shipped config. Prevents future regression to the
  scalar form.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `v2-canonical-backtest-contract`: the cost-model boundary gains a
  schedule-typed `stamp_tax_schedule` field, replacing the scalar
  `stamp_tax_bps`. The canonical official-metrics anchor, the qlib
  `backtest.backtest` callable, and every other field on
  `CanonicalExchangeConfig` are unchanged.

## Impact

- **Migration**: All operator-side YAML configs that previously set
  `stamp_tax_bps` MUST be updated. The migration error in the YAML
  loader points at the new key. Shipped configs are migrated in this
  change.
- **Numeric drift**: Any walk-forward run that previously used the
  default `stamp_tax_bps: 10.0` and spans 2023-08-28 will see a
  small (~2-5 bp) reduction in post-Aug-2023 sell costs. The
  fold-0 regression fixture is regenerated (or its tolerances
  loosened — see tasks 5.1-5.3) so the test reflects the corrected
  rate.
- **Backwards-incompatible API**: `CanonicalExchangeCostModel`
  consumers that construct the dataclass directly (test fixtures,
  research scripts) MUST migrate to the new field. The repo's
  governance test catches every in-tree caller; out-of-tree
  consumers will get a clear `TypeError` at construction.
