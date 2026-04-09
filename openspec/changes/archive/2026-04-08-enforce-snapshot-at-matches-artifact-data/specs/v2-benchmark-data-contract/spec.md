## ADDED Requirements

### Requirement: Benchmark contract SHALL detect snapshot_at vs artifact data mismatch

The benchmark data contract SHALL surface a `temporal_issue` error whenever the
manifest-declared `snapshot_at` does not equal the actual maximum row date in the
benchmark artifact. This protects downstream consumers from silently trusting a
manifest that lies about the freshness of the underlying data, in either direction
(claiming newer than reality, or claiming older than reality).

#### Scenario: manifest snapshot_at is newer than artifact max row date
- **WHEN** a `BenchmarkArtifactProfile` is supplied with `has_snapshot_at_mismatch=True`
- **THEN** `BenchmarkDataContract.validate_and_build_status` returns `contract_health="error"`
- **AND** the `errors` tuple contains `temporal_issue`

#### Scenario: manifest snapshot_at is older than artifact max row date
- **WHEN** a `BenchmarkArtifactProfile` is supplied with `has_snapshot_at_mismatch=True`
- **THEN** `BenchmarkDataContract.validate_and_build_status` returns `contract_health="error"`
- **AND** the `errors` tuple contains `temporal_issue`

#### Scenario: manifest snapshot_at exactly equals artifact max row date
- **WHEN** a `BenchmarkArtifactProfile` is supplied with `has_snapshot_at_mismatch=False`
- **AND** all other validations pass
- **THEN** `BenchmarkDataContract.validate_and_build_status` returns `contract_health="ok"`
