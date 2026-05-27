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

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from src.contracts import _shared_validators as _sv
from src.contracts.canonical_boundaries import (
    CANONICAL_RUNTIME_LAYER,
    CanonicalBoundaryError,
    assert_canonical_runtime_layer,
)

_EXPECTED_CANONICAL_BACKTEST_PATH = "qlib.backtest.backtest"

try:
    from qlib.backtest import backtest as _qlib_backtest_callable

    CANONICAL_OFFICIAL_BACKTEST_CALLABLE: Callable[..., Any] | None = _qlib_backtest_callable
    CANONICAL_OFFICIAL_BACKTEST_PATH = (
        f"{_qlib_backtest_callable.__module__}.{_qlib_backtest_callable.__name__}"
    )
    _QLIB_BACKTEST_ANCHOR_AVAILABLE = True
except ImportError:  # pragma: no cover - environment-dependent
    CANONICAL_OFFICIAL_BACKTEST_CALLABLE = None
    CANONICAL_OFFICIAL_BACKTEST_PATH = _EXPECTED_CANONICAL_BACKTEST_PATH
    _QLIB_BACKTEST_ANCHOR_AVAILABLE = False

# The risk/return metric helper used by BacktestRunner to turn a return series
# into official risk metrics (annualized return, information ratio, max
# drawdown, etc.) The backtest path is already locked; this is the **second**
# entry point for official numbers. Anchor it the same way so that any attempt
# to compute official metrics via ``empyrical``, ``pyfolio``, or a hand-rolled
# sharpe function inside ``src/core/`` is caught by governance rather than
# shipped silently. Forbidden alternatives are enforced in
# ``tests/governance/test_no_alt_backtest_path.py``.
_EXPECTED_CANONICAL_METRIC_HELPER_PATH = "qlib.contrib.evaluate.risk_analysis"

try:
    from qlib.contrib.evaluate import risk_analysis as _qlib_metric_helper_callable

    CANONICAL_OFFICIAL_METRIC_HELPER_CALLABLE: Callable[..., Any] | None = (
        _qlib_metric_helper_callable
    )
    CANONICAL_OFFICIAL_METRIC_HELPER_PATH = (
        f"{_qlib_metric_helper_callable.__module__}."
        f"{_qlib_metric_helper_callable.__name__}"
    )
    _QLIB_METRIC_HELPER_ANCHOR_AVAILABLE = True
except ImportError:  # pragma: no cover - environment-dependent
    CANONICAL_OFFICIAL_METRIC_HELPER_CALLABLE = None
    CANONICAL_OFFICIAL_METRIC_HELPER_PATH = _EXPECTED_CANONICAL_METRIC_HELPER_PATH
    _QLIB_METRIC_HELPER_ANCHOR_AVAILABLE = False

OFFICIAL_METRIC_STATUS = "official"
CANONICAL_INPUT_REQUIRED_FIELDS = (
    "predictions_ref",
    "evaluation_start",
    "evaluation_end",
    "account_config",
    "exchange_config",
    "adjust_mode",
    "signal_to_execution_lag",
    "benchmark_code",
)
CANONICAL_INPUT_OPTIONAL_FIELDS: tuple[()] = ()

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
    # Audit P0-1 / add-minimal-risk-constraints: schema-driven
    # consumers (UI, JSON validators) need to discover the new
    # WARN_AND_CLIP sibling field as part of the official output
    # contract. Populated only when ``risk_constraints`` was in
    # WARN_AND_CLIP mode AND at least one clip happened — see
    # ``CanonicalBacktestOutput.positions_pre_clip`` docstring.
    "positions_pre_clip",
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
class StampTaxScheduleEntry:
    """One segment of the CN stamp-tax schedule.

    ``effective_from`` is the first day (inclusive) on which ``bps``
    applies. The segment runs until the next entry's
    ``effective_from`` (or forever, for the last entry).

    The CN A-share stamp tax has changed at least twice in the
    realistic backtest window:

    * 2008-09-19: 0.1% sell-side only (10 bps).  Before this date the
      tax was charged on both sides; we do NOT model that era — see
      ``CN_STAMP_TAX_SCHEDULE_DEFAULT`` and the audit-P0-4 design
      doc for the rationale.
    * 2023-08-28: halved to 0.05% (5 bps) by MOF reform.
    """

    effective_from: date
    bps: float

    def __post_init__(self) -> None:
        # ``datetime.datetime`` is a subclass of ``datetime.date`` —
        # a bare ``isinstance(..., date)`` check accepts it, which
        # later trips a ``TypeError`` deep in
        # ``compute_effective_stamp_tax_bps`` when a ``date``
        # period bound is compared against a ``datetime``
        # ``effective_from``. Reject ``datetime`` explicitly at the
        # contract boundary so the error message names the field
        # and points at the fix (drop the time component). Codex
        # P2 follow-up on PR #178.
        if isinstance(self.effective_from, datetime):
            raise CanonicalBacktestContractError(
                "StampTaxScheduleEntry.effective_from must be a "
                "datetime.date (not datetime.datetime); got a "
                f"datetime with time component {self.effective_from!r}. "
                "YAML inputs like ``2023-08-28 00:00:00`` produce "
                "datetime values — drop the time component "
                "(``2023-08-28``) or call ``.date()`` before passing."
            )
        if not isinstance(self.effective_from, date):
            raise CanonicalBacktestContractError(
                "StampTaxScheduleEntry.effective_from must be a "
                f"datetime.date, got {type(self.effective_from).__name__}."
            )
        if not isinstance(self.bps, (int, float)) or isinstance(self.bps, bool):
            raise CanonicalBacktestContractError(
                "StampTaxScheduleEntry.bps must be a real number, "
                f"got {type(self.bps).__name__}."
            )
        if self.bps < 0 or self.bps > STAMP_TAX_BPS_MAX:
            raise CanonicalBacktestContractError(
                f"StampTaxScheduleEntry.bps for effective_from="
                f"{self.effective_from.isoformat()} must be in "
                f"[0, {STAMP_TAX_BPS_MAX}], got {self.bps}."
            )


