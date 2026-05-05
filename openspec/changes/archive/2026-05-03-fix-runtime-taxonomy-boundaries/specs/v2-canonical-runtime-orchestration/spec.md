## ADDED Requirements

### Requirement: Canonical runtime orchestration SHALL keep risk constraints out of the canonical core path

Risk-constraint behavior SHALL NOT be executable through the canonical
`src.core` runtime layer when it modifies predictions after signal generation
and before backtest execution, unless a dedicated approved runtime
specification defines those trading semantics.

#### Scenario: canonical core risk-constraint path is called
- **WHEN** a caller imports `src.core.risk_constraints` and tries to apply risk
  constraints
- **THEN** the call fails closed with a typed risk-constraint error
- **AND** the error explains that executable behavior is experimental
- **AND** no predictions are filtered or reweighted through the canonical core
  path

#### Scenario: experimental risk constraints are inspected
- **WHEN** maintainers inspect executable risk-constraint behavior
- **THEN** the implementation is located under an explicitly experimental
  namespace
- **AND** it is not treated as an official canonical metrics path

### Requirement: Pipeline attribution SHALL use an explicit validated taxonomy artifact when configured

Pipeline attribution SHALL only replace the board heuristic with a real
industry taxonomy when the operator supplies an explicit static taxonomy CSV,
manifest, and taxonomy id. The artifact SHALL be loaded through the taxonomy
artifact loader and validated through the taxonomy data contract before its map
is passed to attribution.

#### Scenario: no Pipeline attribution taxonomy is configured
- **WHEN** `Pipeline.run()` builds `AttributionConfig` without taxonomy artifact
  settings
- **THEN** attribution uses its default board heuristic
- **AND** the attribution result remains labeled with the board-heuristic
  taxonomy id

#### Scenario: valid Pipeline attribution taxonomy is configured
- **WHEN** `PipelineConfig` includes a static taxonomy artifact path, manifest
  path, and taxonomy id
- **THEN** Pipeline loads the artifact profile through `TaxonomyArtifactLoader`
- **AND** Pipeline validates the profile through `TaxonomyDataContract`
- **AND** Pipeline reads the CSV mapping only after validation has no errors
- **AND** `AttributionConfig.industry_map_override` and
  `AttributionConfig.industry_taxonomy_id` are set together

#### Scenario: incomplete Pipeline attribution taxonomy settings are provided
- **WHEN** only some of artifact path, manifest path, and taxonomy id are set
- **THEN** Pipeline config construction raises a typed `PipelineError`
- **AND** no implicit board/taxonomy mixing is performed

#### Scenario: invalid Pipeline attribution taxonomy artifact is configured
- **WHEN** the configured taxonomy artifact or manifest fails contract
  validation
- **THEN** Pipeline raises a typed `PipelineError`
- **AND** it does not silently fall back to the board heuristic

#### Scenario: unsupported Pipeline attribution taxonomy mode is configured
- **WHEN** `PipelineConfig.industry_temporal_mode` is not `static`
- **THEN** Pipeline raises a typed `PipelineError`
- **AND** no runtime attribution map is built from an unsupported temporal mode
