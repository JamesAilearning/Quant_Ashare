## Why

The member gate scores operator-supplied fit/valid dates without proving they describe the actual training run. Rotation validates only broad date span/gap/freshness rules, so a correctly hashed PASS gate measured on another plausible valid period can authorize installation.

## What Changes

- Bind member-gate fit and validation dates to the exact producer config through the existing pickle/sidecar/config digest chain, using a shared factual validator.
- Reject unbound member measurements before dataset construction or model deserialization. Preserve a FAIL artifact with an unmeasured/null IC and an explicit reason; no new gate or threshold.
- Revalidate the incoming member's gate window against bound run evidence during rotation, before model loading, backup or installation, including older PASS artifacts.
- Preserve bootstrap's existing equivalent pre-registered-window binding, serving's public interface, gate schema and canonical metrics.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `v2-daily-stock-recommendation`: require exact producer validation-window binding in member gates and maintenance rotation.

## Impact

`src/data/model_training_provenance.py`, `scripts/retrain_gate.py`, `scripts/retrain_gate_lib.py`, `scripts/rotate_ensemble_member.py`, synthetic runner/rotation/shared-validator tests and the production runbook. The pure integrity gate also classifies malformed non-string model types as FAIL instead of raising before the evidence refusal can be recorded. Existing producer fields `train_start`, `train_end`, `valid_start`, `valid_end` and `run_config_sha256` are reused; no producer schema migration or dependency additions.

This does not change the valid-window IC sanity gate into a truly unseen test-period performance gate. Registered-family/source certification, failed-quarter recovery policy, ensemble trailing-quarter overlap rules and production deployment remain outside this change.
