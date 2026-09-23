## Context

Current quarantine evidence has seven fields including a policy ID and three byte hashes. Acquisition, durable publication/recovery, provider gates, serving, exports and read-only notices assume one policy. The new observed S payload is not an approved legacy omission. Actual production remains untouched and the failed isolated generation is retained.

## Goals / Non-Goals

**Goals:** separately authorize the exact 688005 conflict, preserve both sources, keep all other retention guards, and exclude only the evidence-selected security from daily picks.

**Non-Goals:** generic waiver configuration, combined incidents, automatic truth adjudication, history fabrication, changed scores/holdings/universe, retraining, UI opt-in, replay labeled as live fetch, or deployment in this PR.

## Decisions

- Define immutable incident descriptors in the existing contract module. Keep old constants as legacy aliases; policy-aware producers/consumers resolve one descriptor. This is a closed set of two approved incidents, not user-configurable rules.
- Preserve the seven-field evidence shape, manifest versions and journal shape. The new ID identifies the exact R reference key and exact S candidate key. Unknown IDs fail in older readers intentionally; do not downgrade an artifact to an old policy.
- For the new incident, require an original reference with exactly the R key on that stock/date, a candidate with exactly the observed S key, and retained incident keys equal to either reference or candidate. Generic retention remains strict everywhere else, including all eight former 688766 keys. No combined policy is accepted.
- With no prior quarantine, an unchanged R-only complete acquisition remains clean. Once the conflict has been established, S-only repeat responses remain quarantined; any changed, absent, additional or restored incident payload refuses pending separate review. This avoids pretending that a returned R settles contradictory history.
- Pass policy explicitly through publication and bind it to journal, prior evidence and recovery selection. No cross-policy recovery or new publication may mutate existing evidence.
- Derive instrument and disclosure from parsed evidence, checking outer context ID against inner evidence ID. Exclude before Top-K, preserve original scores/audit rows, disclose incomplete data in JSON/CSV/CLI/UI, keep historical evaluation refusal.

## Risks / Trade-offs

- Reusing schema with a new policy could mislabel the security → validate ID equality at every trust boundary; legacy artifact regression tests remain unchanged.
- R may later reappear → refuse rather than silently clear the conflict; automatic reconciliation is explicitly not implemented.
- API may omit or change more rows → reject before raw publication. No stock-count-based exemptions.
- Failed production acceptance has expensive completed downloads → leave the failed generation unchanged during code work; plan evidence-bound targeted continuation separately after a reviewed merge.

## Migration Plan

Serial synthetic tests, required logic/governance and data suites, imports, lint/types, local review, fresh Codex review and CI precede merge. New policy is opt-in and does not alter default callers. Live acceptance, backups and reversible switch remain separate. Never load new-ID data with old code or overwrite retained failure reports to claim recovery.

## Open Questions

No scope decision pending: the operator explicitly accepted temporary exclusion of SH688005. Resolving its conflicting historical truth is a future separately approved action.
