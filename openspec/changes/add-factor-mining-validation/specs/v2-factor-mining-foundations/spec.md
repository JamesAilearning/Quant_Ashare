## MODIFIED Requirements

### Requirement: Phase 1 SHALL NOT access qlib, PIT data, or any data source

Code under `src/factor_mining/` SHALL NOT import from `qlib`, SHALL NOT call `qlib.init`, and SHALL NOT reference `qlib.data.D`. A repository-wide grep for `qlib\.data`, `qlib\.init`, or `from qlib` under `src/factor_mining/` MUST return zero matches. The PIT layer SHALL be reached only through `src/factor_mining/pit_adapter.py`, which is the designated data door; only `pit_adapter.py` MAY import `PITDataProvider` from `src.pit.query`. Other modules under `src/factor_mining/` (including Phase 3's `gp_engine.py` / `miner.py` and Phase 6's `validator.py` / `promote.py`) SHALL NOT import `src.pit` directly. `miner.py` and `promote.py` MAY consume PIT data only via `FactorMiningDataView` instances they construct around `PITDataProvider` (i.e. the PIT entry point is still `pit_adapter.py`). The qlib direct-import ban remains absolute for the entire subpackage.

#### Scenario: a developer runs the strict data-gate grep
- **WHEN** a developer runs `grep -rn "qlib\.data\|qlib\.init\|from qlib" src/factor_mining/`
- **THEN** the output is empty (zero matches)
- **AND** any non-empty output is treated as a scope violation, not an acceptable exception

#### Scenario: validator.py or promote.py attempts to import src.pit directly
- **WHEN** a module under `src/factor_mining/` other than `pit_adapter.py` adds `from src.pit.query import …` or `import src.pit`
- **THEN** the change is rejected at review
- **AND** the reviewer directs the contributor to route the call through `FactorMiningDataView` in `pit_adapter.py`

#### Scenario: promote.py constructs a panel in PIT mode
- **WHEN** a reviewer inspects `src/factor_mining/promote.py`'s PIT-mode branch
- **THEN** the file constructs `FactorMiningDataView` and consumes its `load_panel()` / `forward_return()` outputs
- **AND** the file does NOT import or call `PITDataProvider.get_features` directly

## ADDED Requirements

### Requirement: Validator SHALL split a pool on a configured IS/OOS date and reject too-short segments

`src/factor_mining/validator.py` SHALL expose `validate_pool(pool, panel, forward_return, criteria) -> list[FactorValidationResult]`. The validator SHALL slice the panel and forward-return on `criteria.is_oos_split_date` so that dates strictly before the split are IS and dates on/after are OOS. If either segment yields fewer than `criteria.min_obs_per_segment` observations (joint non-NaN cells), every factor in the pool SHALL fail validation with a `segment_too_short` reason — no OOS metric is computed in this case.

#### Scenario: too few OOS dates
- **WHEN** `validate_pool` is called with a panel whose OOS segment has only 5 trading dates and `criteria.min_obs_per_segment = 30`
- **THEN** every returned `FactorValidationResult.passes` is `False`
- **AND** every result's `reasons` contains `oos_segment_too_short`

#### Scenario: standard split with adequate segments
- **WHEN** `validate_pool` is called with a panel where both segments have ≥ `min_obs_per_segment` dates
- **THEN** each factor's `is_n_obs` and `oos_n_obs` are populated with non-zero counts
- **AND** the `passes` flag depends on the OOS metric thresholds (not the segment-length check)

### Requirement: Validator SHALL reject factors whose OOS metrics fall below thresholds

For each pool entry the validator SHALL evaluate the factor against the OOS slice and SHALL set `passes = False` if `abs(oos_ir) < criteria.min_oos_ir` OR `abs(oos_rank_ic_mean) < criteria.min_oos_rank_ic_mean`. NaN values SHALL be treated as 0 for the threshold comparison. The failure reasons SHALL list each violated criterion as a distinct string so an operator can see exactly which threshold the factor missed.

