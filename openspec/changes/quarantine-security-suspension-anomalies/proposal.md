## Why

Repeated vendor revisions of individual securities' suspension history have blocked production recovery because existing exceptions encode three exact incidents. The operator explicitly approved evidence-backed isolation of anomalous securities without repeated per-security approval, while prioritizing production recovery and retaining system-wide safety gates.

## What Changes

- Add explicit `suspend-security-anomalies-v1` authorization for a bounded minority of securities with demonstrably lost suspension business keys in an otherwise valid, complete aggregate acquisition.
- Preserve vendor candidate rows unchanged and immutable original/retained evidence; derive the quarantine list from actual four-field key loss, never an operator-supplied bypass list.
- Add a strictly parsed ten-field typed evidence variant, including immutable prior-file witnesses and SHA-bound published predecessor lineage so later-added history cannot be forgotten or foreign archives admitted. Preserve the three existing policies and their seven-field evidence semantics unchanged.
- Propagate the evidence-derived security list through provider/PIT checks, recommendation exclusion before Top-K, output checks and existing read-only disclosure. Historical evaluation still refuses quarantined data.
- Document autonomous incident handling and efficient recovery from verified downloaded inputs, without treating an interrupted operation as successful.

## Capabilities

### New Capabilities

- `security-suspension-quarantine`: Evidence-backed per-security suspension-history isolation under a stable explicit policy.

### Modified Capabilities

None. Existing exact policies remain unchanged; the new capability is an opt-in extension.

## Impact

Contracts, suspension candidate publication/verification, crash-closed publication journal, inference/output and their existing CLI/read-only consumers. No dependency, model, training, canonical metric, price adjustment, holdings or scheduler changes. Actual production restoration remains a separate genuinely executed acceptance and reversible deployment workflow.
