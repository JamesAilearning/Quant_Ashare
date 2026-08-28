---
name: quant-ashare-ui-change
description: "Implement or fix Quant_Ashare Streamlit operator UI behavior, pages, navigation, and visual states while preserving existing runtime contracts. Use for requested UI changes; do not use for a read-only UI review."
---

# Quant_Ashare UI Change

Implement the smallest operator-facing UI change that meets the user's request.
The web layer is a consumer of explicit contracts: it must not become a second
runtime, metrics, selection, or promotion path.

## Before writing

- Follow the repository's OpenSpec routing for meaningful changes. Keep the
  change focused on one operator outcome.
- Locate the page, its sibling helper modules, and the producer of every
  artifact field the change will read. Never infer a JSON/report field from a
  similarly named function or page.
- Read two or three neighbouring render paths before adding a new state or
  action so the page's error, warning, and navigation conventions remain
  consistent.
- State whether the requested action is read-only, launches an existing audited
  runner, or mutates operator-only state. Do not introduce a hidden write path.

## Implementation boundaries

- Keep official metrics and research/production decisions in their canonical
  producer paths. UI may render existing evidence but must not recompute,
  promote, or silently substitute it.
- Preserve explicit provenance and failure states. Missing, corrupt, foreign,
  stale, superseded, or unverifiable artifacts need an honest visible state,
  never a default or neighbouring artifact as fallback.
- Reuse audited runners and lifecycle controllers for actions. Do not recreate
  subprocess, lock, stop, or status semantics inside a page.
- Keep Streamlit-only session/query handling at the page boundary; put parsing,
  classification, and cross-page handoff rules in Streamlit-free helpers when
  they need regression tests.

## Required coverage

Cover every new or changed operator outcome that is practical for the scope:

- normal/ready state;
- empty or first-use state;
- error, corrupt, or unverifiable artifact state; and
- running or disabled-action state when the page observes or launches work.

Add focused helper tests and the appropriate page/AppTest or source-boundary
tests. Perform browser acceptance for material visual or navigation changes,
including at least the useful empty and populated/error paths available locally.

## Finish safely

- Run targeted checks first, then the repository-required lint, import smoke,
  logic/governance tests, and OpenSpec validation when applicable.
- Do not run heavy suites in parallel. Keep the local machine responsive.
- Run the repository's local review workflow before presenting the change as
  ready. Report any path that could not be manually exercised because its real
  artifact was unavailable.
