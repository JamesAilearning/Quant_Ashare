# qlib_trading_system_v2

This is a clean-slate V2 quantitative trading system built with Qlib and managed with OpenSpec.
V1 is used as a source of lessons and migration principles, not as an implementation template.

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

## Tushare Market Data

A-share OHLCV training data is built into a survivorship-corrected,
point-in-time qlib bundle by the data-pipeline scripts under
`scripts/data_pipeline/` (`01_fetch_tushare` → `05_build_qlib_bins` →
`06_validate_pit_data`). See the
[PIT migration guide](docs/pit/migration_guide.md) for the full chain and the
exact arguments.

```powershell
$env:TUSHARE_TOKEN = "your_pro_token_here"
python -m pip install -e ".[tushare]"
# Then run scripts/data_pipeline/ 01 → 06 (see the migration guide above).
```

Point `provider_uri` at the built bundle (e.g. `D:/qlib_data/my_cn_data_pit`);
`QUANT_PROVIDER_URI` is its env default (ops Phase 1), so the shipped configs'
`${QUANT_PROVIDER_URI:-…}` resolves to it automatically.

> The earlier operator-UI "Tushare 数据" ingest page and its standalone
> publisher were retired (unify U3); the `scripts/data_pipeline/` chain is the
> single production bundle builder.
