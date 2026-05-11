from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class WalkForwardFold:
    """Result for a single fold in the walk-forward process."""

    fold_index: int
    train_period: str
    valid_period: str
    test_period: str
    ic_1d: float
    ic_5d: float
    annualized_return: float
    max_drawdown: float
    information_ratio: float
    prediction_shape: tuple[int, ...]
    # Path to the per-fold JSON report written by ``_run_single_fold``.
    # Optional so legacy callers / mock-based tests that construct a
    # fold without persisting a report (e.g. the aggregate-NaN tests
    # below) keep working unchanged.
    report_path: str | None = None


@dataclass(frozen=True)
class WalkForwardResult:
    """Aggregated walk-forward results."""

    folds: Sequence[WalkForwardFold]
    aggregate_metrics: Mapping[str, float]
    num_folds: int
    # Path to the aggregate JSON report written by ``WalkForwardEngine.run``.
    # ``None`` when the engine ran without persisting one (e.g. legacy
    # callers patched only ``_run_single_fold`` and never reach the
    # aggregate-write step).
    report_path: str | None = None
