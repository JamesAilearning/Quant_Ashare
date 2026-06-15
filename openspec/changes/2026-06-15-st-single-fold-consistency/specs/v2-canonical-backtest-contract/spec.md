# v2-canonical-backtest-contract Specification (delta)

## ADDED Requirements

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
