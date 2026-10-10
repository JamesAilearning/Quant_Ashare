## Context

Real original SHA `2adfad52476cc645270e8358b79ade01a3b3b4734ee12dbf65f1721567afdfbb` contains the exact R and S incident pair plus all eight unrelated SH688766 keys. The failed preparation consumed its permanent claim without copying or fetching; its evidence stays unchanged. Canonical `_validate_history` currently accepts R-only reference and R-only/S-only retained incident states. `publish_suspension_candidate` uses retained as reference without prior evidence, so the real original pair is refused even with the already approved exact S candidate.

## Goals / Non-Goals

**Goals:** Accept the exact already-observed original pair as immutable audit evidence under the existing single-stock opt-in while preserving every candidate, unrelated-retention and consumer refusal boundary.

**Non-Goals:** New policy IDs, broader keys/dates/timings, schema/signature changes, row filtering/manufacture, combined/legacy behavior changes, automatically clearing an established conflict, model/trading changes, production deployment or replay of any consumed operation.

## Decisions

- Modify only the existing single-conflict history branch: reference incident keys are exactly R or R+S; retained keys exactly R, S or R+S; candidate still exactly S. Use the incident descriptor's existing immutable sets and preserve exact equality, not arbitrary subset admission. The unchanged generic guard retains all other original and retained keys, including SH688766's eight records.
- Preserve complete original R+S Parquet bytes through existing `_preserve` and the same seven-field reference/retained/candidate SHA evidence. Do not filter the original file into an R-only reference, invent rows, change journal semantics, or add an external predicate adapter.
- Keep clean first-acquisition and established-conflict resolution rules unchanged. Prior S-only repeat stays quarantined; R-only, R+S, missing, retimed or extra candidate incident payloads remain refused after conflict. Legacy/combined branches are untouched.
- Synthetic regression inputs independently spell out the real pair and eight unrelated records, not values derived from implementation expected-key constants. Verify first publication, repeated evidence, provider validation, crash-journal recovery and tamper/refusal without local bundles or API credentials.

## Risks / Trade-offs

- Expanding an equality into a generic waiver -> keep finite exact role sets and test extra/missing/retimed payloads and loss of each unrelated key.
- Pair support silently clears or broadens an existing conflict -> unchanged strict S-only candidate, explicit opt-in, prior-evidence revalidation and cross-policy negative twins.
- Repeated or recovered artifacts lose original S/R bytes -> verify whole immutable evidence hashes and round-trip through existing journal/provider readers, not just helper return values.
- Passing tests restate the implementation's assumptions -> independently literal production-shaped fixtures plus before-fix failure evidence and independent local review.

## Migration Plan

Run new tests against unchanged code first, implement only the approved role-set correction, then targeted suspension/transaction/provider tests, mandatory logic/governance, source import, lint/types and strict OpenSpec validation serially under existing resource bounds. Review locally to convergence before push; request Codex review after each push and merge only the latest clean reviewed head with CI passing under the user's existing merge authorization. Separately bind new external recovery tools to the actual merged head/source hashes, qualify a fresh one-shot preparation, then execute the one newly approved recovery attempt. No code-only report certifies production.

## Local Verification Evidence

The independently literal `test_first_conflict_from_original_pair_preserves_both_reference_rows` failed before the fix with `AggregateResponseError: suspension quarantine reference has unapproved conflict payload`; it passes after the fix. Final serial local validation passed 5,696 logic/governance tests (33 skipped; 2,087 subtests) and 1,948 data-pipeline tests (1 skipped; 94 subtests), plus the source import, whole-tree Ruff, strict mypy (244 files) and strict OpenSpec validation. Independent local review found no P0-P2 issues. Existing warnings were retained in the actual gate logs. These synthetic/local results do not certify preparation, vendor acquisition, production data, deployment or live recommendations; the separate genuine acceptance and rollback gates remain mandatory.

## Open Questions

None for this code change: operator approved the exact pair correction and one distinct execution after review/tests. Genuine data acceptance, matched rollback, reversible production switching and scheduling remain separate prerequisites, not waived by this approval.
