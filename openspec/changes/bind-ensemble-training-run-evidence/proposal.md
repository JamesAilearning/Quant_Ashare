## Why

The ensemble loader checks declared window arithmetic and pickle/sidecar hashes but never binds those windows to the training run's persisted config. A manifest can therefore describe plausible quarterly dates unrelated to the actual run, or keep serving after its config is altered, although bootstrap already verifies the config digest.

## What Changes

- Share the existing strict producer-layout config reader and sidecar config-digest validator between bootstrap and serving, retaining bootstrap's compatibility adapters and failure types.
- Before deserializing each member, require a readable mapping config whose exact bytes match the manifest-bound sidecar's `run_config_sha256`; require its `train_start`/`train_end` to equal the member's declared `fit_start`/`fit_end` as valid ISO dates.
- Keep the loader interface and all four callers unchanged; migrate synthetic serving and rotation fixtures to the actual producer layout and add pre-deserialization/zero-write regressions.
- Reject unbound legacy or hand-copied artifacts explicitly. Do not manufacture missing provenance or alter production model files.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `v2-daily-stock-recommendation`: require factual training-run evidence when loading ensemble members.

## Impact

`src/data/model_training_provenance.py`, ensemble serving, bootstrap helper adapters, and focused serving/rotation/bootstrap tests. The pipeline already writes the required config digest and flat training-date fields; no producer or persisted schema changes are required. Canonical metrics, blend mathematics, member spacing, and retraining policy are unchanged.

This is foundation-only evidence binding. It does not close the separate registered-family/universe/feature/hyperparameter policy checks, source mainline ancestry, gate valid-window agreement, or failed-quarter recovery-policy findings.
