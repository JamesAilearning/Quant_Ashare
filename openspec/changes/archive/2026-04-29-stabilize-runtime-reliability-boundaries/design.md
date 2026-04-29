## Context

The canonical runtime boundary was hardened in the previous change, but a few
adjacent surfaces did not move with it. The CLI loader still constructs the old
runtime config shape; documentation still describes a pre-runtime skeleton; and
some error paths still degrade structured outputs into ambiguous fallback
payloads.

This change is intentionally narrow: it makes existing behavior explicit and
failure modes typed. It does not decide the pending risk-constraint migration.

## Decisions

1. **Use one adjustment-mode source in walk-forward CLI.**
   - Decision: build `WalkForwardConfig` first, then derive
     `QlibRuntimeConfig.data_adjust_mode` from `wf_config.adjust_mode`.
   - Rationale: the walk-forward request and qlib provider declaration must use
     the same caller-controlled field.

2. **Return-series serialization fails loud on unknown shapes.**
   - Decision: `_series_to_dict()` raises `BacktestRunnerError` when the input
     cannot be iterated or a key/value cannot be converted.
   - Rationale: `{"raw": str(series)}` is not a structured return series and
     causes later consumers to fail far from the boundary.

3. **Temporal loader IO errors keep real path context.**
   - Decision: the shared loader reports `artifact_file`, the in-scope path
     object, in OSError messages.
   - Rationale: loader errors should identify the boundary that failed without
     masking the original OSError.

4. **Runtime dependencies belong in the base project dependencies.**
   - Decision: declare PyYAML, matplotlib, optuna, and python-dateutil in
     `[project].dependencies`.
   - Rationale: shipped entry points and runtime modules use these packages.
     They should work after a normal project install, not only in a manually
     enriched development environment.

5. **Governance cleanup is limited to facts already on main.**
   - Decision: update stale docs, reconcile archived tasks, and archive the
     merged `harden-canonical-runtime-boundary` change by applying its spec
     deltas to baseline.
   - Rationale: leaving completed work active or archived tasks unchecked makes
     future OpenSpec decisions noisy.

## Risks / Trade-offs

- [Risk] Tests run in this worktree may observe unrelated local edits in
  `src/core/model_trainer.py` and `src/core/walk_forward.py`.
  - Mitigation: do not touch or stage those files; report the dirty worktree in
    the checkpoint.
- [Risk] `openspec` CLI is unavailable in this environment.
  - Mitigation: maintain artifacts manually in the existing repository format
    and document the validation limitation.

## Rollback

Revert this change. It does not migrate persisted data or alter official
runtime semantics.
