# Local verification record

Scope: one approved historical incident only. No production fetch, provider/model
publication, scheduled-task change, or historical performance certification was
performed for these code checks. Synthetic tests use temporary files and no
Tushare credentials. Heavy checks run serially with one compute thread and a
resource guard (8 GiB process-tree ceiling, 6 GiB minimum available memory).

## Observed RED before fixes

- Contract regressions: 96 failed / 22 passed before the evidence contract existed;
  the completed contract suite passed 123 cases.
- Initial fetch/update/historical-entry regressions: 18 failed / 1 passed;
  recommendation regressions: 36 failed / 3 passed.
- `test_quarantined_provider_refuses_before_historical_output_or_resume`
  with a padded Pipeline path reached output setup (`TypeError` from the sentinel
  configuration), instead of refusing quarantine. Canonical normalization closes it.
- `test_subset_fetch_still_returns_incomplete_when_existing_quarantine_is_not_selected`
  returned 0 instead of 3 when the persisted incident was not selected this run.
- UI reader identity/baseline regressions: 6 failed / 10 passed; cockpit broad
  override regressions: 2 failed / 16 passed. These now pass all 18 cases.
- `test_non_json_quarantine_mapping_refuses_before_replacing_existing_outputs`
  raised JSON `TypeError` after the CSV write; the guard now rejects non-dict
  context before I/O, with existing output bytes preserved (61 recommendation cases pass).
- Direct evaluation entry regressions: all 10 reached heavy-operation sentinels
  rather than rejecting quarantine. The completed suite passes 21 cases,
  including ordinary clean/holey/unstamped compatibility and retrain exit 2.

## Local review convergence

Independent cross-reviews covered acquisition/serialization, build/update/engine
gates, inference/UI, and the direct evaluation entry points. All reported P1/P2
items above were fixed, tested and re-reviewed. No accepted P3 items remain.
Final repository validation is tracked in `tasks.md` and the pull request.

## Final local gates (2026-09-23)

- Required `pytest tests/logic/ tests/governance/`: 5,609 passed, 33 skipped,
  2,087 subtests passed; 23 existing dependency/Windows fixture warnings remain
  visible. Completed in 266 seconds with a 593 MiB measured process-tree peak.
- Data pipeline: 1,385 passed, one skipped, 94 subtests passed.
- PIT: 34 passed. Lightweight regression: three passed, three skipped; the
  real-data walk-forward replay was explicitly not run locally.
- Ruff: all checks passed. Strict mypy: 243 source files passed.
- All changed `src/` modules, six CLI modules and three pure UI reader modules
  passed real imports. Streamlit page files are exercised by existing page
  wiring tests, not imported as live applications.
- `openspec validate quarantine-known-suspension-gap --strict`: passed.

## Deliberately separate acceptance

The live-data reconstruction, raw-file completeness audit, provider/model fidelity,
fresh rollback backup, supervised first update and scheduler restoration remain
unchecked production tasks. Synthetic green tests do not discharge those tasks.
UI verification covers pure readers and page wiring; no browser session or live
Streamlit server was started for this change.

## Publication-interruption follow-up (PR #497)

The first remote review found a real P1: replacing suspension raw bytes before
publishing their quarantine manifest could leave a shortened candidate beside a
clean v1 manifest after an interruption. Original fault injection covered
partition/preparation errors, but did not cover this inter-file commit boundary.
Evidence hashes alone could not protect the ordinary clean-manifest reader.

The fix records a bounded, hash-bound pending transaction before raw replacement.
Readers/builders/reset refuse a pending transaction, including when the manifest
is absent. Only an explicit matching real suspension refresh may recover it:
prove committed raw/manifest bytes, or preserve the candidate and restore retained
bytes, then force a real refresh. Candidate, journal and manifest writes are
fsynced; this does not claim a general multi-file or power-loss-atomic filesystem.

Observed fault-injection RED/GREEN results:

- Three publication regressions failed before the journal fix (3 failed / 72
  passed), because a clean manifest was readable across an unfinished write.
  The same focused run then passed all 75 cases.
- Initial-quarantine and full-restoration candidate durability tests both failed
  before candidate fsync was added (2 failed / 75 passed), with the explicit
  assertion that candidate bytes were not fsynced before pending publication.
  Both pass after the fix. These are actual observed failures, not inferred RED.
- Additional tests cover cleanup interruption, repeat recovery, malformed and
  oversized journals, mismatched hashes/ranges/policies, reset/dry/subset refusal,
  missing manifest, exclusive initial creation, and changes before rollback.
  These coverage additions are not claimed as pre-fix RED observations.
- The first full follow-up regression run found only the missing governance
  rationale on an optional absent-manifest digest (1 failed / 5,608 passed).
  Absence is an explicit recorded pre-publication state; unreadable/corrupt
  files still raise. The rationale is now stated at that return, not bypassed
  by weakening the governance test.

The initial Ubuntu CI also exposed a dependency-sensitive unused PyArrow type
suppression. The same constructor now has an explicit dynamic callable boundary;
runtime behavior is unchanged, and fresh local mypy passed all 244 source files.
The follow-up will be re-reviewed and receive a fresh Codex review request after
push. Production acceptance remains separate and unchecked.

Follow-up local gates: required logic/governance 5,609 passed / 33 skipped /
2,087 subtests (265 seconds, 23 unchanged warnings); data pipeline 1,438 passed /
one skipped / 94 subtests. Ruff passed after ordering the new imports, all six
changed source/CLI modules imported successfully, fresh strict mypy passed 244
source files, and strict OpenSpec validation passed. Independent final diff
review found no remaining P0/P1/P2; the missing-manifest coverage suggestion was
implemented, so no P3 was accepted instead of fixing it. All checks remain serial;
the full suite's measured process-tree peak was 564 MiB.

## Durable retry follow-up (PR #497, second review)

The next review found that rollback cleared the journal before the mandatory
real refetch had succeeded. A credential-construction error at that point left
only an in-memory retry flag, which a later ordinary invocation could lose.
The first interruption suite proved rollback and completed retry separately;
it missed failure after rollback and before the next real publication.

Actual RED: `test_client_failure_after_rollback_keeps_durable_refetch_obligation`
failed with `rollback must retain the durable real-refetch obligation` (one
failed test). After the fix the focused transaction/fetch/update run passed 142
cases. The journal now distinguishes `publishing` and `retry_required`, and only
an exact hash-verified selected refresh can read the prior manifest while the
durable block remains. A real prepared successor rebinds publication; failed or
manifest-only attempts cannot clear it. The previously complete old manifest
does not authorize blind resume after rollback, including across another failure.
Additional token/network/default refusal, absent-manifest, identical-byte retry,
complete-restoration and bad-phase cases are coverage additions, not claimed RED.

Independent local re-review also found that a retry rebind could adopt externally
changed prior-manifest bytes after its first check. Actual RED:
`test_retry_begin_cannot_adopt_manifest_changed_after_restored_pair_check` failed
with `DID NOT RAISE ValueError`. The rebind now keeps the original manifest hash
and rechecks the whole restored context before replacing its journal. After this
fix all 143 focused transaction/fetch/update cases passed. Both independent
reviewers then found no remaining P0/P1/P2 in the follow-up diff.

Final durable-retry gates: required logic/governance 5,609 passed / 33 skipped /
2,087 subtests (264 seconds, 23 unchanged warnings, 639 MiB process-tree peak);
data pipeline 1,460 passed / one skipped / 94 subtests. Ruff and real imports of
the changed source/CLI modules passed. Fresh strict mypy passed all 244 source
files, and strict OpenSpec validation passed. No production acceptance is claimed.
