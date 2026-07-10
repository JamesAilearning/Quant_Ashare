# v2-rebalance-cadence Specification

## Purpose

Make the portfolio rebalance cadence configurable via signal thinning
(阶段7 enabler, Route A): emitting predictions only on scheduled rebalance
days holds the portfolio elsewhere (qlib's zero-order None branch), with an
identity-preserving daily default and both a per-fold (fold_phase) and a
deployable calendar (iso_week) anchor. Shipped by `add-rebalance-cadence`
(#336); the 7b cadence×horizon campaign consumed it and concluded a
pre-registered DEAD-END (docs/prereg/cadence_horizon_results.md — the
label×cadence plane is exhausted; the enabler stays as a production opt-in,
default N=1 byte-identical).

## Requirements

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
Thinning precedes the position-based execution-lag restamp, which is
calendar-correct only on a dense daily series; therefore a non-daily
cadence (N>1) SHALL be supported ONLY at `signal_to_execution_lag=1` — the
combination of N>1 and lag>1 SHALL be refused rather than silently landing
the fill ~N trading days out. Validation SHALL reject fail-loud, AT BOTH
`WalkForwardConfig` construction AND the `BacktestRunner.run`
official-metrics boundary (direct callers bypass the config): non-positive
or non-integer N, `phase` outside `[0, N)`, an unknown anchor, N=1 combined
with a non-zero phase, `iso_week` with non-nominal N/phase, and N>1 with
lag>1.

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

#### Scenario: a non-daily cadence with lag>1 is refused
- **WHEN** `rebalance_cadence_days > 1` is combined with
  `signal_to_execution_lag > 1`
- **THEN** both `WalkForwardConfig` construction and `BacktestRunner.run`
  raise fail-loud, naming the thinning-before-restamp interaction — the
  fill is never silently landed ~N trading days out

#### Scenario: the runner boundary validates direct callers
- **WHEN** `BacktestRunner.run` is called directly (bypassing
  `WalkForwardConfig`) with an invalid cadence (bad phase, unknown anchor,
  or the lag interaction)
- **THEN** it raises `BacktestRunnerError` before producing official metrics

#### Scenario: the schedule is calendar-defined, not stamp-defined
- **WHEN** a scheduled rebalance trading day is absent from the prediction
  index (a masked/missing cross-section)
- **THEN** that day is HELD (nothing kept for it) — the cadence never
  silently shifts to the first available signal date or an off-schedule
  weekday; the schedule is derived from the evaluation window's trading
  calendar

#### Scenario: the unfillable final in-window day is excluded
- **WHEN** the cadence schedule would select the LAST in-window trading day
  (whose T+1 execution day is outside the window under lag=1)
- **THEN** that stamp is excluded from the traded set (qlib cannot fill it,
  so counting it would desync IC/attribution/exchange from the backtest);
  if excluding it leaves no fillable rebalance day, the run FAILS LOUD
  rather than publishing an all-hold official result

#### Scenario: the equal-weight baseline is omitted on a thinned arm
- **WHEN** a non-daily cadence runs with `compute_baselines=True`
- **THEN** `equalweight_topk` is OMITTED (a WARN explains why) rather than
  published as a one-day-hold series that would drop the held strategy's
  hold-day P&L — the strategy metrics are unaffected

#### Scenario: fold IC and attribution describe the traded (thinned) signal set
- **WHEN** a non-daily cadence runs
- **THEN** BOTH the fold `ic_1d`/`ic_5d` (and aggregate `mean_ic_*`) AND the
  per-fold Brinson attribution are computed over the predictions THINNED to
  the rebalance schedule — the same schedule the backtest trades — so the
  descriptive IC is not identical across cadences and the attribution
  universe (prediction instruments ∪ held) excludes names that only appear
  on skipped no-signal days; the default daily cadence leaves both inputs
  unchanged (byte-identical)

#### Scenario: derived artifacts thin consistently
- **WHEN** a thinned arm runs
- **THEN** ST-mask pairs and the exchange code universe derive from the
  THINNED stamps only (the equal-weight baseline is not thinned — it is
  OMITTED for a non-daily cadence per the scenario above, since its
  one-day-hold shape cannot represent a held-across-days arm)

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
