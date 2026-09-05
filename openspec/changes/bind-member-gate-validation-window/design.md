## Context

The producer persists four flat train/valid boundaries and a config digest in the model sidecar. Serving now binds the train boundaries, but the member IC runner still trusts its CLI dates and rotation accepts any valid window satisfying broad bounds. Bootstrap already binds both run config and measured windows to the same registered preset.

## Goals / Non-Goals

**Goals:** Bind the member gate to its actual training/validation configuration at measurement and consumption, including replayed older PASS gates; preserve auditable failure artifacts and zero-install rotation refusals.

**Non-Goals:** Changing IC thresholds, early-stopping behavior, independent OOS performance certification, registered-family/source validation, ensemble dry-run overlap, quarterly recovery, gate schema migration, producer changes or live deployment.

## Decisions

1. Add `check_member_gate_provenance` to the existing data-layer evidence module. It checks the sidecar's pickle digest against caller-bound pickle identity, then reuses the exact-layout config reader, config digest validator and train-window validator, and checks canonical ISO valid dates for exact agreement. No new persisted fields and no inference from operator declarations.
2. In the member runner, read pickle and sidecar once. The helper uses the same sidecar the integrity gate judges and the actual digest of the same pickle bytes that successful scoring deserializes. Perform binding before dataset creation or unpickling. On missing/corrupt/mismatched evidence, preserve the integrity result, emit the existing IC block shape with FAIL, null `ic_1d` and an explicit not-measured reason, and let the normal CLI write the failed artifact. Missing pickle remains a producer/tool error. Do not add a third gate or invent a numeric measurement.
3. In rotation, retain all existing gate and plan checks. Before strict loading/backup/install, read the incoming member's sidecar once for this validation and check its hash against the staged manifest. Pass that parsed buffer, manifest pickle identity and already verified gate dates into the same helper. The existing loader subsequently checks actual pickle bytes and all surviving members. A producer-only fix would leave older PASS artifacts exploitable, so both boundaries are required.
4. Keep bootstrap unchanged: it already requires measured valid windows to equal registered windows and the digest-bound run config to equal the same registered preset. Run its existing tests for compatibility. Keep `load_member_models` unchanged and keep the pure gate/rotation libraries free of runtime imports.
5. Classify non-string sidecar model types as integrity FAIL before membership lookups. Lists and objects otherwise raise `TypeError` before an unbound-evidence FAIL artifact can be recorded. Preserve all existing valid-model indexing conventions, gate verdicts and measured-field shapes; this is corruption handling, not a new model-family policy.

## Risks / Trade-offs

- Legacy or mismatched gates stop authorizing rotation → regenerate gates only using genuine bound run artifacts; no automatic production rewrite or date/hash editing.
- Invalid evidence could destroy failure visibility → synthetic CLI tests require persisted FAIL with null IC and zero scoring/deserialization calls.
- Re-reading evidence could accidentally bind different revisions → each check hashes/parses one byte buffer, rotation checks sidecar against the immutable staged digest, and config bytes must match that sidecar; later loader reads remain independently bound to those identities.
- A successful binding is not proof of approved policy or unseen performance → state the factual-only scope in the runbook and delivery summary.

## Migration Plan

Current pipeline artifacts already carry all required fields. Add valid boundaries to the incoming rotation test fixture and use actual-layout synthetic runner fixtures. Deploy only reviewed code/docs; do not modify live runs or gate artifacts. Rollback removes the additional guard and restores the known evidence gap.

## Open Questions

None for exact factual validation-window binding. An independent unseen-period performance gate would require a separate decision.