# Canonical CN stamp-tax schedule. Operators who do not opt into a
# custom schedule resolve to this. We model the post-2008-09-19 era
# (sell-side only); pre-2008 backtests must extend the schedule
# explicitly because the tax was both-side before then and the
# single-bps-per-entry model cannot represent that.
CN_STAMP_TAX_SCHEDULE_DEFAULT: tuple[StampTaxScheduleEntry, ...] = (
    StampTaxScheduleEntry(effective_from=date(2008, 9, 19), bps=10.0),
    StampTaxScheduleEntry(effective_from=date(2023, 8, 28), bps=5.0),
)


# Marker error class — separate so config layers can catch
# schedule-shape errors specifically (vs. other contract errors).
def resolve_stamp_tax_schedule(
    value: Any,
) -> tuple[StampTaxScheduleEntry, ...]:
    """Convert a YAML / config-shaped schedule value into the typed
    tuple consumed by :class:`CanonicalExchangeCostModel`.

    Accepted inputs:

    * ``None`` → returns :data:`CN_STAMP_TAX_SCHEDULE_DEFAULT`. This
      is the recommended default for almost every CN backtest.
    * ``Sequence[Mapping[str, Any]]`` — each mapping MUST carry
      ``effective_from`` (an ``ISO-YYYY-MM-DD`` string or a
      ``datetime.date``) and ``bps`` (a real number). Returns the
      corresponding tuple of :class:`StampTaxScheduleEntry`. Order
      and bounds are NOT validated here — that is the responsibility
      of ``CanonicalExchangeCostModel._validate_stamp_tax_schedule``
      so the same validator runs whether the schedule came from
      YAML or from a direct dataclass construction.
    * ``tuple[StampTaxScheduleEntry, ...]`` — returned verbatim.
      Lets internal callers pass an already-validated schedule
      through without re-coercion.

    Raises
    ------
    CanonicalBacktestContractError
        On any other shape: a string, a single mapping, a mapping
        with the wrong keys / types, a malformed ISO date, etc. The
        message names the offending value so an operator editing a
        YAML config can fix it without reading source.
    """
    if value is None:
        return CN_STAMP_TAX_SCHEDULE_DEFAULT

    # Already typed — return verbatim.
    if isinstance(value, tuple) and all(
        isinstance(e, StampTaxScheduleEntry) for e in value
    ):
        return value

    # Reject strings (a common YAML mistake where someone writes
    # ``stamp_tax_schedule: cn_default`` thinking it's a registry
    # lookup — we don't support that for now to keep the surface
    # small).
    if isinstance(value, (str, bytes, Mapping)):
        raise CanonicalBacktestContractError(
            "resolve_stamp_tax_schedule: expected a list of "
            "{effective_from, bps} mappings or None, got "
            f"{type(value).__name__} ({value!r}). See "
            "openspec/changes/add-stamp-tax-schedule for the "
            "accepted shape."
        )
    try:
        iter(value)
    except TypeError as exc:
        raise CanonicalBacktestContractError(
            "resolve_stamp_tax_schedule: value is not iterable "
            f"({type(value).__name__}). Expected a list of "
            "{effective_from, bps} mappings or None."
        ) from exc

    entries: list[StampTaxScheduleEntry] = []
    for i, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise CanonicalBacktestContractError(
                f"resolve_stamp_tax_schedule: entry [{i}] must be a "
                f"mapping with keys 'effective_from' and 'bps'; got "
                f"{type(item).__name__}."
            )
        # Allow either ``effective_from`` (the spec-canonical key)
        # OR ``from`` as a shorthand. Reject anything else.
        raw_date = item.get("effective_from", item.get("from"))
        if raw_date is None:
            raise CanonicalBacktestContractError(
                f"resolve_stamp_tax_schedule: entry [{i}] is missing "
                "the required 'effective_from' key."
            )
        if isinstance(raw_date, date):
            eff_from = raw_date
        elif isinstance(raw_date, str):
            try:
                eff_from = date.fromisoformat(raw_date)
            except ValueError as exc:
                raise CanonicalBacktestContractError(
                    f"resolve_stamp_tax_schedule: entry [{i}] "
                    f"'effective_from' ({raw_date!r}) is not an "
                    f"ISO YYYY-MM-DD date: {exc}."
                ) from exc
        else:
            raise CanonicalBacktestContractError(
                f"resolve_stamp_tax_schedule: entry [{i}] "
                "'effective_from' must be a date or ISO YYYY-MM-DD "
                f"string, got {type(raw_date).__name__}."
            )
        if "bps" not in item:
            raise CanonicalBacktestContractError(
                f"resolve_stamp_tax_schedule: entry [{i}] is missing "
                "the required 'bps' key."
            )
        bps = item["bps"]
        # StampTaxScheduleEntry.__post_init__ enforces type + range —
        # construct it and let those checks fire.
        entries.append(StampTaxScheduleEntry(
            effective_from=eff_from, bps=bps,
        ))

    if not entries:
        raise CanonicalBacktestContractError(
            "resolve_stamp_tax_schedule: empty sequence; supply at "
            "least one entry or pass None for the CN default."
        )
    return tuple(entries)


