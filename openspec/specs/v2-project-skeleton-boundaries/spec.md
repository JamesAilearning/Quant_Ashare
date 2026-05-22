# v2-project-skeleton-boundaries Specification

## Purpose
TBD - created by archiving change create-v2-project-skeleton. Update Purpose after archive.
## Requirements
### Requirement: V2 SHALL provide a boundary-first project skeleton

The repository SHALL include a directory skeleton that separates production runtime (`src/`), contract validation (`src/contracts/`), test (`tests/`), and research (`research/`) layers before runtime implementation is exposed to consumers. The skeleton MAY contain both intentionally minimal placeholders (e.g. `research/factor_lab/`, `app/` or `web/` while their runtime contracts are still emerging) and production-layer subpackages with active runtime contracts (e.g. `src/factor_mining/`, governed by `v2-factor-mining-foundations` and registered into training pipelines via `v2-feature-handler-registry`). Production-layer subpackages SHALL NOT import from research placeholders, and research placeholders SHALL remain non-canonical per the existing "Research factor_lab SHALL remain non-production by contract" requirement.

#### Scenario: required skeleton directories exist
- **WHEN** maintainers inspect the V2 repository
- **THEN** the repository contains directories for `app/` or `web/`, `src/core/`, `src/data/`, `src/contracts/`, `src/factor_mining/`, `tests/`, `docs/`, and `research/factor_lab/`
- **AND** placeholder directories (`research/factor_lab/`, and `app/` or `web/` where runtime contracts have not yet landed) remain intentionally minimal

#### Scenario: src/factor_mining/ is recognised as a production-layer subpackage
- **WHEN** maintainers inspect `src/factor_mining/`
- **THEN** the subpackage contains production-runtime modules (operators, expression tree, grammar) governed by `v2-factor-mining-foundations`
- **AND** the subpackage is distinct from `research/factor_lab/`, which continues to be a research-only placeholder per the unchanged "Research factor_lab SHALL remain non-production by contract" requirement
- **AND** code under `src/factor_mining/` SHALL NOT import from `research/factor_lab/` (research is non-canonical)

#### Scenario: a contributor places production factor code under research/factor_lab/
- **WHEN** a contributor introduces operator, expression, or grammar code under `research/factor_lab/`
- **THEN** the change is rejected at review
- **AND** the reviewer directs the contributor to `src/factor_mining/` per Phase 0 outcome O1 in `docs/factor_mining/decisions.md`

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

