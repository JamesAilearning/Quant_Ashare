## Context

The producer stamps `source_git_commit` and `source_git_dirty` into the sidecar from its run-start capture. Bootstrap checks these fields and mainline ancestry. Rotation already pins the certification revision once and reads the incoming manifest-bound sidecar for validation-window binding, but does not adjudicate training source.

## Goals / Non-Goals

**Goals:** Reject an incoming maintenance member unless its existing bound source evidence records an explicitly clean checkout and a commit equal to or ancestral to the same pinned mainline revision; preserve classified zero-install refusals.

**Non-Goals:** New daily-serving Git access, retrospective checking of surviving incumbent members, bootstrap changes, model-family/hyperparameter registration, independent performance gates, schema/threshold changes, retraining or live deployment.

## Decisions

1. Reuse `bootstrap_cutover_lib.check_member_source_provenance` for the exact existing field policy rather than create a second interpretation. A small rotation adapter translates `CutoverRefusal` into `RotationRefusal`; the pure validator and bootstrap interface stay unchanged. Do not extract/move helpers or import the bootstrap executor into maintenance.
2. Check ancestry with `git merge-base --is-ancestor <training commit> <pinned revision>` inside the rotation executor, using its already-resolved repository and revision. Both equality and ancestry pass; unknown, non-ancestor, or Git failure refuses. Bound the read-only command to 30 seconds and classify OS/timeout errors. No fetch, ref mutation or fallback revision inside this guard.
3. Call the adapter on the same incoming sidecar buffer already checked against the staged manifest, after config/date binding and before strict model loading, backup or installation. An old PASS gate cannot waive this eligibility; existing staging cleanup, lock release and failure audit output remain. The lockfile intentionally persists to avoid inode races; tests reacquire the lock instead of requiring file deletion.
4. Apply this source-evidence eligibility to maintenance explicitly in this OpenSpec change. Do not describe the current baseline as already spelling out this exact quarterly algorithm, and do not rerun strategy certification or reinterpret a mainline commit as proof of registered model parameters.

## Risks / Trade-offs

- Legacy artifacts lack source fields → refuse, preserve incumbent and existing gate artifacts; obtain genuine eligible training evidence rather than edit sidecars.
- A moving mainline could change the answer mid-check → use the already pinned revision, never resolve the branch again for source checking.
- Git failures or unknown history could be mistaken for eligibility → every nonzero result, timeout or OS failure is a classified refusal before installation.
- New checks could mask unrelated rotation regressions → fixture sidecars use a real scratch-repository source commit; negative tests rebind the full digest chain and assert no loader/backup/install, while existing success tests still install.

## Migration Plan

Deploy reviewed code only. Current producer fields require no schema migration. Existing members remain untouched; newly rotated members must carry genuine clean/mainline source evidence. Rollback removes this source guard and restores the known maintenance gap; do not rewrite production artifacts as part of this change.

## Open Questions

None for this source-only maintenance eligibility. Extending ancestry checks to every daily serving load or registering model-family policy requires separate scope and decisions.
