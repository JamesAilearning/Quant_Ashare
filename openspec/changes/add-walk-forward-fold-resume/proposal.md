# Add Walk-Forward Fold-Level Checkpoint + Resume

## Why

A walk-forward run that crashes in fold 6 of 8 (transient qlib OOM,
disk full, OS reboot, etc.) currently re-trains folds 0–5 from
scratch on the next attempt. With 8-fold ensembles of LGB models on
Alpha158 universes, that's hours of wasted compute on already-done
work. The user explicitly flagged "system is slowly training" in the
audit — fold-level resume is the highest-leverage fix because it
turns "all-or-nothing" into "incremental progress".

The artifacts needed for resume **already exist on disk**:

- `output/wf/model_fold{i}.pkl` — trained model (already written by
  `_run_single_fold`).
- `output/wf/fold_{i:02d}_report.json` — per-fold metrics
  (already written).
- `output/wf/fold_{i:02d}_predictions.pkl` — prediction artifact
  (already written).
- `output/wf/fold_{i:02d}_positions.json` — backtest positions
  (already written when non-empty).

The missing pieces:

1. **A small manifest per fold** that records "this artifact set
   corresponds to fold N of config-fingerprint F" so we can validate
   that resumed artifacts belong to the *current* config (not a
   stale leftover from a previous YAML).
2. **A scan + skip step** at the top of
   `WalkForwardEngine.run` that loads matching manifests and reuses
   them instead of re-running the fold.
3. **A CLI flag** on `scripts/run_walk_forward.py` so the operator
   can override the default auto-resume (e.g. `--no-resume` to force
   a clean rerun after changing something subtle, or
   `--resume-from-fold N` to truncate beyond a specific fold).

## What Changes

- **ADD `src/core/walk_forward/_resume.py`** — `FoldManifest` dataclass
  with `from_fold` / `save` / `load` / `discover` helpers. Includes
  `compute_config_fingerprint(config)` which hashes
  `dataclasses.asdict(config)` excluding the `output_dir` field (so
  renaming the output directory doesn't invalidate the run).

- **ADD `ResumeMode` enum** in `src/core/walk_forward/_resume.py`:
  - `AUTO` (default) — load any manifest whose fingerprint + window
    match the current config + window; re-run the rest.
  - `FORCE_RERUN` — ignore manifests; re-run every fold. Output
    artifacts overwritten in place (same as today's behaviour).
  - `RESUME_FROM_FOLD(n)` — for folds `< n`, load manifest if
    available, else re-run; for folds `>= n`, always re-run.

- **MODIFY `src/core/walk_forward/engine.py::WalkForwardEngine.run`** —
  add a keyword-only `resume_mode: ResumeMode = ResumeMode.AUTO`
  parameter. Before the fold loop, call
  `FoldManifest.discover(output_dir)` to get the set of resumable
  manifests; for each window, decide skip-via-manifest vs run-fresh
  based on `resume_mode` + fingerprint + window match. Existing
  callers that don't pass `resume_mode` get auto-resume — but since
  fresh output dirs have no manifests, the behaviour is identical to
  the legacy path. Adds a manifest write at the end of
  `_run_single_fold` (after the report write, before return).

- **MODIFY `scripts/run_walk_forward.py`** — add CLI flags:
  - `--resume-from-fold N` (default unset → AUTO)
  - `--no-resume` (default false → AUTO)
  - The two are mutually exclusive; passing both raises a clear
    error.

- **ADD `tests/logic/test_walk_forward_resume.py`** — unit + small
  integration tests for the manifest roundtrip + fingerprint
  computation + skip/re-run decision matrix. Heavy integration
  (running a real fold then a real resume) goes behind `@skip_unless_e2e`
  per the user's `RUN_E2E=1` guard rule.

- **ADD new capability `v2-walk-forward-resume`** — three
  requirements covering the manifest contract, the resume decision
  matrix, and the CLI flag surface.

## Non-Goals

- **No change to existing fold artifact filenames.** `model_fold{i}.pkl`,
  `fold_{i:02d}_report.json`, etc. all remain at their current
  paths. Existing tooling reads them unchanged.
- **No automatic cross-config migration.** If the operator changes
  `train_months` or `topk` between runs, the manifests' fingerprint
  doesn't match and folds re-run. Resuming "partial config matches"
  is out of scope — the right tool there is "force_rerun".
- **No GUI surface.** The operator UI's jobs page can show whether a
  run was resumed (via the manifest count in the output dir) but the
  flag is not surfaced in the UI in this change.
- **No change to ensemble math.** Resumed folds contribute to
  `prior_model_paths` exactly as they did the first time around —
  the model.pkl is the same file.
- **No change to attribution.** Resumed folds keep their original
  attribution result (read from the report JSON); no re-run.
- **No incremental backfill.** "Run two more folds on top of yesterday's
  6-fold run" is a separate feature (would require extending
  `overall_end`); resume only covers "finish the run that was
  already going".
