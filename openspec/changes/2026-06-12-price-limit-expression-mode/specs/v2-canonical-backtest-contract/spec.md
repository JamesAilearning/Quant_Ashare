# v2-canonical-backtest-contract Specification (delta)

## ADDED Requirements

### Requirement: Price limits SHALL be enforced via close-derived expressions

The canonical backtest SHALL pass `limit_threshold` to qlib as the
EXPRESSION-mode tuple computed from `$close` history
(`$close/Ref($close,1)-1` compared against ±magnitude), never as a float.
Float mode keys on the stored `$change` field, which the PIT bundle does
not produce, and qlib silently disables the limit checks when that field
is empty — allowing buys at limit-up and sells at limit-down. The contract
field remains a float MAGNITUDE (uniform across boards — a documented
conservative bias; per-board refinement is backlogged).

When the provider lacks a usable `$factor` field, the runner SHALL emit a
loud warning that qlib trades in adjusted-price mode with round lots
disabled (diagnostic; the official path proceeds).

#### Scenario: a limit-up fill day blocks the buy
- **WHEN** a top-scored signal's fill day closes +10% versus the prior
  close and the day is not one-price (high != low)
- **THEN** the canonical backtest holds no position in that name

#### Scenario: a limit-down fill day blocks the sell
- **WHEN** a held name is rotated out and its fill day closes -10%
- **THEN** the name remains in the book on (and after) that day

#### Scenario: float mode never reaches qlib
- **WHEN** `BacktestRunner.run` constructs qlib exchange kwargs
- **THEN** `limit_threshold` is the two-element expression tuple derived
  from the contract magnitude
