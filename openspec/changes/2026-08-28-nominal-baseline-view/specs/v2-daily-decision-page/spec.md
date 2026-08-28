# Delta for v2-daily-decision-page

## ADDED Requirements

### Requirement: The page SHALL name the rebalance day its nominal roster comes from

The page SHALL identify, for the selected trade date, the most recent artifact
that records an actual rebalance, and SHALL show that artifact's date together
with the codes it listed. Under a weekly cadence most sessions are HOLD days, so
the roster an operator is nominally following is usually recorded on a DIFFERENT
day than the one on screen; without this the only way to find it is to open each
earlier date in turn and read its HOLD banner.

The search SHALL run backwards from the selected date, inclusive, so that
selecting a historical date answers the question as it stood on that date rather
than today.

An artifact SHALL qualify as that baseline only when it records the cadence
field explicitly AND that field says the session rebalanced. An artifact with no
cadence field SHALL NOT qualify: absence means the run predates cadence
semantics, and the HOLD reader deliberately reports such an artifact as "not a
hold" for backward compatibility — treating that as "did rebalance" invents
semantics for a run that recorded none.

An artifact SHALL also be disqualified when its schema version is unsupported,
when its filename date disagrees with its recorded as-of session, when a
schema-versioned artifact carries no metadata block, when its recorded entry
session is not strictly later than its as-of session, or when it cannot be read
at all. These are the same boundaries the page already applies to the artifact
an operator selects directly; a backward scan that applied fewer of them would
be a second, weaker validation path over the same files, and would present as a
trustworthy baseline an artifact the page itself refuses to render.

The scan SHALL apply the producer shape contract through the SAME implementation
the today-workbench summary applies, not through a parallel list of checks. That
contract covers both groups of fields the scan draws conclusions from: the
candidate list (per-row key and type contract, no duplicate codes, contiguous
ranks, descending scores, a count within `meta.topk`) and the cadence record
(both cadence fields written together or neither, a rebalance day's next date
equal to its own as-of session, and a hold's next date a strict ISO weekday no
earlier than its entry session). Enumerating these separately in each reader is
how the weaker path returns: a review that names two missing checks leaves the
rest of the same class in place. The shape contract SHALL NOT include the
provenance verdict, which asks whether an artifact came from the CURRENT
incumbent — an older rebalance was legitimately produced by an earlier model,
and rejecting it on that ground would be wrong.

**Only a validated hold SHALL license scanning further back.** A disqualified
artifact SHALL stop the search and be reported as such, because it may itself
record a rebalance that supersedes any older roster — scanning past it can
present a roster that has already been replaced as the current one. A hold is
the only evidence that a session did not trade and therefore that an older
roster still stands. An artifact that records no cadence field SHALL likewise
stop the search: it is neither a proven rebalance nor a proven hold.

#### Scenario: the baseline is an earlier rebalance day

- **GIVEN** the selected date's artifact records a hold, and an earlier artifact
  records a rebalance
- **WHEN** the page renders
- **THEN** it names that earlier date as the baseline and lists its codes

#### Scenario: an artifact predating cadence semantics is never the baseline

- **GIVEN** the only earlier artifact records no cadence field
- **WHEN** the page renders
- **THEN** it does not treat that artifact as a rebalance day

#### Scenario: an incomplete cadence pair stops the scan

- **GIVEN** an artifact records `rebalance_day` without `next_rebalance_date`
- **WHEN** the page renders
- **THEN** the scan stops there and reports the baseline as unknowable, rather
  than reading the missing field as an unrecorded next date

#### Scenario: a roster with a repeated code is not a position count

- **GIVEN** an artifact whose candidate list names the same code twice
- **WHEN** the page renders
- **THEN** the scan refuses that artifact as a baseline, and the roster reader
  raises rather than reporting the row count as the number of positions

### Requirement: A search that finds nothing SHALL say what it skipped and why

Where no qualifying artifact is found, the page SHALL state that no trustworthy
baseline exists, SHALL report how many artifacts it examined, and SHALL list
each skipped artifact with its own reason. Reporting a bare "unavailable" is
FORBIDDEN: "the baseline is thirty days old", "every session since was a hold",
and "the earlier artifacts are corrupt" call for different operator actions.

The page SHALL distinguish "no baseline found" from "no position": the absence
describes what THIS MACHINE'S artifacts can establish, not what the operator
holds.

