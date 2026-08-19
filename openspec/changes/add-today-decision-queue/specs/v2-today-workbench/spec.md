## MODIFIED Requirements

### Requirement: Render a complete ordered daily queue from read-only sources

The Today Workbench SHALL render an ordered, read-only `今日待办` queue in
addition to its summary cards.  It SHALL derive items only from existing
operator read models, classify each as blocker, attention, in-progress,
review, or information, and use a stable priority/time/source-key order.
The queue SHALL NOT execute, retry, cancel, approve, or mutate any job,
artifact, configuration, serving state, or trading state.

#### Scenario: Multiple distinct exceptions coexist

- **WHEN** the current daily artifact needs verification and two distinct jobs
  have exceptional terminal statuses
- **THEN** the queue shows all three distinct items
- **AND** does not replace the two job exceptions with one representative item

#### Scenario: No blockers are present

- **WHEN** the data bundle and latest daily artifact are healthy and no job
  requires attention
- **THEN** the page states that there are no blockers
- **AND** does not claim that a trade, position, or order was executed

### Requirement: Preserve verification and precise navigation context

Every queue item SHALL state its source/reason, source time when available,
and a navigation-only destination.  Unreadable, corrupt, mismatched, or
otherwise unverifiable data SHALL create a visible needs-verification item
rather than an optimistic status.  A manual-review item for a valid dated
signal SHALL carry that exact artifact date to the daily decision page.

#### Scenario: A valid signal still has candidates without an effective review

- **WHEN** a valid current signal has candidate codes without effective
  journal decisions for its artifact date
- **THEN** the queue shows a review item with the remaining count
- **AND** navigation opens the daily decision page with that artifact date

#### Scenario: Journal data cannot be read

- **WHEN** the decision journal cannot be read
- **THEN** the queue shows a needs-verification item with the journal reason
- **AND** does not report zero outstanding reviews
