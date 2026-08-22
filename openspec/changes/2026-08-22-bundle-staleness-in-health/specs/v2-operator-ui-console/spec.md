# Delta for v2-operator-ui-console

## ADDED Requirements

### Requirement: Bundle health SHALL report how much serving budget is left

The health summary SHALL carry the number of calendar days remaining before
the serving staleness floor refuses to score, and SHALL state that number in
its message whatever the badge colour. The floor is the producer's own
`bundle_max_age_days`; the summary SHALL NOT invent a second threshold of
its own.

Structural integrity alone is not health. Measured on this operator's
machine while the nightly update had failed three nights running, the card
read `状态 ok` for a bundle whose last day was **8 calendar days old with 6
days left before serving refuses** — the first view an operator sees was
green throughout. The summary's own docstring already claimed to describe
"freshness state" while computing nothing of the sort.

The status SHALL degrade to `error` once the remaining budget is exhausted,
because at that point serving would already refuse — a fact, not a chosen
threshold. No intermediate freshness threshold is introduced here;
escalating before exhaustion belongs to the task queue, which speaks about
what the operator must do rather than about the bundle itself.

The floor value SHALL be pinned against the producer's source rather than
imported, and a test SHALL fail when the two drift. Importing the producer
would pull qlib into the rendering process; restating the number without a
guard is how the two copies silently diverge.

#### Scenario: a bundle with budget left

- **GIVEN** a structurally clean bundle whose last day is inside the floor
- **WHEN** the health summary is produced
- **THEN** the status is unchanged by staleness and the message states the
  remaining days

#### Scenario: a bundle past the floor

- **GIVEN** a bundle whose last day is older than the floor allows
- **WHEN** the health summary is produced
- **THEN** the status is `error`

#### Scenario: a bundle whose last day is unknown

- **GIVEN** a provider with no readable coverage end date
- **WHEN** the health summary is produced
- **THEN** the remaining days are reported as unknown rather than assumed
