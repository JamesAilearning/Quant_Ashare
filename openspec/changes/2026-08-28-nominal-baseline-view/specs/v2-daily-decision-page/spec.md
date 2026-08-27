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
