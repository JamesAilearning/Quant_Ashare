## Context

The existing Streamlit pages deliberately separate command execution, daily
recommendation review, jobs, results, data inspection, and production
governance. That keeps ownership boundaries clear but leaves no read-only
landing page for the operator's daily sequence. The workbench must aggregate
those existing facts without creating a new runtime, data, or metric path.

## Goals / Non-Goals

**Goals:**

- Provide one read-only landing page for the daily operating sequence.
- Show an explicit distinction between bundle health, serving identity,
  recommendation provenance, and a trade-authorisation decision.
- Surface a current running job or latest failed job with a link to the
  existing Jobs page.
- Make a successful daily-signal run navigate to the exact published
  recommendation date in the existing review page.
- Group existing pages by operator task, not implementation module.

**Non-Goals:**

- No training, backtesting, factor mining, or automatic order execution.
- No new official metric, signal, or health computation.
- No stored operator portfolio, buy/hold/sell advice, experiment comparison,
  or promotion-policy change.

## Decisions

### Reuse existing read-side contracts

The page will call the existing bundle health, update-status, incumbent,
recommendation-artifact, provenance, cadence, and job-list helpers. It will
not reproduce their validation logic. In particular, a recommendation only
gets a HOLD or rebalance summary after the existing filename/payload and
incumbent provenance checks pass. A mismatch, legacy artifact, malformed v2
artifact, or unknown provenance becomes an explicit "needs verification"
summary.

This keeps the daily-decision page as the detailed authority and prevents the
workbench from silently treating the newest file as the current model output.

### Keep health, identity, and authorisation distinct

Bundle health is informational. A parseable serving manifest proves a serving
identity, not a promotion or trading authorisation. The workbench will use
careful labels and link to the existing production-operations page for the
full authorisation and recertification view.

### Use an explicit one-shot navigation request

After a successful run, the run centre will derive a date only from an
already-published file whose basename matches the established daily
recommendation artifact format. It records that date in a dedicated
session-state request key and renders an explicit "view this signal" button.
The daily-decision page consumes the request before building its select box.
The request is discarded when its date is absent from the available artifacts.

An immediate redirect would hide the command result and its `entry_date`
disclosure. An unqualified link to the recommendation page would reintroduce
"latest file" guessing. The explicit button preserves both facts.

### Keep workbench logic testable without Streamlit

New classification and parsing logic will live in a small pure helper module.
The Streamlit page is limited to rendering helper output and page links.
Tests will exercise provenance states, malformed artifacts, job precedence,
and the direct-navigation request without a live server.

## Risks / Trade-offs

- [A recommendation file changes between workbench reads] → readers validate
  each selected artifact and render an error/verification state rather than
  retaining an earlier successful classification.
- [A previous requested date has been deleted] → the daily-decision page
  drops the one-shot request and selects the normal newest artifact; it does
  not invent or retain a stale selection.
- [No job catalog is available] → the workbench renders an explicit no-job
  state; it never treats absence as a successful run.
- [The page is mistaken for a trading dashboard] → fixed copy states that it
  is read-only, that a signal is not an automatic order, and directs detailed
  review to the existing pages.

## Migration Plan

The change adds a page and navigation labels only. Existing direct URLs and
pages remain available. Rollback consists of removing the new page and its
navigation registrations; no runtime artifacts or persisted schemas change.

## Open Questions

None. The change uses existing UI artifacts and preserves the current manual
decision boundary.
