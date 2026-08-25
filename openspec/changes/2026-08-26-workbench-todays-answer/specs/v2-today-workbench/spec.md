# Delta for v2-today-workbench

## ADDED Requirements

### Requirement: The workbench SHALL synthesize one answer to "should I buy today" from existing verdicts only

The workbench SHALL render, as its first card, a single synthesized answer to
the operator's daily question — a pending rebalance instruction, nothing to
do, or an honest non-answer — and every input to that synthesis SHALL come
from a verdict the page already computes: bundle preconditions from the
serving side's own freshness verdict consumed in FULL (`usable` — age,
integrity, and the health summary's withhold-only share; consuming only part
of it lets this card present an instruction while the health card on the
same page reports a problem), cadence and pick cardinality from the
provenance-verified daily-signal summary, and instruction currency from
`entry_date` compared against the freshness verdict's own calendar tail.
The synthesis layer SHALL NOT derive staleness, cadence, or currency of its
own, and SHALL NOT consult a wall clock.

The card SHALL honour the daily-decision baseline's entry semantics:
`entry_date` names an **already-closed** session, the list is not a
next-morning buy instruction, and how real orders converge to the list is
the operator's execution convention. Every state that names the artifact's
`entry_date` — current, empty-list, and superseded alike — SHALL restate
that disclosure, and the card SHALL NOT phrase any execution imperative or
equate `entry_date` with a buy day. The abnormal state that rejects an
artifact's claimed session is exempt: it presents the claim as rejected,
not as content.

Before comparing any dates, the card SHALL bind the artifact to the current
bundle by the producer-written data provenance: `meta.provider_uri` MUST
match the current provider under the recommender's own normalization, and
when both the artifact's `meta.bundle_tag` and the current integrity stamp's
identity tag exist they MUST match. A missing artifact `provider_uri`, an
unidentifiable current side, or either mismatch SHALL refuse the answer with
both spellings named — an artifact from another or a superseded bundle can
match the current tail by date coincidence while every health check on the
page describes different data. A missing identity tag on either side is a
legitimate stamp state (identity blocks are optional): the card SHALL fall
back to provider binding alone, and SHALL NOT refuse for that reason or
pretend the tags were compared.

An empty target list on a rebalance day is a legitimate producer state
(`--topk 0`, or every candidate masked): the card SHALL NOT call it an
instruction — it SHALL say there is nothing to buy and why that can be
legitimate. A rebalance answer SHALL state the number of candidates.

Every state SHALL carry the disclaimer that the sentence is not an order and
grants no trading permission.

#### Scenario: a verified rebalance instruction current with the data tail

- **GIVEN** a provenance-verified rebalance artifact with a non-empty target
  list whose `entry_date` equals the freshness verdict's calendar tail, and
  a serving-side verdict that accepts today
- **WHEN** the card renders
- **THEN** it says there is a rebalance instruction pending human review,
  states the candidate count and the already-closed-session disclosure, and
  points at the detail page without naming an execution day

#### Scenario: a verified HOLD current with the data tail

- **GIVEN** a provenance-verified HOLD artifact whose `entry_date` equals
  the calendar tail
- **WHEN** the card renders
- **THEN** it says there is nothing to do, names the next rebalance date,
  and stays a navigation summary

#### Scenario: a rebalance day whose target list is empty

- **GIVEN** a provenance-verified rebalance artifact current with the tail
  whose target list is empty
- **WHEN** the card renders
- **THEN** it says there is nothing to buy, names the empty list, and does
  not raise an error state

#### Scenario: serving refuses today

- **GIVEN** the serving-side freshness verdict refuses today
- **WHEN** the card renders
- **THEN** it refuses to answer and states the days behind and the serving
  limit as numbers

#### Scenario: a bundle failing a health precondition beyond age and integrity

- **GIVEN** age and integrity pass while the health summary still withholds
  (for example a missing instruments directory)
- **WHEN** the card renders
- **THEN** it refuses to answer and carries the health reason

#### Scenario: an artifact from another provider or a superseded bundle

- **GIVEN** a verified artifact whose `meta.provider_uri` does not match the
  current provider, or whose `meta.bundle_tag` differs from the current
  integrity stamp's identity tag
- **WHEN** the card renders
- **THEN** it refuses to answer and names both spellings

#### Scenario: an identity tag missing on one side

- **GIVEN** a verified artifact whose provider matches while the artifact or
  the current stamp carries no identity tag
- **WHEN** the card renders
- **THEN** it answers on provider binding alone, without refusing and
  without claiming the tags were compared

#### Scenario: an artifact that could not be verified

- **GIVEN** the latest artifact fails provenance or shape verification
- **WHEN** the card renders
- **THEN** it refuses to answer and carries the verification failure reason

### Requirement: A non-answer SHALL distinguish a flow state from an abnormal state

The card SHALL separate "there is no current instruction" (a flow state: no
artifact yet, or the data tail has moved past the newest instruction's
`entry_date` — both dates named verbatim) from "the question cannot be
answered" (an abnormal state: a refusing or unreachable verdict, an
unverifiable artifact, or an artifact claiming a session later than the data
tail — a state the producer cannot legitimately emit). The two read
differently and demand different next steps; the card SHALL NOT collapse
them into one optimistic or one alarming state.

#### Scenario: the data tail has moved past the newest instruction

- **GIVEN** a verified artifact whose `entry_date` is before the freshness
  verdict's calendar tail
- **WHEN** the card renders
- **THEN** it says the newest instruction has not caught up with the data,
  names both dates, and points at the run center

#### Scenario: an artifact claiming a session later than the data tail

- **GIVEN** a verified artifact whose `entry_date` is after the calendar
  tail
- **WHEN** the card renders
- **THEN** it refuses to answer, names both dates, and asks for source
  verification

#### Scenario: no artifact at all

- **GIVEN** no daily recommendation artifact exists
- **WHEN** the card renders
- **THEN** it says there is no instruction and points at the run center,
  without raising an error state
