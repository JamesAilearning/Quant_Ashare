## Why

Several runtime paths can currently produce official or operator-facing outputs
from inputs whose provenance or numeric validity is weaker than the surrounding
contracts imply. Walk-forward ensembling changes the predictions consumed by
signal analysis and canonical backtest, but the materialized prediction source
is not recorded; separate edge cases allow non-finite values, stale indexes, or
implicit provider defaults to leak into reports.

## What Changes

- Approve walk-forward ensembling as an explicit runtime behavior only when its
  averaged prediction artifact and contributing model references are recorded.
- Require walk-forward CLI configs to provide an explicit `provider_uri`.
- Reject or skip malformed ensemble prior predictions instead of union-aligning
  mismatched indexes.
- Harden model-training, backtest position parsing, attribution, visualization,
  hyperparameter optimization, benchmark artifact loading, and Tushare provider
  conversion against the reviewed reliability failures.
- Archive completed OpenSpec changes so active scope contains only in-flight
  work.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `v2-canonical-runtime-orchestration`: approve constrained walk-forward
  ensemble semantics, require reproducible prediction provenance, require
  explicit CLI provider selection, and allow post-backtest optional analysis
  failures to be reported instead of discarding completed backtests.
- `v2-model-training-config-consistency`: require supported model families to
  receive their configured early-stopping controls and model-specific bounds.
- `v2-attribution-benchmark-weights`: require attribution to reject non-finite
  returns/weights and missing instrument-return data rather than emitting
  valid-looking zero effects.
- `v2-tushare-qlib-provider-bundle`: require adjusted VWAP fallback correctness
  and reuse of a single Tushare pro client per wrapper instance.
- `v2-benchmark-artifact-loader`: require CSV header normalization to preserve
  original column positions when deriving data indexes.

## Impact

- Affected code: `src/core/walk_forward.py`,
  `scripts/run_walk_forward.py`, `scripts/compare_walk_forward_runs.py`,
  `src/core/model_trainer.py`, `src/core/backtest_runner.py`,
  `src/core/performance_attribution.py`, `src/core/pipeline.py`,
  `src/core/hyperparam_optimizer.py`, `src/core/visualizer.py`,
  `src/data/benchmark_artifact_loader.py`,
  `src/data/_temporal_artifact_loader_base.py`,
  `src/data/tushare/provider_bundle.py`, and `src/data/tushare/client.py`.
- Affected tests: targeted runtime, governance, and data-loader regression
  tests for each hardened boundary.
- Official backtest metric calculation remains anchored to the existing
  canonical qlib path; this change hardens inputs/provenance around that path
  without introducing a competing metric engine.
