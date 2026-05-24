## MODIFIED Requirements

### Requirement: Validity filters SHALL enforce coverage, variance, and sanity constraints

`passes_validity(result, config)` SHALL return False if any of: (a) `result.coverage < config.coverage_min` (default 0.8), (b) the fraction of dates with cross-sectional std above `config.variance_min` (default 1e-6) is below `config.variance_days_frac_min` (default 0.7), or (c) the fraction of **finite cells** in `result.factor_values` whose absolute value exceeds `config.extreme_outlier_magnitude` (default 1e8) exceeds `config.extreme_outlier_frac_max` (default 0.05). The sanity check's denominator SHALL be the count of finite cells (`np.isfinite(arr).sum()`), NOT the total cell count, and non-finite (NaN / Inf) cells SHALL NOT count as outliers. This separates the sanity check (a magnitude filter on the finite fraction) from the coverage check (a NaN-density filter); an earlier implementation used the total-cell denominator and counted non-finite cells as outliers, which made the effective binding constraint coverage ≥ `1 - extreme_outlier_frac_max` (≥ 0.95 with defaults), strictly tighter than the design doc's `coverage_min = 0.80`. An all-NaN factor SHALL return 0.0 for the sanity check (the metric is undefined; coverage_min is the binding rejection in that case). Otherwise `passes_validity` returns True. (The data-leakage filter from `factor_mining_design.md` §5.2 item 3 is enforced by the Phase 1 grammar's scale-invariance gate and does not need a runtime check.)

#### Scenario: a near-constant factor fails the variance check
- **WHEN** `passes_validity` is called on a factor whose cross-sectional std is below 1e-6 on 50 % of dates with default config
- **THEN** the function returns False
- **AND** `compute_fitness` on the same result returns `-inf`

#### Scenario: 30% NaN with bounded finite values passes the sanity check
- **WHEN** `passes_validity` is called on a factor with 30 % NaN cells whose finite values are all bounded (e.g. samples from `N(0, 1)`), with `coverage_min = 0.0` and `variance_days_frac_min = 0.0` (so sanity is the only check)
- **THEN** the function returns True
- **AND** the sanity check does NOT double-count the 30 % NaN cells as outliers

#### Scenario: extreme outliers in finite cells still fail the sanity check
- **WHEN** `passes_validity` is called on a factor with 30 % NaN cells AND 10 % of the remaining finite cells set to magnitude `1e10`, with `coverage_min = 0.0`, `variance_days_frac_min = 0.0`, and `extreme_outlier_frac_max = 0.05`
- **THEN** the function returns False
- **AND** the rejection is driven by the finite-cell magnitude check (10 % of finite cells > 5 % threshold)

#### Scenario: an all-NaN factor does not crash the sanity check
- **WHEN** `passes_validity` is called on a factor whose every cell is NaN, with `coverage_min = 0.0` and `variance_days_frac_min = 0.0`
- **THEN** the function returns True (the sanity metric is undefined; coverage_min is the binding rejection in production configs, and we disabled it here to isolate the sanity outcome)
- **AND** no exception is raised
