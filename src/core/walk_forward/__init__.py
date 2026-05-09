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

# Re-export symbols that tests reference via mock.patch paths
# (not part of the public API — not in __all__):
from src.core.backtest_runner import BacktestRunner  # noqa: F401
from src.core.model_trainer import ModelTrainer  # noqa: F401
from src.core.performance_attribution import PerformanceAttribution  # noqa: F401
from src.core.qlib_runtime import is_canonical_qlib_initialized  # noqa: F401
from src.core.signal_analyzer import SignalAnalyzer  # noqa: F401
from src.data.feature_dataset_builder import FeatureDatasetBuilder  # noqa: F401

__all__ = (
    "WalkForwardConfig",
    "WalkForwardError",
    "WalkForwardFold",
    "WalkForwardResult",
    "WalkForwardEngine",
)
