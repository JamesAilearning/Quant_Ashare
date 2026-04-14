"""Canonical backtest contract interfaces for V2 (foundation-only placeholder).

The canonical official-metrics path is anchored to a concrete qlib callable
via import, so that any attempt to introduce a competing path (for example
a legacy ``contrib``-level evaluate helper) is statically visible at import
time instead of only in documentation. The list of forbidden alternative
paths is enforced by ``tests/governance/test_no_alt_backtest_path.py``.

If qlib is not installed in the current environment, the anchor falls back
to the expected path string so that contract-only tests can still run;
governance regression tests explicitly verify the live anchor when qlib
is importable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

from src.contracts import _shared_validators as _sv
from src.contracts.canonical_boundaries import (
    CANONICAL_RUNTIME_LAYER,
    CanonicalBoundaryError,
    assert_canonical_runtime_layer,
)

_EXPECTED_CANONICAL_BACKTEST_PATH = "qlib.backtest.backtest"

try:
    from qlib.backtest import backtest as _qlib_backtest_callable  # type: ignore[import-not-found]

    CANONICAL_OFFICIAL_BACKTEST_CALLABLE: Optional[Callable[..., Any]] = _qlib_backtest_callable
    CANONICAL_OFFICIAL_BACKTEST_PATH = (
        f"{_qlib_backtest_callable.__module__}.{_qlib_backtest_callable.__name__}"
    )
    _QLIB_BACKTEST_ANCHOR_AVAILABLE = True
except ImportError:  # pragma: no cover - environment-dependent
    CANONICAL_OFFICIAL_BACKTEST_CALLABLE = None
    CANONICAL_OFFICIAL_BACKTEST_PATH = _EXPECTED_CANONICAL_BACKTEST_PATH
    _QLIB_BACKTEST_ANCHOR_AVAILABLE = False
OFFICIAL_METRIC_STATUS = "official"
CANONICAL_INPUT_REQUIRED_FIELDS = (
    "predictions_ref",
    "evaluation_start",
    "evaluation_end",
    "account_config",
    "exchange_config",
    "adjust_mode",
    "signal_to_execution_lag",
)
CANONICAL_INPUT_OPTIONAL_FIELDS = ("benchmark_code",)

ADJUST_MODE_PRE = "pre_adjusted"
ADJUST_MODE_POST = "post_adjusted"
ADJUST_MODE_NONE = "unadjusted"
SUPPORTED_ADJUST_MODES: tuple[str, ...] = (
    ADJUST_MODE_PRE,
    ADJUST_MODE_POST,
    ADJUST_MODE_NONE,
)

EXECUTION_PRICE_OPEN = "open"
EXECUTION_PRICE_CLOSE = "close"
EXECUTION_PRICE_VWAP = "vwap"
SUPPORTED_EXECUTION_PRICE_KINDS: tuple[str, ...] = (
    EXECUTION_PRICE_OPEN,
    EXECUTION_PRICE_CLOSE,
    EXECUTION_PRICE_VWAP,
)

SUPPORTED_EXCHANGE_FREQUENCIES: tuple[str, ...] = ("day",)

# Opinionated bounds on cost-model fields. Relaxing these is change-controlled
# via a dedicated spec change. See
# openspec/changes/archive/2026-04-08-harden-canonical-backtest-input-for-quant-risks/design.md
# for rationale.
COMMISSION_RATE_MAX = 0.01          # 100 bps per side, one-way
STAMP_TAX_BPS_MAX = 100.0           # 100 bps sell-side
SLIPPAGE_BPS_MAX = 200.0            # 200 bps symmetric
CANONICAL_INPUT_EXPLICITLY_REJECTED_FIELDS = (
    "experimental_controls",
    "research_artifact_refs",
    "allow_implicit_fallback",
)
CANONICAL_OUTPUT_FIELDS = (
    "metric_status",
    "official_backtest_path",
    "return_series",
    "risk_analysis",
    "report",
    "provenance",
    "positions",
)


class CanonicalBacktestContractError(ValueError):
    """Raised when canonical backtest contract constraints are violated."""


@dataclass(frozen=True)
class CanonicalAccountConfig:
    """Frozen account configuration for the canonical backtest path."""

    init_cash: float

    def __post_init__(self) -> None:
        if not isinstance(self.init_cash, (int, float)) or isinstance(self.init_cash, bool):
            raise CanonicalBacktestContractError(
                f"CanonicalAccountConfig.init_cash must be a real number, got {type(self.init_cash).__name__}."
            )
        if self.init_cash <= 0:
            raise CanonicalBacktestContractError(
                f"CanonicalAccountConfig.init_cash must be > 0, got {self.init_cash}."
            )


@dataclass(frozen=True)
class CanonicalExchangeCostModel:
    """Frozen, bound-checked cost model for the canonical exchange.

    Fields
    ------
    commission_rate:
        Per-side commission as a fraction of trade notional (e.g. 0.0003 = 3 bps).
        Must be in ``[0, COMMISSION_RATE_MAX]``.
    stamp_tax_bps:
        CN-market stamp tax, sell-side only, in basis points.
        Must be in ``[0, STAMP_TAX_BPS_MAX]``.
    slippage_bps:
        Symmetric slippage assumption, applied to both buy and sell fills.
        Must be in ``[0, SLIPPAGE_BPS_MAX]``.
    min_cost:
        Per-trade minimum commission in account currency. Must be ``>= 0``.
    """

    commission_rate: float
    stamp_tax_bps: float
    slippage_bps: float
    min_cost: float

    def __post_init__(self) -> None:
        self._check_numeric("commission_rate", self.commission_rate, 0.0, COMMISSION_RATE_MAX)
        self._check_numeric("stamp_tax_bps", self.stamp_tax_bps, 0.0, STAMP_TAX_BPS_MAX)
        self._check_numeric("slippage_bps", self.slippage_bps, 0.0, SLIPPAGE_BPS_MAX)
        if not isinstance(self.min_cost, (int, float)) or isinstance(self.min_cost, bool):
            raise CanonicalBacktestContractError(
                f"CanonicalExchangeCostModel.min_cost must be a real number, got {type(self.min_cost).__name__}."
            )
        if self.min_cost < 0:
            raise CanonicalBacktestContractError(
                f"CanonicalExchangeCostModel.min_cost must be >= 0, got {self.min_cost}."
            )

    @staticmethod
    def _check_numeric(field_name: str, value: Any, lo: float, hi: float) -> None:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise CanonicalBacktestContractError(
                f"CanonicalExchangeCostModel.{field_name} must be a real number, got {type(value).__name__}."
            )
        if value < lo or value > hi:
            raise CanonicalBacktestContractError(
                f"CanonicalExchangeCostModel.{field_name} must be in [{lo}, {hi}], got {value}."
            )


@dataclass(frozen=True)
class CanonicalExchangeConfig:
    """Frozen exchange configuration for the canonical backtest path."""

    freq: str
    execution_price_kind: str
    cost_model: CanonicalExchangeCostModel

    def __post_init__(self) -> None:
        if self.freq not in SUPPORTED_EXCHANGE_FREQUENCIES:
            raise CanonicalBacktestContractError(
                f"CanonicalExchangeConfig.freq must be one of {SUPPORTED_EXCHANGE_FREQUENCIES}, "
                f"got '{self.freq}'."
            )
        if self.execution_price_kind not in SUPPORTED_EXECUTION_PRICE_KINDS:
            raise CanonicalBacktestContractError(
                f"CanonicalExchangeConfig.execution_price_kind must be one of "
                f"{SUPPORTED_EXECUTION_PRICE_KINDS}, got '{self.execution_price_kind}'."
            )
        if not isinstance(self.cost_model, CanonicalExchangeCostModel):
            raise CanonicalBacktestContractError(
                "CanonicalExchangeConfig.cost_model must be a CanonicalExchangeCostModel instance."
            )


@dataclass(frozen=True)
class CanonicalBacktestInput:
    """Input boundary for the canonical official-metrics path."""

    predictions_ref: str
    evaluation_start: str
    evaluation_end: str
    account_config: CanonicalAccountConfig
    exchange_config: CanonicalExchangeConfig
    adjust_mode: str
    signal_to_execution_lag: int
    benchmark_code: Optional[str] = None
    source_layer: str = CANONICAL_RUNTIME_LAYER
    allow_implicit_fallback: bool = False
    experimental_controls: Mapping[str, Any] = field(default_factory=dict)
    research_artifact_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class CanonicalBacktestOutput:
    """Output boundary for canonical official metrics payload.

    ``positions`` is the authoritative per-day portfolio weight map
    ``{date_str: {instrument: weight}}`` where ``weight`` sums to ~1.0
    (long-only portfolios). Downstream consumers (performance attribution,
    turnover analysis) must prefer this over reconstructing weights from
    predictions, which would diverge from the actual topk-dropout selection.
    """

    metric_status: str
    official_backtest_path: str
    return_series: Mapping[str, Any]
    risk_analysis: Mapping[str, Any]
    report: Mapping[str, Any]
    provenance: Mapping[str, Any]
    positions: Mapping[str, Mapping[str, float]] = field(default_factory=dict)


class CanonicalBacktestContract:
    """Canonical backtest contract placeholder (no runtime execution in this change)."""

    @staticmethod
    def list_official_paths() -> tuple[str, ...]:
        """Canonical official metrics source set (must remain singular)."""
        return (CANONICAL_OFFICIAL_BACKTEST_PATH,)

    @staticmethod
    def input_boundary() -> Mapping[str, tuple[str, ...]]:
        """Declarative canonical input boundary for docs/tests and future wiring."""
        return {
            "required": CANONICAL_INPUT_REQUIRED_FIELDS,
            "optional": CANONICAL_INPUT_OPTIONAL_FIELDS,
            "rejected": CANONICAL_INPUT_EXPLICITLY_REJECTED_FIELDS,
        }

    @staticmethod
    def output_schema() -> tuple[str, ...]:
        """Declarative canonical output schema for official metrics payload."""
        return CANONICAL_OUTPUT_FIELDS

    @staticmethod
    def validate_input(request: CanonicalBacktestInput) -> None:
        """Validate canonical input boundary; execution is intentionally out of scope."""
        if not str(request.predictions_ref or "").strip():
            raise CanonicalBacktestContractError("predictions_ref is required for canonical backtest contract.")
        if not str(request.evaluation_start or "").strip() or not str(request.evaluation_end or "").strip():
            raise CanonicalBacktestContractError("evaluation_start and evaluation_end are required.")
        start_d = _sv.parse_iso_date(
            request.evaluation_start, error_cls=CanonicalBacktestContractError
        )
        end_d = _sv.parse_iso_date(
            request.evaluation_end, error_cls=CanonicalBacktestContractError
        )
        if start_d is not None and end_d is not None and start_d > end_d:
            raise CanonicalBacktestContractError(
                f"evaluation_start ({request.evaluation_start}) must be <= "
                f"evaluation_end ({request.evaluation_end})."
            )
        if not isinstance(request.account_config, CanonicalAccountConfig):
            raise CanonicalBacktestContractError(
                "account_config must be a CanonicalAccountConfig instance; "
                "free-form dicts are not accepted by the canonical contract."
            )
        if not isinstance(request.exchange_config, CanonicalExchangeConfig):
            raise CanonicalBacktestContractError(
                "exchange_config must be a CanonicalExchangeConfig instance; "
                "free-form dicts are not accepted by the canonical contract."
            )

        if request.adjust_mode not in SUPPORTED_ADJUST_MODES:
            raise CanonicalBacktestContractError(
                f"adjust_mode must be one of {SUPPORTED_ADJUST_MODES}, got '{request.adjust_mode}'."
            )

        if not isinstance(request.signal_to_execution_lag, int) or isinstance(request.signal_to_execution_lag, bool):
            raise CanonicalBacktestContractError(
                f"signal_to_execution_lag must be an int, got {type(request.signal_to_execution_lag).__name__}."
            )
        if request.signal_to_execution_lag < 1:
            raise CanonicalBacktestContractError(
                "signal_to_execution_lag must be >= 1 to avoid look-ahead bias; "
                f"got {request.signal_to_execution_lag}."
            )

        if request.allow_implicit_fallback:
            raise CanonicalBacktestContractError(
                "Implicit fallback is forbidden in canonical contract; behavior must be explicit."
            )

        try:
            assert_canonical_runtime_layer(request.source_layer)
        except CanonicalBoundaryError as exc:
            raise CanonicalBacktestContractError(str(exc)) from exc

        if request.experimental_controls:
            raise CanonicalBacktestContractError(
                "experimental_controls are non-canonical and not accepted by canonical contract input."
            )
        if request.research_artifact_refs:
            raise CanonicalBacktestContractError(
                "research_artifact_refs are non-canonical and cannot be consumed by canonical contract."
            )

    @classmethod
    def run_placeholder(cls, request: CanonicalBacktestInput) -> CanonicalBacktestOutput:
        """Execution placeholder only; runtime implementation is intentionally deferred."""
        cls.validate_input(request)
        raise NotImplementedError(
            "Canonical backtest runtime is intentionally unimplemented in this contract-only change."
        )
