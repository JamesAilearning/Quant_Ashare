# Delta for v2-daily-decision-page

## MODIFIED Requirements

### Requirement: The candidate table SHALL pass through existing flags only

The candidate table SHALL render, per pick: rank, stock code, stock name,
predicted score, `tradable_flag`, `unavailable_reason` (which already carries
`st` / suspension / one-price-lock reasons from the generation side), plus a
display-only cost reference column comparing the predicted score against a
round-trip cost constant. The UI SHALL NOT compute any new
ST/suspension/PIT flag on its own; absent source fields render as absent.

The cost constant SHALL be **assembled from the certified production cost
convention**, never restated as a literal: the slippage term SHALL be read
from the certified guard profile (`scripts/eval_profiles.py`), and the
commission / stamp-tax terms — duplicated locally because the canonical
contract module pulls qlib into this production-facing page — SHALL be
pinned equal to their canonical sources by test. Assembly SHALL mirror the
backtest exchange convention (open = commission + slippage; close =
commission + stamp tax + slippage). The column header SHALL be derived from
the constant so header and subtrahend can never disagree.

The page SHALL disclose that the column is a **conservative lower bound**,
not a per-day hurdle: the score is a 1-day predicted return while production
holds ~5 days, so one round trip is amortized rather than paid daily.

#### Scenario: flags are pass-through
- **WHEN** a pick carries `tradable_flag=false` with `unavailable_reason`
- **THEN** the table shows exactly those values; no UI-side recomputation occurs

#### Scenario: cost reference tracks the certified convention
- **WHEN** the certified profile's one-way slippage moves
- **THEN** the cost constant, and the column header naming it, move with it
- **AND** no restated literal disagrees with the assembled value

#### Scenario: duplicated cost terms cannot rot
- **WHEN** the canonical commission default or the CN stamp-tax schedule moves
- **THEN** CI fails on the duplicated constants rather than the UI silently
  rendering a stale anchor

## ADDED Requirements

### Requirement: The page SHALL state that entry is an already-closed session

The candidate table SHALL be accompanied by an explicit disclosure that
`entry_date` names an **already-closed** trading session, not "buy at
tomorrow's open". Tradability screening (suspension / one-price-lock) needs
that session's real bars, so the generator can never emit a list for a
not-yet-traded session; reading the list as tomorrow's buy instruction would
place orders against a price that has already happened. The disclosure SHALL
name the actual `entry_date` value of the artifact on screen, and SHALL state
that how real orders converge to the list is the operator's execution
convention (the deviation the observation period records).

#### Scenario: entry semantics are disclosed on every artifact

- **WHEN** any recommendation artifact is rendered
- **THEN** the page states that its `entry_date` is an already-closed session
  and is not a next-morning buy instruction
