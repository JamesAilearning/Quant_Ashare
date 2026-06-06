# Tasks: require-post-adjusted-for-mined-factor-wf

## 1. Guard
- [ ] `config.py`: add `_PIT_FEATURE_HANDLERS = frozenset({"MinedFactor"})`
      and import `ADJUST_MODE_POST`.
- [ ] `__post_init__`: after the `adjust_mode in SUPPORTED_ADJUST_MODES`
      check, raise `WalkForwardError` when `feature_handler` is a PIT handler
      and `adjust_mode != ADJUST_MODE_POST` (message names the value + the
      required `adjust_mode: "post_adjusted"`).

## 2. Fix shipped + committed MinedFactor configs (retroactive guard safety)
- [ ] `config_walk_mined.yaml`: add `adjust_mode: "post_adjusted"`.
- [ ] `test_run_walk_forward_mined.py`: add `adjust_mode: "post_adjusted"` to
      every MinedFactor test YAML (6 sites, incl. error-path tests whose
      `_load_config` constructs `WalkForwardConfig` before the bundle helper).
- [ ] `test_walk_forward_resume.py`: `test_includes_feature_handler` — set
      `adjust_mode="post_adjusted"` on both configs (still isolates
      `feature_handler`).

## 3. Tests (new, test_walk_forward.py)
- [ ] MinedFactor + `pre_adjusted` → `WalkForwardError` at construction; message
      names the required `post_adjusted`.
- [ ] MinedFactor + `post_adjusted` → constructs OK.
- [ ] Alpha158 + `pre_adjusted` → constructs OK (PIT rule does not apply).
- [ ] shipped `config_walk_mined.yaml` loads + constructs cleanly under the guard.

## 4. Quality gates
- [ ] `pytest tests/logic/` (walk_forward + mined + resume) green.
- [ ] `ruff check` clean on changed files.
- [ ] `mypy` (CI command) clean on `config.py`.
- [ ] `openspec validate 2026-06-05-require-post-adjusted-for-mined-factor-wf`.
