## Context

`read_journal()` already produces an append-only audit history plus an
`effective` mapping.  That mapping owns correction semantics: for each
`(trade_date, code)`, it selects the latest valid `decided_at`, breaking exact
timestamp ties by later file order.  UI code must consume this mapping rather
than rescan JSONL or invent another duplicate-resolution rule.

## Goals / Non-Goals

**Goals:** render current-candidate review counts, adopt/reject/watch labels,
latest effective review time, and a concise per-candidate reason; keep Today
Workbench's dated review queue on the same read model.

**Non-Goals:** append, edit, remove, or repair journal data; treat a journal
label as a trade, order, position, or serving change; make the journal a
runtime or official-metrics input.

## Decisions

### One pure projection

`summarise_daily_review_progress()` receives only the selected trade date,
already-validated candidate codes, and the journal's effective view.  It
rejects missing or duplicate candidate identifiers rather than manufacturing a
completion count.  For each exact `(trade_date, code)` key it creates a
candidate state; entries for other dates or unknown candidates are excluded
from the progress totals but remain visible in the existing audit table.

The returned immutable projection includes total, reviewed, unreviewed,
adopt/reject/watch counts, latest effective review time, and candidate states
in artifact order.  The page supplies Chinese display labels; the helper keeps
the persisted `adopt` / `reject` / `watch` vocabulary and remains free of
Streamlit and pandas.

### Page order and invalid states

The existing page continues to validate the artifact, `picks`, date match,
and HOLD boundary before it builds a projection.  A HOLD artifact keeps its
existing form block and does not render a completion summary or candidate
review labels.  Malformed journal lines, including rows with a blank or
non-string human reason, remain excluded by `read_journal()` at the persisted-data boundary;
the page shows its existing warning and labels counts as valid-record-only,
never as evidence of execution.

If an otherwise readable artifact has missing or duplicate candidate codes,
the page disables its form and candidate-progress projection because it cannot
make an exact journal-key mapping.  That artifact error does not invalidate
the separate append-only journal: the page continues to render valid audit
entries and malformed-row warnings rather than ending before the audit view.

### Shared Workbench consumer

The Today Workbench converts its already validated candidate-code list and
effective journal view through the same projection.  If its conservative
queue cannot verify journal input it still creates its existing verification
item; it does not substitute a zero count.

## Risks / Trade-offs

- A progress display can be mistaken for execution.  Every new label calls it
  `人工审阅`, and the page retains the explicit non-trading boundary.
- An existing malformed journal line makes audit completeness uncertain.  It
  is visibly warned and never converted into a valid review.
- Candidate identifiers are the only matching key.  Missing or duplicate
  codes are rejected rather than guessed from names, ranks, or scores.

## Migration Plan

This is an additive read-only view.  Removing the helper and render block
returns the page to its current journal form and audit table; no persisted
artifact or journal migration is required.

## Open Questions

None.
