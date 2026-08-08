"""Shared canonical-backtest request assembly for the two engines.

``Pipeline`` (single-fold) and ``WalkForwardEngine`` (rolling) both turn
their config into the SAME ``CanonicalBacktestInput`` and the SAME risk
constraints object before calling ``BacktestRunner.run``. Those two
assemblies used to be hand-maintained copies: 18 of the request's fields
(exchange / cost model / stamp-tax schedule / slippage / limit threshold /
adjust mode / execution lag / benchmark) were byte-identical in both
engines, as was the 7-line risk-constraints policy expression, and only
the run-shape arguments legitimately differed (which artifact the
predictions came from, and which window is being evaluated).

Why that mattered: AGENTS.md's "Two engines, one schema" rule exists
because an asymmetry between the engines is invisible — both runs succeed
and both publish official metrics, they just charge different costs or
apply different constraints. ``tests/governance/
test_two_engine_schema_parity.py`` DETECTS report-key drift after the
fact; assembling the request in one place makes request-field drift
impossible by construction.

The engine configs are two unrelated dataclasses, so the shared surface is
expressed as a ``Protocol`` — structural typing keeps this module from
importing (and coupling to) either engine.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Protocol

from src.core.canonical_backtest_contract import (
    CanonicalAccountConfig,
    CanonicalBacktestInput,
    CanonicalExchangeConfig,
    CanonicalExchangeCostModel,
    resolve_stamp_tax_schedule,
)
from src.core.risk_constraints import (
    MinimalRiskConstraints,
    RiskConstraintMode,
    campaign_risk_constraints_v1,
)


class CanonicalBacktestConfig(Protocol):
    """The config surface a canonical backtest is assembled from.

    Structural, not nominal: ``PipelineConfig`` and ``WalkForwardConfig``
    both satisfy it without inheriting anything. A new engine config that
    grows the same fields becomes usable here for free; one that DROPS a
    field fails type-checking at the call site rather than at runtime.

    Members are declared READ-ONLY (``@property``) rather than as plain
    annotations: both engine configs are ``@dataclass(frozen=True)``, and a
    plain annotation would demand a settable attribute that a frozen
    dataclass cannot provide.
    """

    # Account / exchange
    @property
    def init_cash(self) -> float: ...
    @property
    def execution_price_kind(self) -> str: ...
    @property
    def commission_rate(self) -> float: ...
    @property
    def stamp_tax_schedule(self) -> Any: ...
    @property
    def slippage_bps(self) -> float: ...
    @property
    def min_cost(self) -> float: ...
    @property
    def limit_threshold(self) -> float: ...

    # Execution semantics
    @property
    def adjust_mode(self) -> str: ...
    @property
    def signal_to_execution_lag(self) -> int: ...
    @property
    def benchmark_code(self) -> str: ...

    # Risk-constraints policy
    @property
    def risk_constraints_enabled(self) -> bool: ...
    @property
    def risk_constraints_calibration(self) -> str: ...
    @property
    def risk_constraints_mode(self) -> str: ...


def build_canonical_request(
    config: CanonicalBacktestConfig,
    *,
    predictions_ref: str,
    evaluation_start: str,
    evaluation_end: str,
) -> CanonicalBacktestInput:
    """Assemble the canonical backtest request from an engine config.

    The three keyword arguments are exactly the parts that legitimately
    differ between engines: ``predictions_ref`` is a provenance marker for
    where the predictions came from (a model artifact for the single-fold
    run, a per-fold prediction artifact for walk-forward), and the
    evaluation window is the run's own test window (a config field for
    Pipeline, a rolling fold window for WalkForward). Everything else is
    config-derived and therefore identical across engines by construction.
    """
    return CanonicalBacktestInput(
        predictions_ref=predictions_ref,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        account_config=CanonicalAccountConfig(init_cash=config.init_cash),
        exchange_config=CanonicalExchangeConfig(
            freq="day",
            execution_price_kind=config.execution_price_kind,
            cost_model=CanonicalExchangeCostModel(
                commission_rate=config.commission_rate,
                stamp_tax_schedule=resolve_stamp_tax_schedule(
                    config.stamp_tax_schedule,
                ),
                slippage_bps=config.slippage_bps,
                min_cost=config.min_cost,
            ),
            limit_threshold=config.limit_threshold,
        ),
        adjust_mode=config.adjust_mode,
        signal_to_execution_lag=config.signal_to_execution_lag,
        benchmark_code=config.benchmark_code,
    )


def resolve_risk_constraints(
    config: CanonicalBacktestConfig,
) -> MinimalRiskConstraints | None:
    """Resolve the run's risk-constraints object, or ``None`` when the
    layer is disabled.

    Two independent config choices compose here and BOTH must reach the
    runtime identically on either engine:

    * the CALIBRATION (CSI800 guard-2 / veto-4, codex P2 on #372) — the
      campaign presets opt into ``campaign_v1`` (name/leverage strict,
      board/cash structural mismatches disabled with rationale);
      everything else keeps the audit P0-1 defaults;
    * the MODE (codex #406) — recording the field without threading it
      would look exactly like a working opt-in while every fold still
      aborted, which is why ``tests/governance/
      test_risk_constraints_mode_optin.py`` pins the threading.

    The effective values land in the backtest provenance either way.
    """
    if not config.risk_constraints_enabled:
        return None
    base = (
        campaign_risk_constraints_v1()
        if config.risk_constraints_calibration == "campaign_v1"
        else MinimalRiskConstraints()
    )
    return replace(base, mode=RiskConstraintMode(config.risk_constraints_mode))
