## ADDED Requirements

### Requirement: Publisher SHALL validate `start_time` / `end_time` as ISO dates with start <= end before calling qlib

`BenchmarkArtifactPublisher.publish` SHALL parse `start_time` and
`end_time` as strict ISO `YYYY-MM-DD` dates AFTER the existing
non-empty check and BEFORE any call to the qlib provider
(`D.features`). Parse failures SHALL raise
`BenchmarkArtifactPublisherError` with the offending field name and
the raw value. The publisher SHALL also verify that the parsed
`start_time` is less than or equal to the parsed `end_time` and
SHALL raise `BenchmarkArtifactPublisherError` otherwise. A
single-day window (`start_time == end_time`) SHALL be accepted.

#### Scenario: start_time is not a valid ISO date
- **WHEN** the publisher is called with `start_time="banana"`
- **THEN** `BenchmarkArtifactPublisherError` is raised
- **AND** the error message contains the field name `start_time` and
  the offending string `banana`
- **AND** neither the csv nor the manifest file is left behind on disk

#### Scenario: end_time uses a non-ISO separator
- **WHEN** the publisher is called with `end_time="2026/02/27"`
- **THEN** `BenchmarkArtifactPublisherError` is raised
- **AND** the error message contains the field name `end_time` and
  the offending string `2026/02/27`
- **AND** neither the csv nor the manifest file is left behind on disk

#### Scenario: start_time is after end_time
- **WHEN** the publisher is called with `start_time="2026-02-27"`
  and `end_time="2026-02-01"`
- **THEN** `BenchmarkArtifactPublisherError` is raised
- **AND** the error message contains both raw values
- **AND** neither the csv nor the manifest file is left behind on disk

#### Scenario: ISO validation runs before any qlib call
- **WHEN** the publisher is called with a malformed `start_time` and
  qlib has not been queried yet
- **THEN** the publisher raises `BenchmarkArtifactPublisherError`
  without ever invoking `qlib.data.D.features`
