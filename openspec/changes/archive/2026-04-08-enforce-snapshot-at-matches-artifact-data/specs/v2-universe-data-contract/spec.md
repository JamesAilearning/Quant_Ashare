## ADDED Requirements

### Requirement: Universe contract SHALL detect snapshot_at vs artifact data mismatch

The universe data contract SHALL surface a `temporal_leakage` error whenever the
manifest-declared `snapshot_at` does not equal the actual maximum effective date
in the universe artifact, for `trade_date` and `range` temporal modes. The
`static` temporal mode is exempt because the artifact carries no date column and
`snapshot_at` is the sole metadata witness.

#### Scenario: trade_date mode with snapshot_at newer than artifact max date
- **WHEN** a `UniverseArtifactProfile` is supplied with `has_snapshot_at_mismatch=True`
- **AND** the request `temporal_mode` is `trade_date`
- **THEN** `UniverseDataContract.validate_and_build_status` returns `contract_health="error"`
- **AND** the `errors` tuple contains `temporal_leakage`

#### Scenario: range mode with snapshot_at older than artifact max date
- **WHEN** a `UniverseArtifactProfile` is supplied with `has_snapshot_at_mismatch=True`
- **AND** the request `temporal_mode` is `range`
- **THEN** `UniverseDataContract.validate_and_build_status` returns `contract_health="error"`
- **AND** the `errors` tuple contains `temporal_leakage`

#### Scenario: static mode is exempt from snapshot_at-vs-data check
- **WHEN** a `UniverseArtifactProfile` is supplied with `has_snapshot_at_mismatch=False`
- **AND** the request `temporal_mode` is `static`
- **THEN** the contract does not raise a snapshot_at-mismatch error