def stamp_tax_schedule_migration_snippet() -> str:
    """Return a copy-pasteable YAML snippet for the canonical CN
    schedule. Used in legacy-key migration error messages.
    """
    return (
        "stamp_tax_schedule:\n"
        "  - effective_from: 2008-09-19\n"
        "    bps: 10.0\n"
        "  - effective_from: 2023-08-28\n"
        "    bps: 5.0\n"
    )


@dataclass(frozen=True)
class EffectiveStampTaxBps:
    """Result of resolving a schedule against a backtest period.

    ``bps`` is the single scalar passed to qlib's
    ``exchange_kwargs["close_cost"]``. When the period spans more
    than one schedule entry it is the trading-day-weighted (or
    calendar-day-weighted, when no calendar is supplied) average
    across segments.

    ``transitions`` lists the schedule entries whose
    ``effective_from`` falls STRICTLY INSIDE the requested period —
    i.e. each represents a rate change crossed during the backtest.
    When non-empty, callers (typically ``BacktestRunner.run``) emit
    a WARN log so the operator knows the per-period scalar is an
    approximation of a time-varying rate.
    """

    bps: float
    transitions: tuple[StampTaxScheduleEntry, ...]


def compute_effective_stamp_tax_bps(
    schedule: tuple[StampTaxScheduleEntry, ...],
    period_start: date,
    period_end: date,
    *,
    calendar: Sequence[date] | None = None,
) -> EffectiveStampTaxBps:
    """Collapse ``schedule`` into a single scalar over ``[period_start, period_end]``.

    Semantics:

    * Period covered by exactly one schedule entry → return that
      entry's bps, ``transitions=()``.
    * Period crosses one or more transitions → return the
      time-weighted average bps + a non-empty ``transitions`` tuple
      so the caller can WARN. Weights are TRADING DAYS when
      ``calendar`` is supplied, calendar days otherwise. (Calendar-
      day fallback shifts the weighting slightly toward holiday
      stretches; trading-day weighting is the correct semantics for
      "rate that applies on actual sell events".)
    * Period starts before ``schedule[0].effective_from`` → raise
      ``CanonicalBacktestContractError``. We deliberately do NOT
      extrapolate the earliest entry backwards, because for CN
      stamp tax that would silently model pre-2008-09-19 sell-only
      tax which was actually both-side. Operators who really need
      pre-2008 backtests must extend the schedule explicitly.

    Parameters
    ----------
    schedule
        Non-empty, ascending-by-``effective_from`` tuple of entries.
    period_start, period_end
        Inclusive backtest window bounds. ``period_end >= period_start``.
    calendar
        Optional sorted sequence of trading days. When omitted, we
        fall back to calendar-day weighting and the result is a
        slightly different scalar on windows containing long
        holidays — usable but documented as an approximation.
    """
    if not schedule:
        raise CanonicalBacktestContractError(
            "compute_effective_stamp_tax_bps: schedule is empty; "
            "expected at least one StampTaxScheduleEntry."
        )
    if period_end < period_start:
        raise CanonicalBacktestContractError(
            "compute_effective_stamp_tax_bps: period_end "
            f"({period_end.isoformat()}) < period_start "
            f"({period_start.isoformat()})."
        )
    if period_start < schedule[0].effective_from:
        raise CanonicalBacktestContractError(
            "compute_effective_stamp_tax_bps: period_start "
            f"({period_start.isoformat()}) precedes the schedule's "
            f"first entry ({schedule[0].effective_from.isoformat()}). "
            "We do NOT extrapolate the earliest rate backwards because "
            "for CN stamp tax that would silently misrepresent the "
            "pre-2008-09-19 both-side era. Extend ``schedule`` "
            "explicitly if you need a pre-coverage period."
        )

    # Walk the schedule and accumulate per-segment (start, end, bps).
    # Each segment is the half-open interval where one entry's bps applies,
    # clamped to the period bounds.
    segments: list[tuple[date, date, float]] = []
    for i, entry in enumerate(schedule):
        seg_start = max(entry.effective_from, period_start)
        seg_end_excl = (
            schedule[i + 1].effective_from
            if i + 1 < len(schedule)
            else None
        )
        # period_end is inclusive — convert to half-open by +1 day below.
        # Use period_end + 1 day as the exclusive upper bound for clamping.
        period_end_excl = date.fromordinal(period_end.toordinal() + 1)
        if seg_end_excl is None or seg_end_excl > period_end_excl:
            seg_end_excl = period_end_excl
        if seg_end_excl <= seg_start:
            continue
        segments.append((seg_start, seg_end_excl, entry.bps))

    if not segments:
        # Defensive: should be unreachable given the pre-checks above.
        raise CanonicalBacktestContractError(  # pragma: no cover
            "compute_effective_stamp_tax_bps: no schedule segment "
            "covers the requested period — internal invariant broken."
        )

    # Weight each segment. Trading-day weighting when a calendar is
    # supplied, calendar-day weighting otherwise.
    weighted_sum = 0.0
    total_weight = 0.0
    for seg_start, seg_end_excl, bps in segments:
        if calendar is None:
            weight = (seg_end_excl - seg_start).days
        else:
            weight = sum(1 for d in calendar if seg_start <= d < seg_end_excl)
        if weight <= 0:
            continue
        weighted_sum += weight * bps
        total_weight += weight

    if total_weight <= 0:
        # No segment received any weight. With ``calendar=None``,
        # weights are calendar-day counts which are positive by
        # construction (we already dropped zero-length segments
        # above), so this branch is reachable only when an
        # explicit calendar was supplied and it has zero entries
        # in EVERY segment — typically a misconfigured bundle that
        # doesn't cover the requested window, or a calendar passed
        # in trimmed to the wrong range.
        #
        # Hard-fail rather than fall back to the first segment's
        # rate. Returning ``segments[0][2]`` would silently produce
        # official metrics from a degraded cost model AND swallow
        # the transitions list, defeating the cross-period WARN.
        # Audit P0-4 / Codex P1 follow-up on PR #178.
        raise CanonicalBacktestContractError(
            "compute_effective_stamp_tax_bps: the supplied calendar "
            f"has zero trading days in [{period_start.isoformat()}, "
            f"{period_end.isoformat()}] — every schedule segment "
            "received weight 0. Cannot resolve a per-run scalar "
            "without at least one trading day. Verify the qlib "
            "provider covers the requested window and that the "
            "calendar argument is not filtered to a non-overlapping "
            "range."
        )

    avg_bps = weighted_sum / total_weight

    # Collect transitions STRICTLY INSIDE the period (excluding the
    # first segment's effective_from, which equals or precedes
    # period_start by construction).
    transitions = tuple(
        entry
        for entry in schedule
        if period_start < entry.effective_from <= period_end
    )

    return EffectiveStampTaxBps(bps=avg_bps, transitions=transitions)


