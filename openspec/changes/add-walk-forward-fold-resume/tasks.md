# Tasks: Walk-Forward Fold-Level Resume

## OpenSpec (propose stage)

- [x] Draft `proposal.md` (Why / What Changes / Non-Goals)
- [x] Draft `tasks.md`
- [x] Draft `specs/v2-walk-forward-resume/spec.md` (3 ADDED Requirements)

## Implementation

### Manifest module

- [x] `src/core/walk_forward/_resume.py`:
      - `FoldManifest` frozen dataclass with `to_dict` / `from_dict`
      - `ResumeMode` enum (`AUTO`, `FORCE_RERUN`, `RESUME_FROM_FOLD`)
      - `compute_config_fingerprint(config)` — sha256 of
        `dataclasses.asdict(config)` JSON, excluding `output_dir`
      - `FoldManifest.discover(output_dir)` → `dict[int, FoldManifest]`
      - `FoldManifest.save(output_dir)` writes
        `fold_{i:02d}_manifest.json` atomically (tmp+rename)
      - Manifest schema version field (v1) so future changes are
        upgrade-detectable

### Engine integration

- [x] `WalkForwardEngine.run`: new keyword-only
      `resume_mode: ResumeMode = ResumeMode.AUTO` parameter
- [x] Before the fold loop, scan manifests via
      `FoldManifest.discover(output_dir)`
- [x] For each window, decision matrix:
      - `FORCE_RERUN` → always run
      - `RESUME_FROM_FOLD(n)` + `i >= n` → run
      - manifest fingerprint mismatch → run + WARN
      - manifest window mismatch → run + WARN
      - else → load `WalkForwardFold` from manifest, log "resumed",
        keep prior_model_paths populated from manifest's `model_path`
- [x] After successful `_run_single_fold`, write `FoldManifest`

### CLI integration

- [x] `scripts/run_walk_forward.py`: add `argparse`-style CLI flags
      while preserving the legacy `python scripts/run_walk_forward.py
      [config.yaml]` positional form
- [x] `--resume-from-fold N` (int)
- [x] `--no-resume` (bool flag)
- [x] Mutually-exclusive validation (raise ValueError if both set)

## Tests

### `tests/logic/test_walk_forward_resume.py`

- [x] `test_fold_manifest_roundtrip` — save then load returns equal
- [x] `test_config_fingerprint_excludes_output_dir` — same config,
      different output_dir → identical fingerprint
- [x] `test_config_fingerprint_includes_train_months` — changing a
      real field changes the fingerprint
- [x] `test_discover_empty_dir_returns_empty_dict`
- [x] `test_discover_skips_malformed_manifest_json`
- [x] `test_resume_decision_auto_with_matching_manifest_skips`
- [x] `test_resume_decision_auto_with_fingerprint_mismatch_reruns`
- [x] `test_resume_decision_auto_with_window_mismatch_reruns`
- [x] `test_resume_decision_force_rerun_ignores_manifest`
- [x] `test_resume_decision_resume_from_fold_n_runs_at_n_and_above`

### `tests/logic/test_run_walk_forward_cli_resume.py`

- [x] `test_cli_parses_resume_from_fold_int`
- [x] `test_cli_parses_no_resume_flag`
- [x] `test_cli_mutually_exclusive_flags_raise`
- [x] `test_cli_default_is_auto_resume`

## Validation

- [x] `pytest tests/logic/test_walk_forward_resume.py
      tests/logic/test_run_walk_forward_cli_resume.py -q` — green
- [x] No change to `tests/logic/test_run_walk_forward_cli.py` or
      `tests/logic/test_run_walk_forward_mined.py` results
- [x] Engine import cycle smoke test: `import
      src.core.walk_forward.engine` succeeds with the new
      `_resume` import

## Deferred (NOT this proposal)

- Incremental backfill (run more folds on top of an existing
  completed run by extending `overall_end`)
- Per-fold partial resume (a fold that crashed mid-train resumes
  from the train step) — the resume is whole-fold; partial folds
  re-run cleanly
- Operator UI surface ("this run was resumed from fold N")
- Auto-cleanup of stale manifests