The backward search SHALL be bounded, and SHALL disclose when it stopped at that
bound rather than at the end of the index. An unbounded walk back would both
grow without limit and let "the baseline expired long ago" be reported as
"found".

#### Scenario: an unreadable newer artifact makes the baseline unknown

- **GIVEN** a newer artifact that cannot be read, and an older artifact that
  records a rebalance
- **WHEN** the page renders
- **THEN** it reports the baseline as unknown and names the unreadable artifact,
  rather than presenting the older roster as current

#### Scenario: a corrupt roster is not shown as an empty position

- **GIVEN** a qualifying baseline artifact whose candidate list is malformed
- **WHEN** the page renders
- **THEN** it refuses that baseline, rather than showing it with an empty roster

#### Scenario: nothing qualifies and each rejection is named

- **GIVEN** an index whose artifacts are a hold day and an unsupported-schema
  artifact
- **WHEN** the page renders
- **THEN** it reports no trustworthy baseline, and names the hold and the
  unsupported schema separately

#### Scenario: the search stops at its bound and says so

- **GIVEN** more consecutive non-qualifying artifacts than the scan bound
- **WHEN** the page renders
- **THEN** it reports that it stopped at the bound, not that the index was
  exhausted

### Requirement: The nominal roster SHALL remain a read-only comparison

The roster SHALL be a set of codes and nothing more. The artifact records rank,
score, and a tradability flag — no weight, no share count, no amount — so
deriving any quantity, including an implied equal weighting, would state a
position the artifact never recorded.

The page SHALL NOT accept operator-entered holdings, SHALL NOT produce a
difference list against any holdings, SHALL NOT apply a no-trade band, and SHALL
NOT offer the roster for download or clipboard copy. It SHALL state that it does
not know the operator's actual holdings.

These prohibitions SHALL be enforced by tests against the page source rather
than by documentation. The check on execution vocabulary SHALL examine only the
strings the page actually renders, because the page's existing disclaimers
legitimately negate those same words, and a whole-file ban would push authors
toward weaker disclaimers.

#### Scenario: the roster carries no quantity

- **GIVEN** a baseline artifact
- **WHEN** its roster is rendered
- **THEN** it shows codes only, with no weight, share count, or amount

#### Scenario: the page offers no handoff to execution

- **GIVEN** the page source
- **WHEN** the boundary tests run
- **THEN** they fail if it gains a holdings input, a download, or a clipboard
  copy of the roster

### Requirement: A validated hold SHALL license only the day it covers

A validated hold SHALL license the scan to cross only the session it covers.
It proves that ONE session did not trade, and proves nothing about sessions
that produced no artifact at all — the artifact list enumerates only files that
exist.

Before the scan crosses from one artifact to the next older one, the interval
between them SHALL be shown to contain no unaccounted trading session.
Otherwise the scan SHALL stop and report the baseline as unknowable. A run that
left no artifact may have been a rebalance, and reporting the older roster as
current would present a list that has already been superseded.

The interval between the selected date and the newest artifact examined SHALL
be checked the same way; it is the same interval question, not a separate one.

Absent a trading calendar, the reader SHALL treat as proven-empty only those
intervals whose every day falls on a weekend. A market holiday is therefore
reported as unknowable. This is the honest degradation: the alternative is
presenting a possibly-superseded roster as the current one, which is precisely
what this view exists to prevent.

Where the scan stops on a gap, the explanation SHALL NOT state that the artifact
it stopped at could not answer whether it was a rebalance — that artifact is a
validated hold, and the doubt lies in the interval beyond it. One sentence
covering both causes is false for one of them.

#### Scenario: a missing weekday between two artifacts stops the scan

- **GIVEN** a validated hold, no artifact for the preceding weekday, and an
  older rebalance artifact
- **WHEN** the page renders
- **THEN** it reports the baseline as unknowable and names the unaccounted
  sessions, rather than presenting the older roster

#### Scenario: a weekend-only gap is crossed

- **GIVEN** a validated hold on a Monday and an artifact on the preceding Friday
- **WHEN** the page renders
- **THEN** the scan continues, because no trading session lies between them

### Requirement: The candidate count SHALL be checked against the producer's identity

The reader SHALL enforce `len(picks) == min(n_scored, meta.topk)`, and SHALL
first validate that `n_scored`, `n_masked`, and `n_st_excluded` are each a
non-negative integer.

