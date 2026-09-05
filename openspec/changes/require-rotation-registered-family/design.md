## Context

Bootstrap already compares digest-bound producer configuration against committed
presets and base config. Quarterly rotation currently checks only its factual
date/digest chain and clean registered source. The m4 preset and governance tests
explicitly preserve m3's non-window family, with only six date fields changing.

## Goals / Non-Goals

**Goals:** enforce that existing family boundary at rotation, with classified
refusal before unpickling or backup/install, synthetic regression coverage and
preserved registered values and public contracts. The boolean-alias follow-up
explicitly tightens type fidelity in the shared bootstrap/rotation comparison.

**Non-Goals:** recertification, new performance gates, changed gate/producer APIs,
fixed m4 dates, provider migration, live data repair, retrospective qualification
of surviving members, or daily-serving Git checks.

## Decisions

1. Read `config.yaml` and the three `BOOTSTRAP_PRESET_PATHS` by `git show` at
   rotation's existing pinned certification revision. Never use the worktree,
   training source commit, mutable environment or other live members as authority.
   Require mapping documents, the established base inheritance and mandatory
   preset family fields; require all three registered non-window surfaces to
   agree. This is consistency of committed registration, not majority voting
   among trained members. Compare all declared non-window fields, including
   future additions, not just the required minimum field names.
2. Exclude only `train_start/end`, `valid_start/end`, `test_start/end` and
   structural `extends` from preset comparison. Existing factual/window checks
   remain in force. Reuse `check_member_training_config` for preset values and
   `SAME_FAMILY_KEYS` / `SAME_FAMILY_DEFAULTS` from the committed base contract.
3. Move bootstrap's two provider helpers and preset-path constant unchanged into
   its shared library, retaining imported compatibility names in the executor.
   Both executors then use committed-template defaults only and canonical qlib
   path normalization. No environment expansion of expected values, no copied
   second algorithm. Verify the move with whole-file filtered content and AST
   proof; do not describe the whole PR as behavior-neutral.
4. Read the incoming producer config for family adjudication and bind that read's
   digest to the SAME already manifest-bound sidecar buffer before inspecting its
   parsed values. A second read after factual validation must earn its own digest
   proof; no public helper return-type change is needed. Normalize parsed copies,
   never rewrite producer evidence.
5. Missing/inconsistent registration, unknown required values, Git/read/parse
   failures or config mismatch refuse through `RotationRefusal`. All new Git
   reads have a 30-second bound. Existing cleanup releases the advisory lock and
   removes staging; its persistent lockfile is not deleted.
6. Compare scalar booleans by identity in the shared family check. Python numeric
   equality admits `1 == True` and `0 == False`, so value-only drift tests cannot
   protect the declared type boundary. Reject boolean/numeric aliases in both
   preset and base comparisons while preserving intentional int/float equality.
   Use that same rule for committed preset-to-preset agreement, so one preset's
   integer guard cannot be hidden by Python dictionary equality with two booleans.
   This is the explicit follow-up behavior change; the provider-helper extraction
   remains unchanged and its proof is distinguished from this comparison fix.

## Risks / Trade-offs

- Four small Git reads per rotation -> no training or bundle reads; bounded calls.
- A future mainline family policy change changes the comparison authority -> this
  matches bootstrap and existing governance; pin once for the entire execution.
- Provider path equivalence does not identify a data snapshot -> make no snapshot
  claim; retain current normalizer semantics rather than inventing a new identity.
- Previously admitted non-family or incomplete run configs now refuse -> keep the
  incumbent; retraining/alternate family requires the registered workflow, not
  rewriting evidence or weakening gates.

## Migration Plan

No artifact/schema migration. Test with scratch repositories and synthetic models,
publish one reviewed PR, merge only after current-head Codex review and all CI pass.
Production training, model rotation and deployment remain operator actions.

## Open Questions

None within the existing same-family boundary. CPU/provider migration or a new
quarterly window policy would require a separate user decision.
