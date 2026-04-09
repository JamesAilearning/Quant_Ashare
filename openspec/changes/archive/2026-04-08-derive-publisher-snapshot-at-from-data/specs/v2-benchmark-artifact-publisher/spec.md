## ADDED Requirements

### Requirement: Publisher SHALL derive `snapshot_at` from the actual max row date

`BenchmarkArtifactPublisher.publish` SHALL set the manifest
`snapshot_at` field to the maximum row date present in the csv it just
wrote, NOT to the requested `end_time`. When the caller explicitly
supplies `snapshot_at`, the publisher SHALL validate strict equality
between the supplied value and the actual max row date and SHALL raise
`BenchmarkArtifactPublisherError` at the publisher boundary if they
differ.

#### Scenario: end_time falls on a non-trading day
- **WHEN** `BenchmarkArtifactPublisher.publish` is called with
  `end_time` set to a Saturday
- **AND** the qlib provider returns rows up to the preceding Friday
- **THEN** the manifest `snapshot_at` equals the Friday date
- **AND** the round-trip profile yields `contract_health == "ok"`

#### Scenario: caller passes a snapshot_at that mismatches actual data
- **WHEN** the caller supplies `snapshot_at="2026-02-25"`
- **AND** the actual maximum row date in the qlib data is `2026-02-27`
- **THEN** the publisher raises `BenchmarkArtifactPublisherError`
- **AND** the error message includes both `2026-02-25` and `2026-02-27`
- **AND** no csv or manifest file is left behind in a partially-written state

#### Scenario: caller passes a snapshot_at that matches actual data
- **WHEN** the caller supplies `snapshot_at="2026-02-27"` and the
  actual maximum row date is also `2026-02-27`
- **THEN** the publisher accepts the call and writes that value into
  the manifest unchanged
