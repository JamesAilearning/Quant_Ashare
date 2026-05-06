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

Tushare OHLCV training data is opt-in. The shipped publisher builds a
separate qlib provider bundle; it does not change `config.yaml` or the
canonical training source by default.

```powershell
$env:TUSHARE_TOKEN = "your_pro_token_here"
python -m pip install -e ".[tushare]"
python scripts/ingest_tushare_qlib_provider.py config_tushare_qlib_provider.yaml
```

To train on the generated bundle, copy your strategy config and explicitly set
`provider_uri` to the generated `output/qlib_tushare` directory. Keep
`adjust_mode` aligned with the ingest config's `data_adjust_mode`. The first
publisher writes `instruments/all.txt`; set `instruments: "all"` in the
training config unless you separately publish an index-specific instrument
file such as `csi300.txt`.
