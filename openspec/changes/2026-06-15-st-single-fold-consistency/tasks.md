# Tasks: st-single-fold-consistency

## 0. Step 0 — diagnosis (read-only)
- [x] Mapped the three ST paths: WF (`config_walk.yaml` sets
      namechange_path + governance pin), live (hard-requires), single-fold
      (`config.yaml` had none → `BacktestRunner.run` WARN-pass → ST
      INCLUDED). Confirmed the WARN-pass is deliberately kept for the ~14
      unit `run()` callers + the config-walk governance test comment.
- [x] Confirmed blast radius: the only full `Pipeline.run` test is
      `@skip_unless_e2e`; WF tests mock `_run_single_fold` — so flipping the
      official call sites to `require_st_mask=True` does not break the fast
      suite (it never reaches the real backtest).

## 1. Implementation
- [x] `config.yaml`: `namechange_path` line (parity with config_walk.yaml,
      env-overridable via QUANT_NAMECHANGE_PATH).
- [x] `BacktestRunner.run`: `require_st_mask=False` param; True + missing
      namechange_path → BacktestRunnerError; False keeps the WARN-pass.
- [x] `pipeline.py` + `walk_forward/engine.py` pass `require_st_mask=True`.
- [x] Governance pin `test_config_st_mask_enabled.py` (mirrors config_walk).
- [x] Operator UI: `config_forms.resolve_namechange_path()` +
      `config_run.py` inject an env-defaulted `namechange_path` into the
      standalone job config for BOTH modes — else a UI-launched official run
      RAISES after a full train under `require_st_mask=True`.
- [x] `test_fold0_baseline.py`: replay passes `namechange_path` (fixture
      config → `QUANT_NAMECHANGE_PATH`) + `require_st_mask=True` so it
      excludes ST like the WF run that produced the expected metrics.

## 2. Tests
- [x] `require_st_mask=True` + no namechange_path → raises "ST mask is
      REQUIRED" (through the mock-qlib drive harness).
- [x] `require_st_mask=False` + no namechange_path → WARN-pass, reaches
      strategy construction (back-compat preserved).
- [x] Governance: config.yaml has a non-empty namechange_path.
- [x] UI: `resolve_namechange_path()` falls back to the default, honors
      `QUANT_NAMECHANGE_PATH`, rejects blank env; source-level pin that the
      page injects the key after the mode split and before the preview.

## 3. Verification
- [x] Full fast suite + mypy --strict + ruff.
- [x] docs/audit_rebase_20260611.md E1 closed (REGEN re-baselines fold0).

## REGEN handoff
- The live single-fold canonical path (`main.py config.yaml`) now excludes ST.
- `test_fold0_baseline.py` now replays ST-excluded (namechange_path +
  require_st_mask=True); the regenerated fixture's config block SHOULD record
  the `namechange_path` it was built with (the replay reads it from there,
  env-fallback otherwise). Regenerate the fixture (RUN_E2E) alongside the
  PR-C/D/E execution-timing / price-limit / benchmark moves; record the
  ST-exclusion delta in docs/baseline_20260611.md.