The producer emits the candidate list as the tradable pool sorted by score and
truncated to `topk`, and `n_scored` IS that pool's size, so the equality holds
by construction. Enforcing only the upper half (`len(picks) <= topk`) admits a
TRUNCATED or EMPTIED list: delete rows from a valid artifact and the ranks stay
contiguous, the scores stay descending, the codes stay unique and the row dates
still match — every other gate passes, while the headline reports the shortened
list as today's rebalance. Emptied entirely, it reports "no action needed" on a
session that really did rebalance.

The three counts SHALL be validated before the equality is evaluated: the
equality reads one of them, so an unvalidated count lets a string or a negative
number walk through the new gate. They are also what the detail page prints as
"how much of the universe was dropped", so a negative value would be handed to
the operator as a statistic.

A candidate code carrying leading or trailing whitespace SHALL be refused, not
normalised. The duplicate-code gate compares bytes while the human-review helper
on the same page compares stripped codes; admitting a padded spelling lets two
rows naming the SAME stock pass as two candidates, so one artifact yields two
contradictory conclusions on one page. Normalising a spelling the producer
cannot emit launders it into a legitimate value.

Where the list exceeds `topk`, the reported cause SHALL name that bound rather
than the general count identity: "the configured topk is smaller than the list"
and "the list was truncated or altered" send the operator to different places.

#### Scenario: a truncated candidate list is refused

- **GIVEN** an artifact whose `n_scored` exceeds `topk` but whose `picks` hold
  fewer than `topk` rows
- **WHEN** the page validates it
- **THEN** it is refused as needing verification

#### Scenario: a pool smaller than topk is legitimate

- **GIVEN** an artifact whose `n_scored` is below `topk` and whose `picks` hold
  exactly `n_scored` rows
- **WHEN** the page validates it
- **THEN** it is accepted

#### Scenario: two rows differing only in padding are not two candidates

- **GIVEN** an artifact whose candidate list names the same code twice, once
  with leading whitespace
- **WHEN** the page validates it
- **THEN** it is refused, rather than counted as two stocks

### Requirement: Both artifact dates SHALL be provably-possible trading sessions

The reader SHALL refuse an artifact whose `as_of_date` or `entry_date` falls on
a weekend, applying the same provable half it already applies to a hold's
`next_rebalance_date` and to gaps in the artifact history.

The upstream entry-timing check verifies only strict ISO formatting and that the
entry is later than the as-of session. A Friday as-of paired with a Saturday
entry therefore passes, and the scan presents it as a trustworthy rebalance
baseline — although the producer's as-of is a real session and its entry is the
next session on the trading calendar, so neither can be a weekend. Applying the
weekday rule to one of an artifact's three date groups and not the others makes
the loosest one the hole.

#### Scenario: a weekend entry date is refused

- **GIVEN** an artifact whose as-of is a Friday and whose entry is the following
  Saturday
- **WHEN** the page validates it
- **THEN** it is refused, and never becomes the nominal baseline

### Requirement: Reaching the scan bound SHALL NOT be reported as exhaustion

Where the backward scan stops because it reached its bound, the page SHALL say
so in its own words, distinct from the message for an index that truly ran out.

Older artifacts still exist in the bounded case; they simply were not read.
Reporting it as "the scan reached the end, so the last rebalance predates every
artifact here" states something the scan did not establish, and directly
contradicts the bound notice rendered below it.

#### Scenario: the bound is reached and named as such

- **GIVEN** more consecutive validated holds than the scan bound
- **WHEN** the page renders
- **THEN** it says the scan stopped at its bound and that older artifacts were
  not read, rather than that the history was exhausted

### Requirement: The universe metadata SHALL be validated, not defaulted for display

The reader SHALL refuse an artifact whose `meta.instruments` is missing or not a
non-empty string, on the same footing as `meta.topk`.

The producer writes both in one unconditional block, so either being absent
means the file is corrupt rather than terse. Rendering the baseline card with
`universe=—` presents a corrupt artifact as a trustworthy baseline, and that dash
reads as "this run did not record a universe" rather than "this file is broken".

#### Scenario: an artifact without universe metadata is not a baseline

- **GIVEN** a schema-v2 artifact whose `meta` block omits `instruments`
- **WHEN** the page validates it
- **THEN** it is refused, rather than shown with a placeholder universe
