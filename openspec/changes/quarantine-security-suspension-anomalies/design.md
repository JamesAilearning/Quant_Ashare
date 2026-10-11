## Context

The genuine 2026-10-11 full acquisition failed before publishing suspend_d because eight SH688766 original keys disappeared in addition to the selected SH688005 conflict. A separately successful, byte-bound 173-response observation found nine missing original keys across those two securities; it is diagnostic evidence, not full or production acceptance. The operator now authorizes autonomous isolation of attributable individual-security anomalies without repeated incident-specific approval.

## Goals / Non-Goals

**Goals:** Unblock valid suspension acquisition under a stable, explicit evidence-based policy; quarantine whole affected securities before recommendation Top-K; retain actual incomplete provenance and system-wide refusal boundaries.

**Non-Goals:** Ignore arbitrary exceptions, generalize other endpoint schemas, filter/manufacture vendor rows, union old rows, change old policy meanings, authorize historical evaluation, modify training/models/holdings, deploy without real acceptance, or rerun consumed operations.

## Decisions

1. Add `suspend-security-anomalies-v1`. A frozen `SecuritySuspensionQuarantine(SuspensionQuarantine)` adds required `quarantined_ts_codes` and `history_sha256`; the base parser explicitly dispatches this policy to exactly nine JSON fields, while old IDs retain exactly seven. The subtype preserves existing typed hole/journal/provider interfaces. Shared evidence helpers resolve security identities from verified evidence rather than a fixed incident ID.
2. Only actual lost four-field keys from the full original reference, immediately retained file or archived previous retained witnesses identify affected securities. Newly added dates, historical backfills or R+S additions alone are not anomalies: the aggregate business key permits multiple types on one day. Missing dates and the sorted security list must exactly match recomputation from SHA-bound Parquet files at each existing verification gate. `history_sha256` is a sorted unique tuple of at most 32 immutable actual earlier-file hashes, initially empty; it never names invented/merged data. Carry a prior witness only while it proves missing keys not already covered by original/current-retained evidence. Bound cumulative witness bytes and decoded data to the existing candidate budget. Preserve the previous retained snapshot when needed, preventing the sequence new-X -> lost-X -> still-lost-X from forgetting X.
3. The queried range must cover all reference, retained and witness history. Candidate/reference/retained/witness files must have valid stock identities and existing schema/budget/response validation. Quarantine is bounded to at most ten securities and strictly less than half the securities in the final persisted evidence baseline (original/current-retained/necessary witnesses). This permits genuinely acquired newer securities without inventing an authorization list; candidate-only new codes cannot imply historical loss. Pruned witnesses cannot inflate the minority denominator. Larger/system-wide/unclear failures are refused, not recast as isolated stock defects. This is a safety ceiling, not permission to skip other validation.
4. Vendor candidate rows are published unchanged through the existing crash-closed journal; original and retained bytes remain immutable audit evidence. Existing quarantines require matching prior authorization. A later validated full acquisition restores a security only when it retains every original, immediately retained and still-required witness key for that security; the current evidence is recomputed, not a manually edited allowlist. A clean acquisition follows the existing clean publication lifecycle, with prior evidence still archived.
5. Provider/PIT and serving retain hole provenance. All dynamic quarantined securities are excluded before Top-K, output writers/CLI/read-only disclosure verify the same evidence, and existing holdings are never represented as sold. Historical evaluation and all default/mismatched/broad-hole paths remain refused. Shared Pipeline/WalkForward field names are unchanged.
6. Other attributable anomalies will be handled under the same operational principle with their own validated endpoint contracts; this PR only implements the current suspension-history blocker. A generic catch-all or automatic waiver of corrupt/global data would violate recovery safety.

## Risks / Trade-offs

- Vendor-wide truncation masquerading as stock loss -> unchanged collector completeness/schema/budget checks, complete query bounds, minority/count ceiling and ordinary-hole refusal.
- Forged or stale exclusion metadata -> exact typed parse, three raw hashes, actual key/code/date recomputation, pending-journal refusal, matching policy at every gate and output audits.
- Incident-specific behavior accidentally broadened -> retain old branches/JSON and run all old regression suites; only the new opt-in admits new attributable losses.
- Repeated expensive recovery work -> reuse previously downloaded endpoints only after fresh inventory and independent validation; never turn a failed full report into success or inject a fake fetch stage. Actual recovery orchestration/acceptance is separate from this code-only PR.

## Migration Plan

Capture a synthetic before-fix failure; implement the contract and all consumers together. Run targeted, required logic/governance and data-pipeline suites, touched-module imports, lint/types and strict OpenSpec validation serially under CPU1/libs1/tree8GiB/free6GiB limits. Converge local review before push, request Codex review after each push, and merge only the latest clean reviewed head with CI passing. Bind any actual recovery runner to the merged source and fresh inputs. Preserve failed generations and permanent claims; full acceptance, companion rollback backups, reversible cutover, first real fixed-path production update and scheduler-last acceptance remain mandatory.

## Open Questions

None for this bounded code change. Production data, deployment and live acceptance are not certified by synthetic tests or the diagnostic observation.
