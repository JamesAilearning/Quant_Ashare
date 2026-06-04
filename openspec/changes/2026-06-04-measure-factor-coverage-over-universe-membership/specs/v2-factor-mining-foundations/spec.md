# Spec delta: v2-factor-mining-foundations

## MODIFIED Requirements

### Requirement: Evaluator SHALL produce IC, IR, RankIC, turnover, and coverage from one factor

`src/factor_mining/evaluator.py` SHALL expose `evaluate_factor(expr, panel, forward_return, *, method, universe_mask=None)` returning an `EvaluationResult` frozen dataclass with at minimum: `factor_values` (date × ticker), `ic_mean`, `ic_std`, `ir`, `rank_ic_mean`, `rank_ic_std`, `rank_ir`, `turnover_daily`, `coverage`, `n_obs_per_day_min`. The IC computation SHALL reuse `src.core._ic_utils.compute_ic_for_group` (per `inventory.md` §B.3 recommendation) and SHALL set IR to NaN when the corresponding IC std is below 1e-9 (per `inventory.md` §B.4).

`coverage` SHALL be computed **relative to universe membership** when a `universe_mask` (boolean date × ticker frame) is supplied: the denominator is the count of (date, ticker) cells where the ticker is a universe member on that day, and the numerator is the count of those member cells that also carry a finite factor value. This is required for survivorship-corrected PIT panels, whose union matrix is ~40 % NaN purely because most union tickers are non-members on any given day; an all-cells denominator makes `coverage_min` unsatisfiable (a perfect factor scores ~0.62 and is rejected, so a full GP run returns an empty pool). When `universe_mask` is None, `coverage` SHALL fall back to the all-cells non-NaN fraction (the legacy behaviour for synthetic / dense panels). The `universe_mask` parameter SHALL be optional and SHALL NOT introduce any `qlib` or `src.pit` import into `evaluator.py` (the mask is produced by `FactorMiningDataView.universe_mask`, the pit_adapter door, and passed in as a DataFrame), preserving the D5 strict gate.

#### Scenario: a factor perfectly correlated with the label
- **WHEN** `evaluate_factor` is called on a synthetic factor that equals forward_return on every (date, ticker) cell
- **THEN** the returned `rank_ic_mean` is approximately 1.0
- **AND** `rank_ir` is finite and large

#### Scenario: a constant factor across dates
- **WHEN** `evaluate_factor` is called on a factor whose value does not change day-to-day for any ticker
- **THEN** the returned `turnover_daily` is 0.0
- **AND** the cross-sectional IC is well-defined for the date dimension (no NaN injection from turnover alone)

#### Scenario: IR convention on zero IC variance
- **WHEN** `evaluate_factor` produces an `ic_std` strictly less than 1e-9
- **THEN** the returned `ir` is NaN (not 0.0)
- **AND** this matches `signal_analyzer.py` / `factor_analyzer.py` IR convention from `inventory.md` §B.4

#### Scenario: coverage is members-relative when a universe mask is supplied
- **WHEN** `evaluate_factor` is called with a `universe_mask` whose non-member cells (mask False) are NaN in the factor and whose member cells (mask True) are all finite
- **THEN** the returned `coverage` is ~1.0 (non-member NaN cells are excluded from the denominator)
- **AND** the same call without `universe_mask` returns the lower all-cells fraction

#### Scenario: a NaN on a member cell still reduces members-relative coverage
- **WHEN** `evaluate_factor` is called with a `universe_mask` and the factor is NaN on a cell where the ticker IS a member that day
- **THEN** that cell counts against `coverage` (numerator excludes it, denominator includes it), so the validity gate still rejects genuinely-undefined factors

#### Scenario: no universe mask reproduces legacy all-cells coverage
- **WHEN** `evaluate_factor` is called with `universe_mask=None` (the default) on a panel with some NaN factor cells
- **THEN** `coverage` equals the count of non-NaN cells divided by the total cell count (the pre-change behaviour, preserved for synthetic / dense panels)
