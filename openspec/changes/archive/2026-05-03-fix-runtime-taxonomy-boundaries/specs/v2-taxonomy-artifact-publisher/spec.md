## ADDED Requirements

### Requirement: Taxonomy publisher SHALL reject duplicate static instruments before IO

For `temporal_mode="static"`, the taxonomy publisher SHALL require at most one
row per instrument. Duplicate instruments would be overwritten or rejected by
runtime map consumers, so they SHALL be treated as publish-time errors before
any artifact or manifest is written.

#### Scenario: duplicate static instrument is rejected
- **WHEN** the publisher is called with `temporal_mode="static"` and two rows
  for the same instrument
- **THEN** `TaxonomyArtifactPublisherError` is raised
- **AND** the error message names the duplicate instrument
- **AND** neither the csv nor the manifest file exists on disk

#### Scenario: repeated instruments remain valid in temporal modes
- **WHEN** the publisher is called in `trade_date` or `range` mode with the
  same instrument appearing in multiple time-scoped rows
- **THEN** duplicate-instrument validation for static mode does not reject the
  rows solely because the instrument repeats
