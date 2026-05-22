## MODIFIED Requirements

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
