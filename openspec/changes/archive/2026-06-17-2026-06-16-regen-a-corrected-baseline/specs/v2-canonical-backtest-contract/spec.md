# v2-canonical-backtest-contract Specification (delta)

## ADDED Requirements

### Requirement: The walk-forward regression baseline SHALL be replay-anchored

The committed walk-forward regression baseline SHALL be reproducible by a
DETERMINISTIC frozen-score replay — replaying fixed per-fold prediction Series
through the canonical `BacktestRunner` at the official semantics (T+1 execution,
close-derived price limits, PIT ST exclusion) — WITHOUT retraining any model or
rebuilding the bundle. The replay SHALL reproduce the committed aggregate AND
per-fold metrics to machine precision, and the regression test SHALL hold that
tolerance in TEST SOURCE (not in the fixture) so a tampered fixture cannot widen
its own gate.

The committed baseline JSON SHALL carry, alongside the numbers, the corrected
semantics, the statistical caveat (the headline shift is within cross-fold noise
and is a metric correction — NOT a strategy improvement and not predictive of
live performance), and the total-return-benchmark deferral note. A CI-runnable
governance test SHALL pin that this framing is committed with the value.

#### Scenario: a deterministic replay reproduces the committed baseline
- **WHEN** the frozen-score replay runs against the same bundle
- **THEN** every committed aggregate and per-fold metric reproduces within the
  in-source tight tolerance, else the regression test fails

#### Scenario: the corrected value cannot be committed without its framing
- **WHEN** the committed baseline JSON holds the corrected headline IR
- **THEN** a CI-runnable pin requires the corrected-semantics provenance, the
  within-noise statistical caveat, and the total-return-deferral note to be
  present, else CI fails

#### Scenario: no single-fold anchor
- **WHEN** the walk-forward regression suite runs
- **THEN** the anchor is the full multi-fold deterministic replay, not a single
  fold (the most volatile, sign-flipping, within-noise fold is not used alone)
