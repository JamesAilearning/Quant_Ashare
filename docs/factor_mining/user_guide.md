# Factor Mining — User Guide

A short user-facing quickstart for the factor-mining subsystem. For
the full design, see
[`factor_mining_claude_code_design.md`](factor_mining_claude_code_design.md);
for the normative type rules, see
[`scale_invariance.md`](scale_invariance.md); for locked decisions
(cost rate, feature universe, data gate, promotion workflow), see
[`decisions.md`](decisions.md).

## Quickstart (synthetic data — no PIT bundle required)

Three commands cover the full mine → validate → bind cycle:

```bash
# 1. Mine — runs the GP loop on a synthetic OHLCV panel.
python -m src.factor_mining.miner config/factor_mining/smoke.yaml

# 2. Promote — validates the run's factors and writes a production
#    version directory.
python -m src.factor_mining.promote \
    --run research/mined_factors/runs/<run_id> \
    --to v1 \
    --dry-run        # prints the report; drop --dry-run to actually copy
```

```python
# 3. Bind — make the promoted pool available to the training pipeline.
from pathlib import Path
from src.data.mined_factor_handler import (
    MinedFactorBundle, register_mined_factor_handler,
)

register_mined_factor_handler(MinedFactorBundle(
    pool_dir=Path("research/mined_factors/production/v1"),
))
# A PipelineConfig with feature_handler="MinedFactor" now resolves
# to the bound factory.
```

## Real-PIT path

The synthetic-data quickstart exercises the code paths but mines on
random walks. For real factor mining you need the PIT-corrected qlib
bundle built per [`inventory.md`](inventory.md) §F.3:

1. **Build the PIT bundle.** Run
   `src/data/pit/qlib_bin_builder.py` end-to-end against your Tushare
   daily + adj_factor + delisted-registry data. Record the output
   directory; this becomes `pit_provider_uri`.

2. **Fill in the default config.** Edit
   `config/factor_mining/default.yaml` and set
   `data.pit_provider_uri` and `data.delisted_registry_path` to the
   paths from step 1.

3. **Mine on real PIT data.** Use the default config (or your own
   variant) with `data.mode: pit`:

   ```bash
   python -m src.factor_mining.miner config/factor_mining/default.yaml
   ```

4. **Promote.** Same as the quickstart, but supply a config with
   `data.mode: pit`:

   ```bash
   python -m src.factor_mining.promote \
       --run research/mined_factors/runs/<run_id> \
       --to v1 \
       --config config/factor_mining/default.yaml
   ```

5. **Bind into a training pipeline.** Pass the PIT paths to the
   bundle so the handler can re-evaluate factors at pipeline build
   time:

   ```python
   register_mined_factor_handler(MinedFactorBundle(
       pool_dir=Path("research/mined_factors/production/v1"),
       pit_provider_uri="D:/qlib_data/my_cn_data_pit",
       delisted_registry_path="D:/qlib_data/my_cn_data_pit_delisted.parquet",
   ))
   ```

6. **Run the training pipeline** with
   `PipelineConfig(feature_handler="MinedFactor", ...)`. The
   pipeline produces a backtest like any other handler.

## What each artifact means

Under `research/mined_factors/runs/<run_id>/`:

- `factor_pool.parquet` — tabular: one row per pool entry, columns =
  metric scalars (`fitness`, `ic_mean`, `ir`, `rank_ic_mean`,
  `turnover_daily`, `coverage`, `expr_size`, `expr_hash`). Fast to
  query with pandas / pyarrow.
- `factor_expressions.json` — `expr_hash` → AST dict mapping for
  every entry. Reconstruct with `Expression.from_dict`.
- `gp_history.json` — list of per-generation `GenerationStats`
  (best/mean/median fitness, n_unique, n_invalid).
- `config.yaml` — the resolved config the miner ran with
  (reproducibility).

After promotion, `research/mined_factors/production/<version>/`
contains the same parquet + JSON pair for survivors, plus:

- `promotion_report.json` — per-factor accept/reject decision with
  IS/OOS IR, IS/OOS RankIC mean, and the list of failed criteria
  (`oos_ir_below_threshold`, `correlated_with_higher_fitness`, …).

## Operator playbook

The minimum cadence to keep mined factors fresh:

1. **Weekly / monthly**: run `python -m src.factor_mining.miner` on
   the rolling PIT window. Each run writes to a fresh
   `runs/{run_id}/`.
2. **Quarterly**: review a promising run; promote with
   `python -m src.factor_mining.promote --run … --to vN`.
3. **As needed**: copy promising candidates into
   `research/mined_factors/candidates/{date}/` for OOS review
   before promotion (per `decisions.md` D4 — manual gated).
4. **When updating production**: bind the new pool via
   `register_mined_factor_handler(MinedFactorBundle(...))` from your
   pipeline-startup code and re-run training.

## See also

- [`decisions.md`](decisions.md) — D1 cost rate (0.003), D3 feature
  universe (six PIT fields), D4 promotion workflow (manual gated),
  D5 strict data gate (zero qlib direct imports under
  `src/factor_mining/`).
- [`scale_invariance.md`](scale_invariance.md) — the kind × taint
  type system; eight pinned pass/fail examples.
- [`inventory.md`](inventory.md) — Phase 0 repo survey; §F.3
  PIT-bundle build instructions.
- [`factor_mining_claude_code_design.md`](factor_mining_claude_code_design.md)
  — phase-by-phase implementation roadmap.
- [`research/mined_factors/README.md`](../../research/mined_factors/README.md)
  — output-layout contract.
