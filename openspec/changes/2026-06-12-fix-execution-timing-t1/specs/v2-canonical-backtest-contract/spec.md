# v2-canonical-backtest-contract Specification (delta)

## MODIFIED Requirements

### Requirement: Canonical backtest input SHALL define explicit signal lag semantics

The canonical backtest input SHALL define `signal_to_execution_lag` as the
TOTAL number of trading days between a signal's stamp and its fill,
INCLUSIVE of qlib's built-in one-day consumption shift
(`TopkDropoutStrategy` consumes, on trade day D, the signal stamped D-1).
The external restamp applied by the runner SHALL therefore be `lag - 1`
trading rows: `1` (the default) SHALL apply no external restamp and fill on
T+1; values above `1` SHALL restamp by exactly `lag - 1` rows. `0` SHALL be
REJECTED on the canonical path: same-day execution requires restamping
signals backward — look-ahead — and the canonical runner stamps every
output `metric_status=official`, so a look-ahead run could masquerade as
official. Negative values and booleans SHALL likewise be rejected.

#### Scenario: default lag fills on the next trading day
- **WHEN** a caller supplies `signal_to_execution_lag=1` and a signal
  stamped day T
- **THEN** `BacktestRunner` applies no external restamp
- **AND** the position first exists on T+1 through the real qlib path

#### Scenario: zero lag is rejected as look-ahead
- **WHEN** a caller supplies `signal_to_execution_lag=0`
- **THEN** `CanonicalBacktestContract.validate_input` raises
  `CanonicalBacktestContractError`
- **AND** the pipeline and walk-forward config layers reject it identically

#### Scenario: lag two restamps one row
- **WHEN** a caller supplies `signal_to_execution_lag=2`
- **THEN** `BacktestRunner` restamps predictions by exactly one trading row

#### Scenario: negative lag is rejected
- **WHEN** a caller supplies `signal_to_execution_lag=-1`
- **THEN** `CanonicalBacktestContract.validate_input` raises `CanonicalBacktestContractError`

#### Scenario: boolean lag is rejected
- **WHEN** a caller supplies `signal_to_execution_lag=True`
- **THEN** `CanonicalBacktestContract.validate_input` raises `CanonicalBacktestContractError`

## ADDED Requirements

### Requirement: Availability masks SHALL filter by the true execution day

The microstructure (suspension / one-price-lock) and ST masks SHALL drop a
prediction row when its EXECUTION day — the trading day after its
post-restamp stamp — is masked, not when its stamp day is. ST attribution
records SHALL carry the execution date. A signal stamped on the final
evaluation day has no in-window execution day and SHALL be treated as
untradeable-by-construction (neither masked nor filled).

#### Scenario: top score suspended on its execution day never fills
- **WHEN** a ticker carries the panel's highest day-T score and is
  suspended (volume 0) on T+1
- **THEN** the canonical backtest holds no position in that ticker on any
  day

### Requirement: Headline IC SHALL be label-aligned

`SignalAnalyzer`'s per-period headline IC (`mean_ic`, and the derived
`ic_1d`/`ic_5d`/`mean_ic_1d` consumers) SHALL correlate day-T scores with
the T+1 → T+1+period return — the window the training label defines and a
lag=1 strategy actually earns. The legacy stamp-day window (T → T+period)
SHALL survive only as an explicitly named secondary metric
(`mean_ic_stamp_day`), and each period summary SHALL name its convention.

#### Scenario: conventions are sharply distinguishable
- **WHEN** prices are constructed so the T+1→T+2 window ranks exactly with
  the scores while the T→T+1 window ranks exactly against them
- **THEN** the headline `mean_ic` reads +1 and `mean_ic_stamp_day` reads -1
