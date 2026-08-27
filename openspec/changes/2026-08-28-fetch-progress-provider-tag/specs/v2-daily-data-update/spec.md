# Delta for v2-daily-data-update

## ADDED Requirements

### Requirement: Fetch progress lines SHALL carry the provider they belong to

A fetch progress line SHALL identify the provider that wrote it, because
sibling bundles share one update log while single-flight is per-provider — two
providers can run at once and their lines interleave, leaving a reader unable
to tell whose progress it is reading.

The stamp SHALL be the same normalised provider path the run-boundary marker
carries. Deriving it separately is FORBIDDEN: the reader validates the stamp by
full round trip, so an identity that differs by one byte is read as somebody
else's, and the only symptom is attribution silently going back to "unknown".

The stamp SHALL be appended at the END of the line, after the existing fields,
so a reader written before this change still parses the line.

An unstamped line SHALL remain valid. A fetch that has no provider identity
configured — a hand-run fetch — SHALL write no stamp at all rather than an
empty one: a reader must be able to tell "this run reported no identity" from
"it reported an empty identity", and only the former may fall back to boundary
attribution.

An identity containing a line terminator SHALL NOT be stamped. A line-oriented
log splits such a line in two, truncating the identity into a different but
entirely well-formed one; refusing to stamp degrades to boundary attribution,
while stamping produces a line that reads as another provider's.

#### Scenario: an orchestrated fetch stamps the run's provider

- **GIVEN** the orchestrator runs a fetch for a provider directory
- **WHEN** a progress line is written
- **THEN** it ends with that provider's normalised path, byte-identical to the
  one in this run's boundary marker

#### Scenario: a hand-run fetch stamps nothing

- **GIVEN** a fetch invoked without a provider identity
- **WHEN** a progress line is written
- **THEN** the line carries no provider field at all
