# v2-feature-handler-registry Specification

## Purpose
TBD - created by archiving change add-feature-handler-registry. Update Purpose after archive.
## Requirements
### Requirement: Feature dataset builder SHALL use a handler registry

The feature dataset builder SHALL resolve `feature_handler` through a registry
of explicitly registered handler factories instead of hard-coded if/else
construction.

#### Scenario: default Alpha158 handler is requested
- **WHEN** `FeatureDatasetConfig.feature_handler` is `Alpha158`
- **THEN** the builder constructs the registered Alpha158 qlib handler
- **AND** existing Alpha158 configs remain compatible

#### Scenario: custom handler is registered
- **WHEN** a caller registers a custom handler name and factory
- **THEN** `FeatureDatasetBuilder` can build a dataset using that registered handler name

#### Scenario: unknown handler is requested
- **WHEN** `FeatureDatasetConfig.feature_handler` is not registered
- **THEN** validation raises `FeatureDatasetError`
- **AND** the error message lists registered handler names

### Requirement: Feature handler registration SHALL remain explicit

The system SHALL NOT import arbitrary handler classes from user config strings.
Only factories registered through the registry boundary SHALL be accepted.

#### Scenario: dotted import path is supplied as handler name
- **WHEN** a caller supplies an unregistered dotted import path as `feature_handler`
- **THEN** validation raises `FeatureDatasetError`
- **AND** no dynamic import is attempted

