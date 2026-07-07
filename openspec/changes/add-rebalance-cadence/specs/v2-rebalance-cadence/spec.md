## ADDED Requirements

### Requirement: Rebalance cadence SHALL be configurable via signal thinning with an identity-preserving default

The walk-forward configuration SHALL expose `rebalance_cadence_days`
(int, default 1), `rebalance_phase` (int, default 0), and
`rebalance_anchor` (`"fold_phase"` default, or `"iso_week"`). The official
backtest SHALL implement cadence by THINNING prediction signal-stamp days
to the rebalance-day set BEFORE the execution-lag restamp — the rebalance
day is the signal-stamp day and the fill still happens at
T+`signal_to_execution_lag`; qlib's strategy holds the portfolio on
no-signal days (zero orders), positions continuing to accrue market-value
returns. `fold_phase` selects every Nth trading day of the evaluation
window starting at day `phase`; `iso_week` selects the first trading day of
each ISO week (the deployable calendar semantics). For N=1 the signal path
SHALL be byte-identical to the pre-change behavior (no filter constructed).
Validation SHALL reject fail-loud: non-positive or non-integer N, `phase`
outside `[0, N)`, an unknown anchor, and N=1 combined with a non-zero
phase (a meaningless combination must never pass silently).

#### Scenario: default is identity-preserving
- **WHEN** a run executes with `rebalance_cadence_days=1`
- **THEN** the prediction input reaching the strategy is byte-identical to
  the pre-change path and the REGEN-2 anchor stays green

#### Scenario: a no-signal day holds the portfolio (CONTRACT test)
- **WHEN** a real qlib backtest runs over the committed mini-bundle with a
  thinned signal
- **THEN** on a day without a signal stamp the backtest emits ZERO orders,
  positions are unchanged day-over-day, AND the account still accrues that
  day's market-value returns — a qlib upgrade flipping the empty-window
  semantics turns this scenario red before anything else

#### Scenario: meaningless phase combinations are refused
- **WHEN** a config sets `rebalance_cadence_days=1` with
  `rebalance_phase != 0` (or any phase outside `[0, N)`)
- **THEN** config construction raises with an actionable message

#### Scenario: derived artifacts thin consistently
- **WHEN** a thinned arm runs
- **THEN** ST-mask pairs, the exchange code universe, and the equal-weight
  baseline's daily top-k derive from the THINNED stamps only

### Requirement: Cadence fields SHALL be resume- and audit-visible

The resume fingerprint SHALL incorporate the three cadence fields (a resumed
run can never silently mix folds across cadence definitions); the fold
manifest SHALL record them additively with named-cause re-run messaging;
and the aggregate report SHALL carry them via the embedded config so a
comparison can prove cadence parity between runs.

#### Scenario: a cadence change invalidates resume with a named cause
- **WHEN** a run directory holds manifests from `rebalance_cadence_days=1`
  and the config now says 5
- **THEN** the folds re-run (fingerprint mismatch) and the log names the
  cadence change — never a bare unexplained re-run
