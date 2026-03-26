## ADDED Requirements

### Requirement: V2 SHALL provide a boundary-first project skeleton

The repository SHALL include a minimal directory skeleton that separates production runtime, contract, test, and research layers before trading implementation begins.

#### Scenario: required skeleton directories exist
- **WHEN** maintainers inspect the V2 repository
- **THEN** the repository contains skeleton directories for `app/` or `web/`, `src/core/`, `src/data/`, `src/contracts/`, `tests/`, `docs/`, and `research/factor_lab/`
- **AND** these directories are present as intentionally minimal placeholders

### Requirement: V2 skeleton SHALL document layer boundaries explicitly

Boundary documentation SHALL describe ownership and separation of production runtime, contract validation, and research-only artifacts.

#### Scenario: boundary documentation is present
- **WHEN** contributors read architecture/layer notes
- **THEN** documentation states the responsibility of each layer
- **AND** documentation preserves governance baseline that official metrics are canonical-path-only
- **AND** documentation states that experimental logic SHALL NOT be silently promoted into production flow

### Requirement: Research factor_lab SHALL remain non-production by contract

The `research/factor_lab/` skeleton SHALL be explicitly marked research-only and SHALL NOT be treated as production runtime or canonical configuration input.

#### Scenario: research boundary is declared
- **WHEN** contributors inspect `research/factor_lab/` placeholder documentation
- **THEN** it states research artifacts are non-production and non-canonical by default
- **AND** it states no runtime or factor-generation logic is introduced in this skeleton change

### Requirement: V2 skeleton SHALL include minimal future-facing test structure

The skeleton SHALL include minimal test placeholders indicating separation between logic tests and governance/contract regression tests.

#### Scenario: tests skeleton is present
- **WHEN** contributors inspect `tests/` skeleton files
- **THEN** they can identify where runtime logic tests will live
- **AND** they can identify where governance/contract boundary regression tests will live

### Requirement: Skeleton change SHALL remain foundation-only

This change SHALL NOT introduce runtime trading behavior, including training flow, backtest execution, benchmark selection, or strategy-rule implementation.

#### Scenario: skeleton implementation review
- **WHEN** reviewers inspect files introduced by this change
- **THEN** no executable trading pipeline behavior is added
- **AND** no trading semantics are changed
