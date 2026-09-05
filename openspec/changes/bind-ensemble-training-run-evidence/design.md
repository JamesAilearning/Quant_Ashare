## Context

The pipeline writes `<run>/artifacts/model.pkl`, `<run>/config.yaml`, and a trainer sidecar carrying the config's SHA-256. Bootstrap already reads that exact layout and checks the digest. Serving checks the sidecar hash but ignores the config binding; rotation and ensemble gates reuse that loader and inherit the omission.

## Goals / Non-Goals

**Goals:** Bind declared member windows to the exact persisted training configuration before deserializing the affected member; reuse one layout reader and digest validator across bootstrap and serving; preserve classified failures and current producer artifacts.

**Non-Goals:** Certified-family comparison, universe/handler/hyperparameter expectations, clean/mainline source checks, valid-window gate policy, quarterly recovery, group-wide preflight, pickle trust/sandboxing, metric or blend changes, production artifact edits.

## Decisions

1. Move only the layout reader and digest validator into `src/data/model_training_provenance.py`, with a dedicated provenance exception. This runtime-adjacent artifact access introduces no selection semantics. Its package import chain is stdlib-only, unlike the aggregate `src.contracts` package; keep YAML loading local to the filesystem reader so the digest-only bootstrap library retains its lightweight import path. Preserve the bootstrap reader's optional return contract through its adapter and translate validation errors back to `CutoverRefusal`. Do not move unrelated policy or source-ancestry logic.
2. Add factual train-boundary validation in the shared contract. Require a mapping with real, canonical YYYY-MM-DD string values for `train_start` and `train_end`; both must equal the manifest member's fit dates. Do not infer missing dates from the manifest or compare only duration. Hash and parse the same config byte buffer.
3. Invoke the contract from `_check_member_sidecar` using its already parsed, manifest-digest-verified sidecar. Preserve the loader's signature, returned objects, per-member ordering, framework checks, and same-buffer pickle hash/deserialization. A bad member's own pickle is never deserialized; this is not a claim that earlier valid members have never loaded.
4. Upgrade real-load synthetic fixtures to one producer-style run directory per member. The new checks are mandatory: fixtures and legacy artifacts without bound evidence must migrate rather than get an opt-out. Existing bootstrap error types and reader refusal behavior remain regression-protected.

## Risks / Trade-offs

- Flat/copy-only model deployments become unbindable → retain the complete producer run, not fabricated metadata; no automatic production rewrite.
- Matching config facts do not prove the config was an approved family → name that remaining policy gap explicitly and build it on this foundation later.
- Shared extraction can drift → preserve digest logic/messages and bootstrap adapter contracts, run bootstrap regression tests, and inspect whole-file pre/post functional-line differences as well as the focused diff. The reader intentionally expands its refusal boundary to filesystem stat errors and YAML constructor `ValueError` (including invalid timestamps), so malformed evidence produces a classified refusal instead of a traceback; this is not a zero-behavior-change extraction.
- Re-reading an external config later cannot authorize different inference facts → the checked bytes and digest are from one read; this change does not introduce a second mutable expected policy source.

## Migration Plan

The existing pipeline already produces the required evidence. No schema or producer migration is needed for current bound run artifacts. Synthetic fixtures must match that layout. Publish only code and documentation; do not modify live models, config files, or manifests. Rollback removes the stricter load check and restores the known provenance gap.

## Open Questions

None for factual evidence binding. Registered-family expectations and source/gate policy remain distinct follow-ups.
