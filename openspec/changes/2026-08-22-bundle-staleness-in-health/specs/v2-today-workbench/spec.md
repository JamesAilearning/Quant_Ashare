# Delta for v2-today-workbench

## ADDED Requirements

### Requirement: A failed update SHALL escalate as the serving budget drains

The queue SHALL raise the failed-update item from attention to blocker once
the remaining staleness budget is at most half the floor, and SHALL state
the remaining days in the item's detail.

A failed update that stays "attention" says the same thing on the first
night and on the third. Measured here: three consecutive failures
(2026-08-17 / 08-20 / 08-21) left the queue showing attention throughout
while the budget fell from 14 days to 6.

Half the floor is a **stated policy**, not a tuned number: at that point the
time left to fix is no longer greater than the time already lost. It SHALL
be derived from the floor rather than written as its own literal, so
changing the floor moves it too.

#### Scenario: an update failed with most of the budget intact

- **GIVEN** a failed update and remaining days above half the floor
- **WHEN** the queue is built
- **THEN** the item stays attention

#### Scenario: an update failed with half the budget gone

- **GIVEN** a failed update and remaining days at or below half the floor
- **WHEN** the queue is built
- **THEN** the item is a blocker and its detail states the remaining days
