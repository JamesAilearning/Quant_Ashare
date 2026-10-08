## Context

The retained reference contains the eight legacy 688766 keys and the null-timing R key for 688005/20260116. The independently audited live response omits exactly the eight former keys and contains both that R key and the 09:30-09:30 S key on the latter date. Old policy IDs are alternatives and cannot be composed implicitly.

## Goals / Non-Goals

**Goals:** one explicitly approved combined policy, lossless history elsewhere, byte-bound original/current evidence and exclusion of exactly two securities from daily candidates.

**Non-Goals:** arbitrary combinations, configurable exemptions, automatic reconciliation/clearing, invented rows, changed model/scores/holdings/universe/cadence, historical certification, UI opt-in, replay as live evidence, or deployment in this code PR.

## Decisions

- Add closed ID `suspend-688005-688766-observed-20261008`. Use tuple-valued security identities in the immutable descriptor. Existing IDs and seven-field evidence stay compatible; older code intentionally rejects the new ID.
- Combined reference affected keys MUST equal the original nine keys (eight legacy plus R). Candidate affected keys MUST equal the observed R+S pair, with all eight legacy keys absent. Retained affected keys MUST be either the original nine or the observed pair. Require every key outside the exact approved affected keys to survive in candidate and retained lineage. Existing conflict/legacy rules do not change.
- Combined evidence MUST carry exactly the eight legacy missing dates, while query coverage MUST include all nine reference-key dates through 20260116. Do not use the missing-date domain as the query-domain shortcut.
- Under this ID no payload restoration, absence or new affected-date key automatically clears quarantine. Reject changed payload pending another explicit decision. A fresh ordinary clean acquisition without this ID remains governed by existing normal rules.
- Preserve the one-hole, journal, hash and schema-version rules; a combined policy is still one byte-bound suspend_d incident, not two holes or a generic mixed-hole waiver. Existing cross-policy recovery rejects, without modifying earlier evidence.
- Keep old JSON `instrument` and CSV `quarantined_instrument` for one-security IDs. New JSON uses exact ordered `instruments: [SH688005, SH688766]`; new CSV `quarantined_instruments` is `SH688005;SH688766`. Refuse singular/plural substitutions, reordered/missing/extra securities, mismatched outer/inner ID and false incomplete flags. Contract-derived construction/validation is shared by export and read-only UI.
- Daily serving excludes both identities before Top-K, preserves original scores/audit rows and admits zero scored exclusions. Disclosure remains active even if neither security belongs to the scored universe. Pipeline and WalkForward continue refusing all quarantine; no metric schema changes.

## Risks / Trade-offs

- Vendor changes again → exact affected-key comparison refuses, no automatic retry/expansion.
- Plural metadata accidentally reuses old singular validation → shared shape validation plus malformed-context and both-security leakage tests.
- Reuse of failed acquisition mistaken for acceptance → code tests are synthetic; live acquisition, full provenance/PIT/provider checks, freshness, rollback, cutover and first production update remain separate gates.

## Migration Plan

Write failing synthetic tests; implement; independently review final diff; serial targeted/full suites, imports, lint/types and strict OpenSpec validation; fresh Codex review and all CI on latest head before authorized merge. Then review bounded live orchestration. Preserve all failed roots and production until real acceptance and reversible matched backup/cutover are complete. Scheduler stays off until last.

## Open Questions

None for code scope: the user explicitly approved this exact combined isolation and continuation. Historical truth reconciliation remains unimplemented.
