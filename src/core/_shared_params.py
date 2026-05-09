"""Shared configuration sub-structures used by both PipelineConfig
and WalkForwardConfig.

These are *validation-only* nested dataclasses. Both top-level configs
keep their existing flat fields (for backward-compatible YAML loading)
but delegate ``__post_init__`` validation to these sub-structures so
the same boundary checks are written exactly once.
"""

from __future__ import annotations

from dataclasses import dataclass


# ── model hyperparameters ────────────────────────────────────────

@dataclass(frozen=True)
class _ModelParams:
    """Gradient-boosted tree hyperparameters shared across both configs."""

    model_type: str
    num_boost_round: int
    early_stopping_rounds: int
    learning_rate: float
    max_depth: int
    num_leaves: int
    lambda_l1: float
    lambda_l2: float
    min_data_in_leaf: int
    feature_fraction: float
    bagging_fraction: float
    bagging_freq: int
    seed: int

    def __post_init__(self) -> None:
        if self.num_boost_round < 1:
            raise ValueError(
                f"num_boost_round must be >= 1; got {self.num_boost_round!r}."
            )
        if self.learning_rate <= 0:
            raise ValueError(
                f"learning_rate must be > 0; got {self.learning_rate!r}."
            )
        if self.max_depth < 1:
            raise ValueError(
                f"max_depth must be >= 1; got {self.max_depth!r}."
            )


# ── backtest mechanics ────────────────────────────────────────────

@dataclass(frozen=True)
class _BacktestParams:
    """Exchange / account / strategy knobs shared across both configs."""

    benchmark_code: str
    init_cash: float
    topk: int
    n_drop: int
    commission_rate: float
    stamp_tax_bps: float
    slippage_bps: float
    min_cost: float
    execution_price_kind: str
    adjust_mode: str
    signal_to_execution_lag: int
    limit_threshold: float

    def __post_init__(self) -> None:
        if self.init_cash <= 0:
            raise ValueError(
                f"init_cash must be positive; got {self.init_cash!r}."
            )
        if self.topk < 1:
            raise ValueError(
                f"topk must be >= 1; got {self.topk!r}."
            )
        for name in ("commission_rate", "stamp_tax_bps",
                      "slippage_bps", "min_cost"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(
                    f"{name} must be a real number; got "
                    f"{type(value).__name__} ({value!r})."
                )
            if value < 0:
                raise ValueError(
                    f"{name} must be >= 0; got {value!r}."
                )
        if (
            not isinstance(self.n_drop, int)
            or isinstance(self.n_drop, bool)
            or self.n_drop < 0
        ):
            raise ValueError(
                f"n_drop must be a non-negative int; got {self.n_drop!r}."
            )
        if self.n_drop >= self.topk:
            raise ValueError(
                f"n_drop ({self.n_drop}) must be < topk ({self.topk})."
            )
        if (
            not isinstance(self.limit_threshold, (int, float))
            or isinstance(self.limit_threshold, bool)
        ):
            raise ValueError("limit_threshold must be a real number.")
        if not (0.0 < float(self.limit_threshold) <= 0.25):
            raise ValueError(
                "limit_threshold must be in (0, 0.25]; got "
                f"{self.limit_threshold!r}."
            )


# ── industry attribution taxonomy ─────────────────────────────────

@dataclass(frozen=True)
class _IndustryAttributionParams:
    """Industry taxonomy artifact references shared across both configs."""

    artifact_path: str | None
    manifest_path: str | None
    taxonomy_id: str
    temporal_mode: str
