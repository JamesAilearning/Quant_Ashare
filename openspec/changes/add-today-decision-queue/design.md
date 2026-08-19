## Context

The Workbench is the top-level navigation surface after PR #451.  Its cards
remain useful, but its single `OperationalSummary` deliberately collapses all
jobs into one result.  The queue must add a fuller view without changing those
card semantics or becoming an execution console.

## Goals / Non-Goals

**Goals:** show all visible blockers and attention items, derive a stable
priority order from trusted read models, provide one precise destination for
each item, and identify pending manual review for a valid signal.

**Non-Goals:** run, retry, cancel, approve, write, poll, or change state;
alter signal, data, official metrics, serving, or trading semantics; infer an
unavailable artifact as a benign no-op.

## Decisions

### Inputs and no-hidden-fallback boundary

The page already reads the current provider health, update status, incumbent,
latest daily artifact, and unified job catalog.  The queue receives their
read-model values plus a decision-journal effective view.  Invalid read inputs
produce a visible verification item; no new reader duplicates the signal or
journal validation rules.

### Queue model and ordering

Each pure `TodayQueueItem` has an immutable kind, a source key, title, detail,
source time, page destination, and optional exact context.  Priority is
blocker (0), attention (1), in-progress (2), review (3), information (4).
Within a priority, items sort by newest available source timestamp descending,
then by a deterministic source key.  De-duplication is only by the same source
key: distinct failed jobs always remain distinct.

### Daily review candidate

A review item is permitted only when the latest signal is a valid `daily` or
`rebalance` artifact and its candidate rows can be rendered by the shared
`picks_table_rows` boundary.  It compares candidate codes with the journal's
existing effective decisions for the same artifact date.  A journal read error
becomes a verification item rather than a fake zero-review count.

### Navigation

Queue navigation is limited to existing pages.  It uses native page links only;
a dated review link passes its artifact date as a URL hint, which the daily
decision page consumes once into its existing date-selection handoff.  This
carries navigation context only and writes neither the artifact nor the
journal.  Jobs links retain their status query filter.

## Risks / Trade-offs

- A queue can grow with historical failures.  It displays all visible distinct
  exceptions but defaults non-blocking sections to a collapsible view.
- The journal is operator-owned state, not a canonical runtime input.  Its
  review count is labelled human review progress only, never trade execution.
- A missing update-status artifact is informational, because absence alone
  cannot prove the data bundle is unavailable; actual bundle health remains
  the decision blocker.

## Migration Plan

The change is additive and read-only.  Existing cards and navigation remain
unchanged.  Removing the queue renderer/helper returns the Workbench to the
previous view without artifact or data migration.

## Open Questions

None.
