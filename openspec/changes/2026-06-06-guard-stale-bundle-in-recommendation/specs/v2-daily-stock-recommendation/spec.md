# v2-daily-stock-recommendation Specification (delta)

## ADDED Requirements

### Requirement: Daily recommendation SHALL refuse to emit a list when the price/feature bundle is stale

`recommend` SHALL verify the bundle's freshness against an EXTERNAL reference
date and refuse to emit a list when the bundle is stale — because it resolves
the as-of date from the qlib bundle's own calendar, it cannot otherwise detect
its own staleness. It SHALL compare the bundle's last trading day to a reference
"today" (the system date in production, injectable for tests and intentional
historical runs) and, if the lag exceeds the configured `bundle_max_age_days`
(calendar days), SHALL raise an explicit error and emit no list rather than
scoring on stale prices. The tolerance SHALL be generous enough that a normal
pre-holiday gap (no new data during a multi-day market holiday) does not trip
it. A bundle whose last trading day is on or after the reference today SHALL NOT
be treated as stale.

#### Scenario: a stale bundle is rejected
- **WHEN** the bundle's last trading day lags the reference today by more than
  `bundle_max_age_days`
- **THEN** an explicit error is raised and no list is produced
- **AND** the error names the bundle's last day and the remedy (update the
  bundle before recommending)

#### Scenario: a fresh bundle (including a normal holiday gap) is accepted
- **WHEN** the bundle's last trading day lags the reference today by no more
  than `bundle_max_age_days` — including a multi-day market-holiday gap during
  which no new data is expected
- **THEN** the freshness guard does not raise and the list is produced

#### Scenario: the reference today is injectable and deterministic
- **WHEN** a reference today is supplied to `recommend`
- **THEN** the freshness comparison uses that value rather than the wall-clock
  date, so the guard is deterministic for tests and lets an operator override
  it for an intentional historical run
