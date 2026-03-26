"""Canonical backtest contract interfaces for V2 (foundation-only placeholder)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from src.contracts.canonical_boundaries import (
    CANONICAL_RUNTIME_LAYER,
    CanonicalBoundaryError,
    assert_canonical_runtime_layer,
)

CANONICAL_OFFICIAL_BACKTEST_PATH = "qlib-native backtest_daily"
OFFICIAL_METRIC_STATUS = "official"
CANONICAL_INPUT_REQUIRED_FIELDS = (
    "predictions_ref",
    "evaluation_start",
    "evaluation_end",
    "account_config",
    "exchange_config",
)
CANONICAL_INPUT_OPTIONAL_FIELDS = ("benchmark_code",)
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
)


class CanonicalBacktestContractError(ValueError):
    """Raised when canonical backtest contract constraints are violated."""


@dataclass(frozen=True)
class CanonicalBacktestInput:
    """Input boundary for the canonical official-metrics path."""

    predictions_ref: str
    evaluation_start: str
    evaluation_end: str
    account_config: Mapping[str, Any]
    exchange_config: Mapping[str, Any]
    benchmark_code: Optional[str] = None
    source_layer: str = CANONICAL_RUNTIME_LAYER
    allow_implicit_fallback: bool = False
    experimental_controls: Mapping[str, Any] = field(default_factory=dict)
    research_artifact_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class CanonicalBacktestOutput:
    """Output boundary for canonical official metrics payload."""

    metric_status: str
    official_backtest_path: str
    return_series: Mapping[str, Any]
    risk_analysis: Mapping[str, Any]
    report: Mapping[str, Any]
    provenance: Mapping[str, Any]


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
        if not request.account_config:
            raise CanonicalBacktestContractError("account_config is required.")
        if not request.exchange_config:
            raise CanonicalBacktestContractError("exchange_config is required.")

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