@dataclass(frozen=True)
class CanonicalExchangeCostModel:
    """Frozen, bound-checked cost model for the canonical exchange.

    Fields
    ------
    commission_rate:
        Per-side commission as a fraction of trade notional (e.g. 0.0003 = 3 bps).
        Must be in ``[0, COMMISSION_RATE_MAX]``.
    stamp_tax_schedule:
        CN-market stamp tax represented as a time-ordered schedule.
        Must be a non-empty tuple of :class:`StampTaxScheduleEntry`
        sorted strictly ascending by ``effective_from``. Each entry's
        ``bps`` must be in ``[0, STAMP_TAX_BPS_MAX]``. The runtime
        collapses this into a single per-run scalar via
        :func:`compute_effective_stamp_tax_bps`. See audit P0-4 +
        ``add-stamp-tax-schedule`` change proposal.
    slippage_bps:
        Symmetric slippage assumption, applied to both buy and sell fills.
        Must be in ``[0, SLIPPAGE_BPS_MAX]``.
    min_cost:
        Per-trade minimum commission in account currency. Must be ``>= 0``.
    """

    commission_rate: float
    stamp_tax_schedule: tuple[StampTaxScheduleEntry, ...]
    slippage_bps: float
    min_cost: float

    def __post_init__(self) -> None:
        self._check_numeric("commission_rate", self.commission_rate, 0.0, COMMISSION_RATE_MAX)
        self._check_numeric("slippage_bps", self.slippage_bps, 0.0, SLIPPAGE_BPS_MAX)
        if not isinstance(self.min_cost, (int, float)) or isinstance(self.min_cost, bool):
            raise CanonicalBacktestContractError(
                f"CanonicalExchangeCostModel.min_cost must be a real number, got {type(self.min_cost).__name__}."
            )
        if self.min_cost < 0:
            raise CanonicalBacktestContractError(
                f"CanonicalExchangeCostModel.min_cost must be >= 0, got {self.min_cost}."
            )
        self._validate_stamp_tax_schedule(self.stamp_tax_schedule)

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

    @staticmethod
    def _validate_stamp_tax_schedule(
        schedule: tuple[StampTaxScheduleEntry, ...],
    ) -> None:
        """Reject empty / non-monotone / duplicate-date schedules.

        ``StampTaxScheduleEntry.__post_init__`` already enforces
        per-entry bounds (type, bps range); this method enforces
        the tuple-level invariants.
        """
        if not isinstance(schedule, tuple):
            raise CanonicalBacktestContractError(
                "CanonicalExchangeCostModel.stamp_tax_schedule must "
                f"be a tuple, got {type(schedule).__name__}."
            )
        if not schedule:
            raise CanonicalBacktestContractError(
                "CanonicalExchangeCostModel.stamp_tax_schedule must "
                "be non-empty. Use CN_STAMP_TAX_SCHEDULE_DEFAULT for "
                "the canonical CN schedule, or supply at least one "
                "StampTaxScheduleEntry."
            )
        for entry in schedule:
            if not isinstance(entry, StampTaxScheduleEntry):
                raise CanonicalBacktestContractError(
                    "CanonicalExchangeCostModel.stamp_tax_schedule "
                    "entries must be StampTaxScheduleEntry instances, "
                    f"got {type(entry).__name__}."
                )
        # Strict ascending dates, no duplicates.
        for prev, curr in zip(schedule, schedule[1:], strict=False):
            if curr.effective_from <= prev.effective_from:
                raise CanonicalBacktestContractError(
                    "CanonicalExchangeCostModel.stamp_tax_schedule "
                    "entries must be strictly ascending by "
                    f"effective_from; got "
                    f"{prev.effective_from.isoformat()} followed by "
                    f"{curr.effective_from.isoformat()}."
                )


