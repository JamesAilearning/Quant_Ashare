## ADDED Requirements

### Requirement: Walk-forward engine SHALL persist a per-fold manifest after each successful fold

After `WalkForwardEngine._run_single_fold` completes successfully (returns a `WalkForwardFold`), the engine SHALL write a `fold_{i:02d}_manifest.json` file in the run's `output_dir`. The manifest SHALL include:

- `version` — schema version integer (currently `1`)
- `fold_index` — int
- `train_period` / `valid_period` / `test_period` — strings (the same format the report uses)
- `config_fingerprint` — sha256 hex digest (first 16 chars sufficient) of `dataclasses.asdict(config)` JSON with `output_dir` removed before hashing
- `model_path` / `report_path` / `predictions_path` — string paths relative to or absolute under `output_dir`
- `positions_path` — string path or null (positions are only written when backtest produced positions)
- `completed_at` — ISO-8601 UTC timestamp
- `fold` — the `WalkForwardFold` dataclass as a dict (so `discover` can reconstruct without re-running)

The write SHALL be atomic (write to `*.tmp` then rename) so a crash mid-write does not produce a half-written manifest that `discover` would parse incorrectly.

A fold that fails (the engine catches and emits a NaN-placeholder fold) SHALL NOT write a manifest, so subsequent resumes do not skip a fold that previously crashed.

#### Scenario: successful fold writes manifest with all fields populated
- **WHEN** `_run_single_fold` returns a non-placeholder `WalkForwardFold` for fold 3
- **THEN** `output_dir/fold_03_manifest.json` exists
- **AND** the manifest's `fold_index == 3`
- **AND** the manifest's `config_fingerprint` matches `compute_config_fingerprint(config)`
- **AND** the manifest's `model_path` equals `f"{output_dir}/model_fold3.pkl"`

#### Scenario: crashed fold does not write manifest
- **WHEN** `_run_single_fold` raises an exception and the caller replaces it with a NaN-placeholder fold
- **THEN** no `fold_{i:02d}_manifest.json` is created for that fold
- **AND** a subsequent `WalkForwardEngine.run` with `ResumeMode.AUTO` re-runs that fold

### Requirement: Walk-forward engine SHALL skip folds whose manifest matches the current config

`WalkForwardEngine.run` SHALL accept a keyword-only `resume_mode: ResumeMode = ResumeMode.AUTO` parameter. Before the fold loop, the engine SHALL scan `output_dir` for `fold_*_manifest.json` files via `FoldManifest.discover`. For each fold window:

- If `resume_mode == ResumeMode.FORCE_RERUN`, run the fold and overwrite any existing artifacts.
- If `resume_mode == ResumeMode.RESUME_FROM_FOLD(n)` and `i >= n`, run the fold.
- Otherwise (AUTO, or `RESUME_FROM_FOLD(n)` with `i < n`), check the discovered manifests:
  - If `i` not in discovered manifests → run the fold.
  - If discovered manifest's `config_fingerprint` ≠ current fingerprint → log WARNING and re-run.
  - If discovered manifest's `train_period`/`test_period` ≠ current window → log WARNING and re-run.
  - Otherwise → reconstruct the `WalkForwardFold` from the manifest, log "fold N: resumed from manifest", and add `(i, model_path)` to `prior_model_paths` so the ensemble logic sees the same set it would have seen on a fresh run.

#### Scenario: AUTO resume with matching manifests skips re-execution
- **GIVEN** `output_dir/fold_00_manifest.json` and `fold_01_manifest.json` both exist with `config_fingerprint == compute_config_fingerprint(config)`
- **WHEN** `WalkForwardEngine.run(config, resume_mode=ResumeMode.AUTO)` is called
- **THEN** `_run_single_fold` is NOT called for fold_index 0 or 1
- **AND** the resulting `WalkForwardResult.folds[0]` equals the `WalkForwardFold` deserialised from `fold_00_manifest.json`
- **AND** `_run_single_fold` IS called for fold_index 2 (and beyond)

#### Scenario: fingerprint mismatch re-runs even with manifest present
- **GIVEN** `output_dir/fold_00_manifest.json` exists with a `config_fingerprint` that does NOT match `compute_config_fingerprint(config)` (e.g. a previous run with `train_months=24` left behind, current run has `train_months=12`)
- **WHEN** `WalkForwardEngine.run(config, resume_mode=ResumeMode.AUTO)` is called
- **THEN** a WARNING is logged identifying the fingerprint mismatch
- **AND** `_run_single_fold` IS called for fold_index 0, overwriting the prior manifest

#### Scenario: FORCE_RERUN ignores all manifests
- **GIVEN** `output_dir/fold_00_manifest.json` exists with a matching fingerprint
- **WHEN** `WalkForwardEngine.run(config, resume_mode=ResumeMode.FORCE_RERUN)` is called
- **THEN** `_run_single_fold` IS called for fold_index 0
- **AND** the prior manifest is overwritten on success

#### Scenario: RESUME_FROM_FOLD(2) runs fold 2 and beyond
- **GIVEN** matching manifests exist for folds 0, 1, 2, 3
- **WHEN** `WalkForwardEngine.run(config, resume_mode=ResumeMode.RESUME_FROM_FOLD(2))` is called
- **THEN** folds 0 and 1 are resumed from manifest (no `_run_single_fold` call)
- **AND** folds 2 and 3 (and beyond) are re-run

### Requirement: run_walk_forward.py CLI SHALL surface --resume-from-fold and --no-resume flags

`scripts/run_walk_forward.py` SHALL accept two additional CLI flags while preserving the legacy positional invocation `python scripts/run_walk_forward.py [config.yaml]`:

- `--resume-from-fold N` — int, 0-based fold index; passed through as `ResumeMode.RESUME_FROM_FOLD(N)`.
- `--no-resume` — bool flag; passed through as `ResumeMode.FORCE_RERUN`.

The two flags SHALL be mutually exclusive. Passing both SHALL raise a clear error before any qlib initialisation. The default (neither flag) SHALL be `ResumeMode.AUTO`.

#### Scenario: --resume-from-fold 3 passes RESUME_FROM_FOLD(3) to the engine
- **WHEN** `python scripts/run_walk_forward.py config_walk.yaml --resume-from-fold 3` is run
- **THEN** `WalkForwardEngine.run` is invoked with `resume_mode=ResumeMode.RESUME_FROM_FOLD(3)`

#### Scenario: --no-resume passes FORCE_RERUN
- **WHEN** `python scripts/run_walk_forward.py config_walk.yaml --no-resume` is run
- **THEN** `WalkForwardEngine.run` is invoked with `resume_mode=ResumeMode.FORCE_RERUN`

#### Scenario: both flags together error before qlib init
- **WHEN** `python scripts/run_walk_forward.py config_walk.yaml --no-resume --resume-from-fold 3` is run
- **THEN** the script raises a clear `ValueError` (or argparse error) naming the two mutually-exclusive flags
- **AND** `init_qlib_canonical` is NOT called
