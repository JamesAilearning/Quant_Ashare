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
    # Wall-clock duration of the fold (``time.perf_counter`` delta
    # around the engine's ``_run_single_fold`` call). ``None`` when
    # the fold was resumed from a manifest that predates the timing
    # field, or when tests construct a fold without going through
    # the engine. Aggregate report surfaces ``mean_fold_duration_seconds``
    # / ``slowest_fold_*`` so operators can spot which fold is
    # dragging a "system slowly training" run.
    duration_seconds: float | None = None
    # ISO 8601 UTC timestamps captured by the engine around the fold
    # body. ``None`` for the same reason as ``duration_seconds``.
    started_at: str | None = None
    finished_at: str | None = None


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