@dataclass(frozen=True)
class CanonicalExchangeConfig:
    """Frozen exchange configuration for the canonical backtest path.

    ``limit_threshold`` is the daily price-move bound within which trades are
    simulated as executed. 0.095 matches A-share main-board ±10% (set slightly
    under the regulatory cap to avoid hitting the hard limit). ChiNext / STAR
    boards use ±20% and ST names use ±5% — callers must pass the correct value
    for the dominant universe; a per-instrument threshold is out of scope for
    the canonical boundary.
    """

    freq: str
    execution_price_kind: str
    cost_model: CanonicalExchangeCostModel
    limit_threshold: float = 0.095

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
        if (
            not isinstance(self.limit_threshold, (int, float))
            or isinstance(self.limit_threshold, bool)
        ):
            raise CanonicalBacktestContractError(
                "CanonicalExchangeConfig.limit_threshold must be a real number, "
                f"got {type(self.limit_threshold).__name__}."
            )
        if not (0.0 < float(self.limit_threshold) <= 0.25):
            raise CanonicalBacktestContractError(
                "CanonicalExchangeConfig.limit_threshold must be in (0, 0.25]; "
                f"got {self.limit_threshold}. Use 0.095 for A-share main board, "
                "0.195 for ChiNext/STAR, 0.045 for ST."
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
    benchmark_code: str = ""
    source_layer: str = CANONICAL_RUNTIME_LAYER
    allow_implicit_fallback: bool = False
    experimental_controls: Mapping[str, Any] = field(default_factory=dict)
    research_artifact_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Reject non-empty benchmark_code at construction so the type
        # annotation matches runtime behaviour — ``Optional[str]`` was
        # misleading: ``validate_input`` always rejects None/empty.
        if not self.benchmark_code:
            raise CanonicalBacktestContractError(
                "CanonicalBacktestInput.benchmark_code must be non-empty; "
                "canonical backtest requires a benchmark."
            )
        # Reject the three "explicitly rejected" fields the moment a non-
        # default value is supplied — without this, callers could
        # construct an input with experimental knobs set, pass it
        # around, and only discover the rejection at validate_input
        # time. ``CANONICAL_INPUT_EXPLICITLY_REJECTED_FIELDS`` lists the
        # fields whose mere presence (with non-default values) is a
        # contract violation; defaults are tolerated so this dataclass
        # can still be constructed by code that doesn't know about the
        # restriction list.
        if self.allow_implicit_fallback:
            raise CanonicalBacktestContractError(
                "CanonicalBacktestInput.allow_implicit_fallback=True is "
                "not accepted by the canonical contract. Remove the field "
                "or use a non-canonical experimental harness."
            )
        if self.experimental_controls:
            raise CanonicalBacktestContractError(
                "CanonicalBacktestInput.experimental_controls is non-empty; "
                "experimental_controls are non-canonical and not accepted "
                "by canonical contract input."
            )
        if self.research_artifact_refs:
            raise CanonicalBacktestContractError(
                "CanonicalBacktestInput.research_artifact_refs is non-empty; "
                "research_artifact_refs are non-canonical and not accepted "
                "by canonical contract input."
            )


@dataclass(frozen=True)
class CanonicalBacktestOutput:
    """Output boundary for canonical official metrics payload.

    ``positions`` is the authoritative per-day portfolio weight map
    ``{date_str: {instrument: weight}}`` where ``weight`` sums to ~1.0
    (long-only portfolios). Downstream consumers (performance attribution,
    turnover analysis) must prefer this over reconstructing weights from
    predictions, which would diverge from the actual topk-dropout selection.

    ``positions_pre_clip`` carries the qlib-produced positions BEFORE
    any risk-constraint clipping. Empty (``{}``) by default — only
    populated when ``BacktestRunner.run`` was given
    ``risk_constraints`` in ``WARN_AND_CLIP`` mode AND at least one
    constraint actually clipped weight on at least one day. In that
    case ``positions`` reflects the clipped allocation (what the
    operator should trade) and ``positions_pre_clip`` reflects what
    qlib's executor actually ran (what produced ``return_series`` /
    ``risk_analysis``). Audit P0-1 / add-minimal-risk-constraints.
    """

    metric_status: str
    official_backtest_path: str
    return_series: Mapping[str, Any]
    risk_analysis: Mapping[str, Any]
    report: Mapping[str, Any]
    provenance: Mapping[str, Any]
    positions: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    positions_pre_clip: Mapping[str, Mapping[str, float]] = field(default_factory=dict)


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
        if request.signal_to_execution_lag < 0:
            raise CanonicalBacktestContractError(
                "signal_to_execution_lag must be >= 0; use 0 only for explicit "
                "same-day execution/no shift, and 1 for T+1 delayed execution. "
                f"got {request.signal_to_execution_lag}."
            )

        if not str(request.benchmark_code or "").strip():
            raise CanonicalBacktestContractError(
                "benchmark_code is required for canonical backtest; "
                "pass an explicit benchmark (e.g. 'SH000300')."
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
