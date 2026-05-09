"""Walk-forward (rolling) backtest engine.

Simulates realistic model deployment by repeatedly:
1. Training on [train_start, train_end]
2. Validating on [valid_start, valid_end]
3. Predicting + backtesting on [test_start, test_end]
4. Rolling all windows forward by ``step_months``

This produces a series of non-overlapping out-of-sample periods whose
results can be stitched together for a full-period performance view.

Boundaries
----------
- Requires canonical qlib init.
- Reuses FeatureDatasetBuilder, ModelTrainer, BacktestRunner, SignalAnalyzer.
"""

from src.core.walk_forward._types import WalkForwardFold, WalkForwardResult
from src.core.walk_forward.config import WalkForwardConfig, WalkForwardError
from src.core.walk_forward.engine import WalkForwardEngine

__all__ = (
    "WalkForwardConfig",
    "WalkForwardError",
    "WalkForwardFold",
    "WalkForwardResult",
    "WalkForwardEngine",
)
