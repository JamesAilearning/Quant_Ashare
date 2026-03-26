# v2-canonical-backtest-contract Specification

## Purpose
TBD - created by archiving change define-v2-canonical-backtest-contract. Update Purpose after archive.
## Requirements
### Requirement: V2 SHALL define exactly one canonical official-metrics backtest path

The system SHALL expose exactly one canonical backtest contract for official metrics, based on qlib-native execution semantics, and SHALL NOT define competing official paths.

#### Scenario: official metrics source is declared
- **WHEN** maintainers inspect canonical backtest contract documentation
- **THEN** exactly one official metrics source is defined
- **AND** the source is explicitly labeled canonical
- **AND** no alternative path is labeled official

### Requirement: Canonical backtest contract SHALL define accepted inputs explicitly

The canonical contract SHALL declare required and optional inputs, including required prediction input, evaluation window, account/exchange configuration, and optional benchmark reference, with explicit exclusion of non-canonical control inputs.

#### Scenario: canonical input schema is reviewed
- **WHEN** contributors review canonical input definitions
- **THEN** required canonical inputs are clearly listed
- **AND** optional canonical inputs are clearly listed
- **AND** unsupported experimental/research controls are explicitly out of scope

### Requirement: Canonical backtest contract SHALL define required outputs for official reporting

The canonical contract SHALL define required output fields for official reporting, including return series, risk-analysis payload, and provenance fields that identify canonical path usage.

#### Scenario: canonical output schema is reviewed
- **WHEN** contributors inspect canonical output definitions
- **THEN** required metric outputs are explicitly listed
- **AND** canonical provenance/status fields are explicitly listed
- **AND** output schema supports auditable official reporting

### Requirement: Canonical contract SHALL keep experimental execution non-official

Experimental execution paths SHALL remain explicitly non-canonical and SHALL NOT be mixed into official metric outputs.

#### Scenario: experimental logic is present in project
- **WHEN** an experimental backtest or risk-control path exists
- **THEN** it is labeled non-canonical
- **AND** official metrics remain sourced only from canonical outputs

### Requirement: Canonical contract SHALL keep research artifacts outside production execution

Research artifacts under `research/factor_lab/` SHALL be treated as non-production and SHALL NOT be consumed by canonical runtime unless promoted through explicit spec-approved changes.

#### Scenario: research boundary is checked
- **WHEN** contributors inspect canonical contract boundaries
- **THEN** research/factor_lab is marked non-production and non-canonical
- **AND** direct runtime coupling from research to canonical execution is disallowed by contract

### Requirement: Canonical contract SHALL forbid implicit fallback semantics

The canonical contract SHALL require explicit behavior for missing dependencies and SHALL NOT allow hidden fallback paths that change official metric meaning without explicit labeling.

#### Scenario: missing canonical dependency occurs
- **WHEN** a required canonical dependency is unavailable
- **THEN** contract behavior is explicitly defined
- **AND** no implicit hidden fallback changes official metric semantics

### Requirement: Canonical contract SHALL define minimum validation and regression expectations

The canonical contract SHALL require minimum validation coverage, including boundary regressions that protect canonical-vs-experimental separation and official-metrics source integrity.

#### Scenario: canonical contract validation baseline is reviewed
- **WHEN** maintainers inspect required validation expectations
- **THEN** minimum regression categories are explicitly defined
- **AND** canonical source integrity checks are part of required validation
- **AND** boundary regressions are required before archive/merge

