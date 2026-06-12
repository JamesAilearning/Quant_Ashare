# v2-canonical-backtest-contract Specification (delta)

## ADDED Requirements

### Requirement: Price limits SHALL be enforced via close-derived expressions

The canonical backtest SHALL pass `limit_threshold` to qlib as the
EXPRESSION-mode tuple computed from `$close` history in Not-form
(`Not(move <= magnitude)` / `Not(move >= -magnitude)` where `move` is
`$close/Ref($close,1)-1`), never as a float. Float mode keys on the stored
`$change` field, which the PIT bundle does not produce, and qlib silently
disables the limit checks when that field is empty — allowing buys at
limit-up and sells at limit-down. The Not-form SHALL block fills whose
move is UNVERIFIABLE (NaN previous close — resumption days after a
suspension gap, a ticker's first bundle day): unverifiable ⇒ untradeable,
since the bare comparison form would silently permit them.

The adjusted-close ratio is the exchange-correct limit test, not an
approximation: tushare's adj_factor derives from the exchange-published
previous close (the rounded 除权除息参考价), so on ex-dividend/ex-rights
days the ratio equals the exchange's own move against its limit reference
(verified empirically across all 2021-2025 corporate-action days: zero
missed main-board limit closes). Raw-price ratios would diverge by the
full event magnitude on every ex-date and SHALL NOT be used. The contract
field remains a float MAGNITUDE (uniform across boards — a documented
conservative bias covering 688/300 ±20%, BJ ±30%, ST ±5%; per-board
refinement is backlogged).

The runner SHALL bound the exchange quote universe (`codes`) to the
post-mask tradable signal set, benchmark excluded, and SHALL refuse to run
when that set is empty (qlib would silently substitute the full provider
universe and emit zero-position official metrics). When the bounded
universe lacks a usable `$factor` (NaN factor on a row whose close is
present), the runner SHALL emit a loud warning that qlib trades in
adjusted-price mode with round lots disabled (diagnostic; the official
path proceeds). The price-limit semantics version SHALL be folded into
backtest provenance and the walk-forward resume fingerprint so pre- and
post-enforcement runs are never conflated.

#### Scenario: a limit-up fill day blocks the buy
- **WHEN** a top-scored signal's fill day closes +10% versus the prior
  close and the day is not one-price (high != low)
- **THEN** the canonical backtest holds no position in that name

#### Scenario: a limit-down fill day blocks the sell
- **WHEN** a held name is rotated out and its fill day closes -10%
- **THEN** the name remains in the book on (and after) that day

#### Scenario: float mode never reaches qlib
- **WHEN** `BacktestRunner.run` constructs qlib exchange kwargs
- **THEN** `limit_threshold` is the two-element Not-form expression tuple
  derived from the contract magnitude

#### Scenario: an unverifiable move blocks conservatively
- **WHEN** a signal's fill day follows a suspension gap (no previous bar,
  so the close move cannot be verified)
- **THEN** the fill is blocked even though numpy NaN comparisons would
  otherwise report "not at limit"

#### Scenario: a non-limit day fills normally (vacuity control)
- **WHEN** the same limit-up ticker is signalled for a 0%-move fill day
- **THEN** the position fills — proving the limit block above is
  attributable to the limit, not to an unrelated defect

#### Scenario: a fully-masked signal fails loud
- **WHEN** every prediction row is removed by the availability masks
- **THEN** the runner raises instead of emitting zero-position official
  metrics over qlib's silently-substituted full universe
