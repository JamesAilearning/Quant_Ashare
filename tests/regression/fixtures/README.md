# Fold 0 regression baseline

To create the baseline fixture for fold 0:

1. Run a known-good walk-forward pipeline (e.g. `config_walk.yaml` with `ensemble_window=1`):
   ```
   python scripts/run_walk_forward.py config_walk.yaml
   ```

2. Copy the fold-0 predictions artifact and the per-fold report:
   ```
   cp output/walk_forward/fold_00_predictions.pkl tests/regression/fixtures/fold0_predictions.pkl
   ```

3. Extract the expected metrics from `output/walk_forward/fold_00_report.json` 
   and fill in `tests/regression/fixtures/fold0_expected_metrics.json`.

4. The test in `test_fold0_baseline.py` will load the predictions, run them through
   `BacktestRunner.run`, and assert that every metric is within the tolerance
   specified in the fixture.

The fixture files are intentionally git-ignored because they contain
real predictions and backtest metrics from production runs.
