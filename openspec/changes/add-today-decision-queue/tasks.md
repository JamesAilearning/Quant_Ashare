## 1. Queue read model

- [x] 1.1 Define pure queue item, priority, stable ordering, and source-key de-duplication helpers.
- [x] 1.2 Map existing health, update, signal, job, and journal evidence into conservative queue items.
- [x] 1.3 Add focused tests for multiple failures, order, deduplication, verification, and review progress.

## 2. Workbench rendering

- [x] 2.1 Read the existing journal/effective decisions and valid signal candidate codes without adding artifact parsers.
- [x] 2.2 Render counts, blocker/attention visibility, collapsed lower-priority items, and navigation-only links with one-shot Jobs filter handoff context.
- [x] 2.3 Carry a dated review item to the existing daily-decision page without journal or artifact writes.

## 3. Verification

- [x] 3.1 Run focused tests, ruff, and import/startup smoke tests for touched modules.
- [x] 3.2 Run required logic/governance tests and strict OpenSpec validation.
- [x] 3.3 Perform local code review and resolve P0/P1/P2 findings.
