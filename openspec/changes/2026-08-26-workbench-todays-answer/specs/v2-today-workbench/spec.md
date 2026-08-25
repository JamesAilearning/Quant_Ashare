# Delta for v2-today-workbench

## ADDED Requirements

### Requirement: The workbench SHALL synthesize one answer to "should I buy today" from existing verdicts only

The workbench SHALL render, as its first card, a single synthesized answer to
the operator's daily question — buy today, watch today, or an honest
non-answer — and every input to that synthesis SHALL come from a verdict the
page already computes: bundle preconditions from the serving side's own
freshness verdict consumed in FULL (`usable` — age, integrity, and the
health summary's withhold-only share; consuming only part of it lets this
card say "buy" while the health card on the same page reports a problem),
cadence and pick cardinality from the provenance-verified daily-signal
summary, and day-ownership from `entry_date` equal to the operator-facing
CN calendar day. The synthesis layer SHALL NOT derive staleness, cadence,
or day-ownership of its own.

An empty target list on a rebalance day is a legitimate producer state
(`--topk 0`, or every candidate masked): the card SHALL NOT call it a buy
instruction — it SHALL say there is nothing to buy and why that can be
legitimate. A buy answer SHALL state the number of candidates.

The serving-side verdict takes precedence: when serving would refuse today,
the card SHALL refuse to answer even if an artifact looks current — that
combination is unreachable through the normal flow, and when it appears one
side is lying; refusing is more honest than picking one.

Every state SHALL carry the disclaimer that the sentence is not an order and
grants no trading permission.

#### Scenario: a verified rebalance instruction whose entry date is today

- **GIVEN** a provenance-verified rebalance artifact with a non-empty target
  list and `entry_date` equal to the CN calendar day, and a serving-side
  verdict that accepts today
- **WHEN** the card renders
- **THEN** it says there is a buy instruction pending human review, states
  the candidate count, and points at the detail page

#### Scenario: a rebalance day whose target list is empty

- **GIVEN** a provenance-verified rebalance artifact for today whose target
  list is empty
- **WHEN** the card renders
- **THEN** it says there is nothing to buy, names the empty list, and does
  not raise an error state

#### Scenario: a bundle failing a health precondition beyond age and integrity

- **GIVEN** age and integrity pass while the health summary still withholds
  (for example a missing instruments directory)
- **WHEN** the card renders
- **THEN** it refuses to answer and carries the health reason

#### Scenario: a verified HOLD whose entry date is today

- **GIVEN** a provenance-verified HOLD artifact for today
- **WHEN** the card renders
- **THEN** it says not to buy, names the next rebalance date, and stays a
  navigation summary

#### Scenario: serving refuses today

- **GIVEN** the serving-side freshness verdict refuses today
- **WHEN** the card renders
- **THEN** it refuses to answer and states the days behind and the serving
  limit as numbers

#### Scenario: an artifact that could not be verified

- **GIVEN** the latest artifact fails provenance or shape verification
- **WHEN** the card renders
- **THEN** it refuses to answer and carries the verification failure reason

### Requirement: A non-answer SHALL distinguish a flow state from an abnormal state

The card SHALL separate "there is no instruction addressed to today" (a flow
state: no artifact yet, or the newest instruction's entry date is another
day — named verbatim) from "the question cannot be answered" (an abnormal
state: a refusing or unreachable verdict, or an unverifiable artifact, with
the reason). The two read differently and demand different next steps; the
card SHALL NOT collapse them into one optimistic or one alarming state.

#### Scenario: the newest instruction addresses an earlier day

- **GIVEN** a verified artifact whose `entry_date` is before today
- **WHEN** the card renders
- **THEN** it says there is no instruction for today, names that entry date,
  and says today's signal has not been generated

#### Scenario: no artifact at all

- **GIVEN** no daily recommendation artifact exists
- **WHEN** the card renders
- **THEN** it says there is no instruction for today and points at the run
  center, without raising an error state
