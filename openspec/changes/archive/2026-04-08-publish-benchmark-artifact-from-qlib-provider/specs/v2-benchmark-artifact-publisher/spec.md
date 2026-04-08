## ADDED Requirements

### Requirement: V2 SHALL provide a benchmark artifact publisher backed by the pinned qlib provider

The system SHALL provide a publisher that produces benchmark artifacts in the canonical csv + manifest shape consumable by `BenchmarkDataContract` without modification, using the pinned qlib provider as the sole data source.

#### Scenario: canonical round trip from publisher to contract
- **WHEN** canonical qlib runtime has been initialized via the canonical entry point
- **AND** the publisher is invoked with an explicit `benchmark_code`, `start_time`, `end_time`, and target csv/manifest paths
- **THEN** a csv with header `date,close` and at least one data row is written
- **AND** a sidecar manifest json containing all required benchmark provenance fields is written
- **AND** the returned profile yields `contract_health == "ok"` when fed into `BenchmarkDataContract.validate_and_build_status`

### Requirement: Publisher SHALL require canonical qlib initialization

The publisher SHALL NOT call `qlib.init` directly. It SHALL verify that canonical qlib initialization has already happened via `src.core.qlib_runtime.init_qlib_canonical`, and SHALL raise a typed error otherwise.

#### Scenario: publisher is called without canonical init
- **WHEN** the publisher is invoked before `init_qlib_canonical` has been called
- **THEN** a `BenchmarkArtifactPublisherError` is raised
- **AND** no csv or manifest file is written

#### Scenario: canonical init boundary is scanned
- **WHEN** governance regression tests scan `src/data/`
- **THEN** no file under `src/data/` references `qlib.init(` directly
- **AND** the publisher imports `is_canonical_qlib_initialized` from `src.core.qlib_runtime`

### Requirement: Publisher SHALL refuse to emit empty artifacts

The publisher SHALL NOT write a csv with zero data rows. An empty qlib query result SHALL be treated as an explicit error, not as a silent healthy output.

#### Scenario: qlib provider returns empty result
- **WHEN** the publisher queries the qlib provider and receives zero rows
- **THEN** a `BenchmarkArtifactPublisherError` is raised with the offending inputs echoed back
- **AND** neither the csv nor the manifest file exists on disk after the failed call

### Requirement: Publisher and loader SHALL share one profile construction path

The publisher SHALL delegate construction of the returned `BenchmarkArtifactProfile` to the canonical `BenchmarkArtifactLoader`, so that there is exactly one code path that interprets csv + manifest into a contract-consumable profile.

#### Scenario: producer / consumer symmetry is audited
- **WHEN** maintainers review the publisher source
- **THEN** the publisher calls `BenchmarkArtifactLoader.load` on its own output and returns the result
- **AND** the publisher does not construct `BenchmarkArtifactProfile` directly
