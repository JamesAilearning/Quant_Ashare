# Mined Factors — output layout

This directory holds the artefacts produced by the factor-mining
subsystem (Phases 3-6). The contents under `runs/`, `candidates/`,
and `production/` are **not checked into git** (see `.gitignore`);
this README and the `.gitignore` are the only tracked files.

## Layout (`decisions.md` D4)

```
research/mined_factors/
├── README.md                   # this file (tracked)
├── .gitignore                  # untracks runs/, candidates/, production/
├── runs/{run_id}/              # auto-saved by the miner, every GP run
│   ├── factor_pool.parquet
│   ├── factor_expressions.json
│   ├── gp_history.json
│   └── config.yaml
├── candidates/{date}/          # researcher copies promising factors here
│   └── {factor_id}.json
└── production/{version}/       # promoted runs the training pipeline reads
    ├── factor_pool.parquet
    └── factor_expressions.json
```

## How to bind a pool into a training run

```python
from pathlib import Path

from src.data.mined_factor_handler import (
    MinedFactorBundle,
    register_mined_factor_handler,
)

register_mined_factor_handler(
    MinedFactorBundle(
        pool_dir=Path("research/mined_factors/production/v1"),
        pit_provider_uri="D:/qlib_data/my_cn_data_pit",
        delisted_registry_path="D:/qlib_data/my_cn_data_pit_delisted.parquet",
    ),
)

# Now any PipelineConfig with feature_handler="MinedFactor" dispatches
# to this bound factory. The factory:
#   1. Loads factor_pool.parquet + factor_expressions.json from pool_dir
#   2. Evaluates each expression against the PIT-loaded OHLCV panel
#   3. Wraps the materialised features in a qlib DataHandlerLP
```

The bind step is **explicit** — importing `src.data.mined_factor_handler`
does NOT register a default MinedFactor handler. This keeps the qlib
import lazy and lets each application choose its own pool.

## Promotion workflow (`decisions.md` D4)

v1 promotion is **manual gated**. The researcher reviews each GP run
under `runs/{run_id}/` and copies the chosen factors into
`candidates/{date}/`. After OOS validation (Phase 6 validator), the
researcher promotes a validated set into `production/{version}/`.

Promotion criteria (configurable per `decisions.md` D4):

- OOS IR > 0.3
- OOS RankIC mean > 0.02
- Max correlation with existing production factors < 0.6
- Stability: rolling 6-month IR > 0.2 in ≥ 70 % of windows

Phase 6 (`add-factor-mining-validation`) ships the `validator.py` and
`promote.py` CLIs that automate the IS / OOS metric calculation,
but the final promotion decision remains a researcher's button-press.

## Phase 5 → Phase 6 gate

The handler in this directory is verified by synthetic-data unit
tests in `tests/logic/test_mined_factor_handler.py`. The real
end-to-end pipeline acceptance ("Sharpe whatever it is, but the
backtest runs") requires:

1. The PIT bundle on disk (see `inventory.md` §F.3 for build
   instructions).
2. A Phase 3 miner run producing a `runs/{run_id}/` directory here.
3. The application binding that run via
   `register_mined_factor_handler(...)`.
4. A training-pipeline run with `feature_handler: "MinedFactor"`.

That sequence is the operator gate before Phase 6 work starts.
