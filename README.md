# qlib_trading_system_v2

This is a clean-slate V2 quantitative trading system built with Qlib and managed with OpenSpec.

## Principles

- Spec-first development: every non-trivial change starts with OpenSpec proposal/design/tasks/spec.
- Canonical metrics first: official performance metrics must come from one auditable canonical path.
- Data-contract first: benchmark, universe, and taxonomy inputs must have explicit validation contracts.
- Minimal drift: no silent behavior changes for published metrics.

## Development Workflow

1. Propose change: `/opsx:propose <change>`
2. Review scope and acceptance criteria.
3. Apply minimal implementation: `/opsx:apply`
4. Validate:
   - targeted tests
   - full tests when needed
   - `openspec validate --specs --strict`
5. Archive only after validation: `/opsx:archive`

## Baseline Docs

- [Architecture Overview](docs/architecture-overview.md)
- [Current State Summary](docs/current-state-summary.md)
- [Improvement Roadmap](docs/improvement-roadmap.md)
- [V1 Lessons](docs/v1-lessons.md)