#### Scenario: classic overfit pattern is rejected
- **WHEN** a factor has `is_ir = +∞ / NaN` (IS rank-IC perfect every day) but `oos_ir ≈ 0` (OOS factor uncorrelated with label) and `criteria.min_oos_ir = 0.3`
- **THEN** `validate_pool` returns a result with `passes = False`
- **AND** the `reasons` tuple contains `oos_ir_below_threshold`

#### Scenario: stable factor passes
- **WHEN** a factor has `oos_ir = 0.5` and `oos_rank_ic_mean = 0.04` against `min_oos_ir = 0.3` and `min_oos_rank_ic_mean = 0.02`
- **THEN** `validate_pool` returns a result with `passes = True`
- **AND** `reasons` is the empty tuple

### Requirement: Validator SHALL filter pairwise-correlated factors after per-factor pass

`src/factor_mining/validator.py` SHALL expose `filter_correlated(results, panel, criteria) -> list[FactorValidationResult]` that processes the per-factor results sorted by `fitness` desc. For each result, it SHALL compute the max absolute Pearson correlation against every already-kept higher-fitness result's factor values (evaluated against the full panel). When that max correlation exceeds `criteria.max_pool_correlation`, the result's `passes` SHALL be set to `False` (or kept False if already failing) and the reason `correlated_with_higher_fitness` SHALL be appended.

#### Scenario: a high-fitness factor and a near-duplicate low-fitness factor
- **WHEN** `filter_correlated` is called on two passing results whose factor-value correlation is 0.9 and `max_pool_correlation = 0.6`
- **THEN** the higher-fitness result remains `passes=True`
- **AND** the lower-fitness result becomes `passes=False` with reason `correlated_with_higher_fitness`

#### Scenario: two uncorrelated factors both pass
- **WHEN** `filter_correlated` is called on two passing results whose factor-value correlation is 0.2 and `max_pool_correlation = 0.6`
- **THEN** both retain `passes=True`

### Requirement: Promotion CLI SHALL be manual-gated with dry-run support

`src/factor_mining/promote.py` SHALL expose `promote_run(config, *, dry_run=False) -> PromotionReport` and a `python -m src.factor_mining.promote --run … --to … [--config …] [--dry-run]` CLI entry. The CLI SHALL NEVER promote automatically — every invocation is a human action per `decisions.md` D4. With `--dry-run`, the report is produced but no files are written. Without `--dry-run`, a successful promote SHALL create `<production_dir>/<version>/` and write `factor_pool.parquet`, `factor_expressions.json`, and `promotion_report.json` under it. The CLI SHALL refuse to overwrite an existing version directory (operator must choose a new label); attempting to do so SHALL raise an error before any file is written.

#### Scenario: dry-run produces a report but writes nothing
- **WHEN** `promote_run(config, dry_run=True)` is called
- **THEN** the returned `PromotionReport.output_dir` is None
- **AND** no files are written under `<production_dir>/<version>/`

#### Scenario: normal run writes three files
- **WHEN** `promote_run(config, dry_run=False)` succeeds on a fresh version label
- **THEN** `<production_dir>/<version>/factor_pool.parquet` exists
- **AND** `<production_dir>/<version>/factor_expressions.json` exists
- **AND** `<production_dir>/<version>/promotion_report.json` exists

#### Scenario: refusing to overwrite an existing version directory
- **WHEN** `promote_run` is called with a `version` whose directory already exists under `production_dir`
- **THEN** the call raises an error before any file is written
- **AND** the error message names the conflicting directory and suggests choosing a new version label

#### Scenario: CLI invocation exit codes
- **WHEN** `python -m src.factor_mining.promote --run <good_run> --to v1` is invoked with valid arguments
- **THEN** the process exits 0
- **AND** prints a one-line summary of the kept count

- **WHEN** `python -m src.factor_mining.promote --run <missing_path> --to v1` is invoked
- **THEN** the process exits non-zero
- **AND** prints a clear error message
