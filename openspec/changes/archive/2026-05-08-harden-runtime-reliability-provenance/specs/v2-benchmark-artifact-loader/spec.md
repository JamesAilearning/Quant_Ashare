## ADDED Requirements

### Requirement: Benchmark artifact CSV parsing SHALL preserve original column positions

Benchmark artifact loaders SHALL normalize header names for matching while
preserving their original CSV column indexes when reading row values.

#### Scenario: CSV contains an unnamed column
- **WHEN** a benchmark CSV header is `date,,close`
- **THEN** the loader maps `close` to the original third column
- **AND** blank unnamed columns do not shift row-value reads into the wrong
  field
