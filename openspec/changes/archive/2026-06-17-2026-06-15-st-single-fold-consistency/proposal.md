# Proposal: st-single-fold-consistency

## Why

Audit E1: the PIT historical ST/*ST exclusion was inconsistent across the
three backtest paths. The walk-forward path (`config_walk.yaml`) sets
`namechange_path`, so it excludes ST; the live recommend path hard-requires
ST exclusion; but the SINGLE-FOLD canonical path (`main.py config.yaml` →
`PipelineConfig` → `BacktestRunner.run`) set no `namechange_path`, so
`BacktestRunner.run` took its backward-compatible WARN-pass and ran an
ST-INCLUDED universe. Single-fold official metrics were therefore not
comparable to walk-forward or live, and a blanked config line would silently
revert either path to includes-ST.

## What Changes

- **`config.yaml`** + **`config_smoke.yaml`**: add `namechange_path:
  "${QUANT_NAMECHANGE_PATH:-…/all_namechanges.parquet}"` — parity with
  `config_walk.yaml`, enabling the single-fold ST mask. Both are standalone
  single-fold configs run via `main.py <config>` (no `extends`); the
  `config_walk_n*` / `config_walk_mined` variants inherit the key from
  `config_walk.yaml` via `extends`.
- **`BacktestRunner.run`**: new `require_st_mask: bool = False`. When True
  and `namechange_path` is missing/blank, the run RAISES instead of taking
  the WARN-pass — the single-fold backtest must exclude ST exactly like
  walk-forward and live. The WARN-pass survives ONLY for raw research/unit
  callers (`require_st_mask=False`, the backward-compatible default the
  existing governance/unit tests rely on).
- **Official call sites** pass `require_st_mask=True`: `src/core/pipeline.py`
  (single-fold) and `src/core/walk_forward/engine.py` (each fold). So both
  official config paths now fail loud on a missing namechange_path; the raw
  `BacktestRunner.run` entry stays permissive for research.
- **Governance pin** `tests/governance/test_config_st_mask_enabled.py`:
  mirrors `test_config_walk_st_mask_enabled.py` for config.yaml, AND sweeps
  every shipped root `config*.yaml` backtest config (single-fold + walk-forward,
  resolving `extends` + `${VAR:-default}`) so a future standalone config can't
  ship without the key — Codex caught `config_smoke.yaml` slipping past the
  single-file pin. The RUN_E2E baseline drift test is invisible to CI.
- **Operator UI** (`web/operator_ui/pages/config_run.py` +
  `config_forms.py`): the UI emits a STANDALONE job config (no `extends`, and
  the runner does not run it through the `${VAR:-default}` YAML loader), so it
  now injects an env-defaulted `namechange_path`
  (`resolve_namechange_path()`, override via `QUANT_NAMECHANGE_PATH`) into the
  generated pipeline AND walk-forward config. Without this, a UI-launched
  official run would RAISE deep inside the backtest — *after* a full train —
  under the new `require_st_mask=True`.
- **`tests/regression/test_fold0_baseline.py`**: the replay now passes
  `namechange_path` (from the fixture config, falling back to
  `QUANT_NAMECHANGE_PATH`) + `require_st_mask=True`, so it excludes ST exactly
  like the official run that produced the expected-metrics fixture (see Impact
  point 2 below).

## Impact on recorded metrics

Two distinct artifacts move, for two distinct reasons — keep them separate:

1. **The live single-fold canonical path** (`python main.py config.yaml` →
   `PipelineConfig` → `BacktestRunner.run`). Enabling its ST mask drops the
   ST/*ST names from the Top-K, so its recorded metrics shift vs the
   previously includes-ST single-fold run. There is no committed fixture for
   this path; it re-baselines whenever an operator next runs it.
2. **The `test_fold0_baseline.py` regression fixture.** This fixture replays a
   *walk-forward* fold0's pre-mask predictions (`fixtures/README.md`), so it
   was already ST-masked upstream by the WF run that generated the expected
   metrics — yet the replay previously called `BacktestRunner.run` with no
   `namechange_path` (runner WARN-pass), comparing an ST-INCLUDED Top-K
   against ST-EXCLUDED baselines. PR-F aligns the replay so it excludes ST
   exactly like the run that produced the fixture. The fixture is regenerated
   at REGEN alongside the PR-C/D/E execution-timing / price-limit / benchmark
   moves; the RUN_E2E drift gate is not run in CI.

This mirrors the walk-forward C1-baseline note when its `namechange_path` was
first enabled.

## Non-Goals

- No re-baseline in this PR (REGEN owns the RUN_E2E fixture regeneration).
- No change to the ST mask MECHANICS (execution-day keying, st_history
  reconstruction) — only WHERE/whether it is mandatory.
- The raw `BacktestRunner.run` WARN-pass is deliberately retained for
  research/unit callers; only the official paths are made strict.
