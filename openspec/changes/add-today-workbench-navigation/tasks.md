## 1. Read-side foundations

- [x] 1.1 Add pure helpers that classify a daily recommendation only after its
  date, shape, cadence, and incumbent-provenance checks pass.
- [x] 1.2 Add a pure operational-summary helper that selects a running job or
  latest terminal failure from the existing unified job summaries.
- [x] 1.3 Add pure parsing and one-shot selection helpers for a published
  daily-recommendation artifact date.

## 2. Operator UI

- [x] 2.1 Add the read-only Today Workbench page using existing health,
  update-status, incumbent, recommendation, and job readers.
- [x] 2.2 Reorganise existing navigation into daily decisions, research and
  validation, and production governance without removing a route.
- [x] 2.3 Add the Run Center success action and daily-decision selection handoff
  for an unambiguously published recommendation artifact.
- [x] 2.4 Persist the unambiguous published date across the action's second
  Streamlit rerun, clearing any superseded or ambiguous result.

## 3. Validation

- [x] 3.1 Add targeted logic and governance regression tests for provenance
  fail-closed behavior, job precedence, task navigation, and direct routing.
- [x] 3.2 Run targeted tests, module import smoke, repository logic/governance
  tests, and strict OpenSpec validation.
- [x] 3.3 Perform a local P0-P3 review of the final diff and resolve all
  findings above P3 before publishing.
