# Delta for v2-today-workbench

## ADDED Requirements

### Requirement: A failed update SHALL escalate on the serving side's own verdict

The queue SHALL take the bundle's staleness verdict from the helper that
already reproduces the recommender's decision, and SHALL NOT compute a
staleness of its own. It SHALL raise the failed-update item to blocker when
that verdict says serving refuses today, or when the reported headroom is at
most half the serving threshold, and SHALL state the headroom in the detail.

A failed update that stays "attention" says the same thing on the first
night and on the third. Measured here: three consecutive failures
(2026-08-17 / 08-20 / 08-21) left the queue showing attention throughout
while the headroom fell from 14 days to 6, and no surface stated that number.

Computing it here instead of asking would repeat a defect this repository
has already paid for: the existing helper matches the recommender on three
separate decisions — the host-local clock rather than the operator-facing CN
one, the `behind > limit` boundary that still accepts a lag of exactly the
threshold, and the tail read from `calendars/day.txt` rather than the
`_fetch_integrity` identity that the health summary prefers. Each is pinned
by its own guard. A second implementation got all three wrong.

Half the threshold is a **stated policy** for an unattended failure, and it
is a different question from the cockpit's own `headroom <= 3` display
warning: that one describes the bundle, this one describes a failure that
nothing is repairing. It SHALL be derived from the reported threshold rather
than written as its own literal.

#### Scenario: a failed update with most of the headroom intact

- **GIVEN** a failed update and headroom above half the threshold
- **WHEN** the queue is built
- **THEN** the item stays attention and states the headroom

#### Scenario: a failed update with half the headroom gone

- **GIVEN** a failed update and headroom at or below half the threshold
- **WHEN** the queue is built
- **THEN** the item is a blocker

#### Scenario: a lag of exactly the threshold

- **GIVEN** a bundle lagging by exactly the serving threshold
- **WHEN** the queue is built
- **THEN** the item does not claim serving would refuse, because it accepts

#### Scenario: a verdict that cannot be reached

- **GIVEN** a bundle whose tail cannot be read unambiguously
- **WHEN** the queue is built
- **THEN** the severity is not raised on an assumed staleness
