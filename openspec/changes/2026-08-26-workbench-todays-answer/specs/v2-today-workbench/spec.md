# Delta for v2-today-workbench

## ADDED Requirements

### Requirement: The workbench SHALL synthesize one answer to "should I buy today" from existing verdicts only

The workbench SHALL render, as its first card, a single synthesized answer to
the operator's daily question — buy today, watch today, or an honest
non-answer — and every input to that synthesis SHALL come from a verdict the
page already computes: staleness and integrity from the serving side's own
freshness verdict (the decision-queue's consumption surface), cadence from
the provenance-verified daily-signal summary, and day-ownership from
`entry_date` equal to the operator-facing CN calendar day. The synthesis
layer SHALL NOT derive staleness, cadence, or day-ownership of its own.

The serving-side verdict takes precedence: when serving would refuse today,
the card SHALL refuse to answer even if an artifact looks current — that
combination is unreachable through the normal flow, and when it appears one
side is lying; refusing is more honest than picking one.

Every state SHALL carry the disclaimer that the sentence is not an order and
grants no trading permission.

#### Scenario: a verified rebalance instruction whose entry date is today

- **GIVEN** a provenance-verified rebalance artifact with `entry_date` equal
  to the CN calendar day, and a serving-side verdict that accepts today
- **WHEN** the card renders
- **THEN** it says there is a buy instruction pending human review, and
  points at the detail page

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
