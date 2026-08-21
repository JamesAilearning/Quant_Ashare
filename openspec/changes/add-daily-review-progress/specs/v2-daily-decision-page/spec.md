## MODIFIED Requirements

### Requirement: Display current-candidate human review progress

For a valid, non-HOLD daily signal, the daily decision page SHALL display the
selected trade date's candidate total, reviewed and unreviewed counts,
adopt/reject/watch counts, and most recent effective review time when one
exists.  It SHALL show each current candidate's effective human-review label
and concise latest reason when present.  These labels SHALL be presented as
human review only, not as orders, positions, or execution state.

#### Scenario: Partially reviewed candidates

- **WHEN** a valid selected artifact has five candidate codes and the journal
  has effective records for two of those codes on that exact trade date
- **THEN** the page shows two reviewed and three unreviewed candidates
- **AND** the two candidate rows show their current human-review labels

#### Scenario: A candidate was corrected

- **WHEN** a candidate has multiple valid journal records for the selected
  trade date
- **THEN** the page uses the current entry from the journal effective view
- **AND** the historical append-only records remain available in the audit
  table

#### Scenario: Records do not match the current candidate set

- **WHEN** the journal contains records for another date or for codes absent
  from the selected artifact
- **THEN** those records do not increase the selected candidate review count

### Requirement: Preserve conservative review boundaries

The page SHALL preserve existing HOLD and artifact-validation protections.
It SHALL NOT show a completion summary for HOLD or unverifiable artifacts.
Malformed journal rows SHALL NOT count as reviewed and SHALL remain visibly
warned.  The review projection SHALL NOT be imported by runtime, backtest,
training, serving, or trading-execution code.

#### Scenario: A malformed journal row coexists with valid records

- **WHEN** the journal reader reports malformed rows
- **THEN** the page retains a visible verification warning
- **AND** any progress count includes only valid effective records

#### Scenario: A journal row has no valid human reason

- **WHEN** a persisted journal row has an empty, whitespace-only, or non-string
  reason
- **THEN** the journal reader counts it as malformed before building its
  effective view
- **AND** valid rows continue to appear in the audit and review-progress view

#### Scenario: Current candidate identifiers are ambiguous

- **WHEN** an otherwise readable selected artifact has a missing or duplicate
  candidate code
- **THEN** the page does not render its decision form, review-completion
  projection, or candidate review labels
- **AND** it continues to render the separate append-only journal audit and
  any malformed-row warning from a readable journal
