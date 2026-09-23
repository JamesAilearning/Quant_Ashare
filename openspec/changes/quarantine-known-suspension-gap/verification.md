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
