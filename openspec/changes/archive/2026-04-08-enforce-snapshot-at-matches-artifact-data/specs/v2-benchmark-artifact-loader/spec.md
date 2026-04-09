## ADDED Requirements

### Requirement: Benchmark loader SHALL detect snapshot_at vs csv max-row-date mismatch

The benchmark artifact loader SHALL compute a strict equality comparison between
the manifest's `snapshot_at` field and the maximum row date observed in the csv,
and SHALL expose the result as `BenchmarkArtifactProfile.has_snapshot_at_mismatch`.
The comparison only fires when both values are present and parseable; missing
values are handled by independent schema/missing-file error codes.

#### Scenario: manifest snapshot_at strictly greater than csv max date
- **WHEN** a manifest declares `snapshot_at = 2026-02-27` and the csv's last row date is `2026-02-20`
- **THEN** `BenchmarkArtifactLoader.load` returns a profile with `has_snapshot_at_mismatch=True`

#### Scenario: manifest snapshot_at strictly less than csv max date
- **WHEN** a manifest declares `snapshot_at = 2026-02-20` and the csv's last row date is `2026-02-27`
- **THEN** `BenchmarkArtifactLoader.load` returns a profile with `has_snapshot_at_mismatch=True`

#### Scenario: manifest snapshot_at equals csv max date
- **WHEN** manifest `snapshot_at` exactly equals csv max row date
- **THEN** `BenchmarkArtifactLoader.load` returns a profile with `has_snapshot_at_mismatch=False`

#### Scenario: manifest is missing snapshot_at field
- **WHEN** the manifest does not contain `snapshot_at`
- **THEN** `has_snapshot_at_mismatch` remains `False`
- **AND** the missing field is reported by the contract via `schema_mismatch`, not by this check
