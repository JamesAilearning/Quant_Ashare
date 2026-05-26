# Regression baselines

Two regression tests live in this directory; both are E2E-gated
(`RUN_E2E=1`) and both skip silently when their fixture files are
absent.

## 1. Fold-0 backtest baseline — `test_fold0_baseline`

Re-runs `BacktestRunner.run` against a frozen fold-0 prediction
fixture and asserts headline backtest metrics haven't drifted.

Generate the two fixtures:

1. Run a known-good walk-forward pipeline (e.g. `config_walk.yaml`
   with `ensemble_window=1`):
   ```
   python scripts/run_walk_forward.py config_walk.yaml
   ```

2. Copy the fold-0 predictions artifact:
   ```
   cp output/walk_forward/fold_00_predictions.pkl \
      tests/regression/fixtures/fold0_predictions.pkl
   ```

3. Extract the expected metrics from
   `output/walk_forward/fold_00_report.json` into
   `tests/regression/fixtures/fold0_expected_metrics.json`
   (see the test for the expected schema).

## 2. Walk-forward aggregate baseline — `test_walk_forward_aggregate_baseline` (FU-5)

Re-runs the FULL walk-forward (all folds, ensemble, aggregate) and
asserts headline aggregate metrics (mean IR / IC / annualized
return / worst drawdown) stay within **±5%** of the stored baseline.

Generate the baseline:

```
RUN_E2E=1 python scripts/generate_regression_baseline.py [config.yaml]
```

The script writes `walk_forward_baseline_metrics.json` here. Per
the project's reference-data workflow:

> I pull, you eyeball, you sign off, I commit.

So the script **does NOT auto-commit** the baseline. Operators must
open the JSON, sanity-check the headline numbers against expected
ranges, then `git add` + `git commit`.

Optionally drop a copy of the config used to generate the baseline
at `walk_forward_baseline_config.yaml` next to the metrics fixture
— the test prefers it over `config_walk.yaml` at the project root,
which means a baseline and its config can't drift apart.

## Why fixtures are git-ignored by default

`fold0_predictions.pkl` contains real predictions from production
runs; `walk_forward_baseline_metrics.json` carries metrics tied to a
specific bundle vintage. Both are useful regression anchors but
neither is appropriate for unconditional inclusion in the repo —
they grow stale on every bundle refresh. The reference-data
workflow keeps them under operator control.

## When to refresh

Refresh whenever:

- A merged PR intentionally changes the headline metric (e.g. PR1
  fixed the rank-IC double-counting; pre-PR1 baselines are
  no longer comparable).
- The qlib bundle is re-ingested with a new coverage window.
- Tushare publishes corrected historical data (rare).

Do NOT refresh because the baseline is "slightly off" — that's
exactly the regression these tests exist to surface.
