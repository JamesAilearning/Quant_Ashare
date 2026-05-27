"""Canonical boundary for risk constraints.

Two surfaces live here, kept distinct on purpose:

1. :class:`MinimalRiskConstraints` — the LIVE constraint engine
   introduced by audit P0-1 / openspec/changes/add-minimal-risk-constraints.
   Covers four constraints (per-name, per-board, cash-buffer-min,
   leverage) with two enforcement modes (``RAISE`` and
   ``WARN_AND_CLIP``). Called from
   :meth:`src.core.backtest_runner.BacktestRunner.run` after the
   qlib positions map is built.

2. :class:`RiskConstraintEngine` — the legacy fail-closed stub
   that pre-dated the live engine. Kept here unchanged because its
   "any apply() raises" contract is referenced by callers /
   governance tests that explicitly want a deliberately-fails
   surface. Do NOT route new code through this class — use
   :class:`MinimalRiskConstraints` instead. The stub's docstring
   below was true before audit P0-1; it stays true as a description
   of THIS class specifically.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.core.board_heuristic import classify_instrument
from src.core.logger import get_logger

_logger = get_logger(__name__)


class RiskConstraintError(RuntimeError):
    """Raised by ``MinimalRiskConstraints.apply`` in ``RAISE`` mode
    on any non-empty violations, and by ``RiskConstraintEngine`` on
    any call (compat with the pre-P0-1 stub behaviour).
    """


# ---------------------------------------------------------------------------
# Enforcement mode
# ---------------------------------------------------------------------------


class RiskConstraintMode(str, Enum):
    """How ``MinimalRiskConstraints.apply`` reacts to violations.

    * ``RAISE`` — collect every violation across every day, then
      raise a single ``RiskConstraintError`` listing all of them.
      Used by backtest-validation workflows where any violation
      should abort the run.
    * ``WARN_AND_CLIP`` — emit one WARN log per violation and
      return a positions map that has been retroactively clipped
      to the limits. Used by live-deployment workflows where the
      operator wants to know about violations but a single bad
      day should not stop the run; the clipped map is what the
      operator would actually trade.
    """

    RAISE = "raise"
    WARN_AND_CLIP = "warn_and_clip"


# ---------------------------------------------------------------------------
# Violation records + result type
# ---------------------------------------------------------------------------


_CASH_BUCKET = "__cash__"
_PORTFOLIO_BUCKET = "__portfolio__"


@dataclass(frozen=True)
class RiskConstraintViolation:
    """One constraint violation on one day.

    Structured (not a string) so downstream tools (UI, JSON
    artifacts, alerting) can filter / aggregate by constraint
    type or instrument without parsing.
    """

    date: str
    constraint_name: str
    instrument_or_bucket: str
    actual: float
    limit: float
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskConstraintsApplyResult:
    """Return value of :meth:`MinimalRiskConstraints.apply`.

    ``clipped_positions`` is always populated with the
    constraint-respecting allocation (no-op when there were no
    violations). ``was_clipped`` is True iff the clipping actually
    moved any weight. Callers in ``WARN_AND_CLIP`` mode use
    ``clipped_positions`` as the authoritative live-trade map;
    callers in ``RAISE`` mode receive the same data but only
    after the engine itself has already raised on any non-empty
    violations.
    """

    violations: tuple[RiskConstraintViolation, ...]
    clipped_positions: Mapping[str, Mapping[str, float]]
    was_clipped: bool


# ---------------------------------------------------------------------------
# MinimalRiskConstraints — the live engine
# ---------------------------------------------------------------------------


# Defaults chosen for a long-only retail A-share profile. Each is
# documented in the dataclass docstring + in the OpenSpec design.
_DEFAULT_MAX_PER_NAME = 0.05
_DEFAULT_MAX_PER_BOARD = 0.40
_DEFAULT_CASH_BUFFER_MIN = 0.01
_DEFAULT_MAX_LEVERAGE = 1.00


@dataclass(frozen=True)
class MinimalRiskConstraints:
    """Frozen configuration + apply method for the four-constraint
    risk engine introduced by audit P0-1.

    Constraints
    -----------
    max_per_name
        Maximum single-instrument weight (fraction of NAV).
        Default 0.05 (5%). A topk=50 portfolio with equal weights
        sits at 0.02 per name, so 0.05 leaves headroom for a
        concentrated tilt while ruling out the "one-stock-blew-up"
        failure mode.
    max_per_board
        Maximum aggregate weight per A-share BOARD bucket
        (Shanghai Main / Shenzhen Main / ChiNext / STAR / BSE).
        Default 0.40. Uses ``board_heuristic.classify_instrument``;
        board ≠ industry — see ``board_heuristic`` module docstring.
        Operators who need an industry cap have to wait for the
        Phase E industry-artifact wiring.
    cash_buffer_min
        Minimum cash share of NAV. Default 0.01 (1%). Provides
        room for trading costs / slippage friction; a long-only
        portfolio with cash_buffer_min > 0 cannot accidentally
        run >100% invested.
    max_leverage
        Maximum sum of absolute instrument weights. Default 1.00.
        For a long-only portfolio this is the same as
        ``sum(weights) <= max_leverage``, so it constrains the
        invested fraction. Combined with ``cash_buffer_min``,
        the tighter of the two governs (sum(weights) <=
        min(max_leverage, 1 - cash_buffer_min)).
    mode
        ``RAISE`` (default) or ``WARN_AND_CLIP``. See
        :class:`RiskConstraintMode`.

    Use
    ---
    Pass an instance via the ``risk_constraints`` kwarg to
    :meth:`src.core.backtest_runner.BacktestRunner.run`. The
    engine is post-trade: it inspects the positions map qlib
    produced. In ``RAISE`` mode it aborts the run on any
    violation; in ``WARN_AND_CLIP`` mode it logs each violation
    and exposes the clipped positions on
    ``CanonicalBacktestOutput.positions`` (with the original
    unclipped map preserved on ``positions_pre_clip``).

    See ``openspec/changes/add-minimal-risk-constraints/`` for
    the full design including the post-trade-vs-pre-trade
    trade-off.
    """

    max_per_name: float = _DEFAULT_MAX_PER_NAME
    max_per_board: float = _DEFAULT_MAX_PER_BOARD
    cash_buffer_min: float = _DEFAULT_CASH_BUFFER_MIN
    max_leverage: float = _DEFAULT_MAX_LEVERAGE
    mode: RiskConstraintMode = RiskConstraintMode.RAISE

    def __post_init__(self) -> None:
        # Each weight-style constraint must be a real number in
        # [0, 1]. ``max_leverage`` allows up to 10 (a sane upper
        # bound — anyone running 10x leverage in a long-only A-share
        # backtest has bigger problems than this validator).
        self._check_range("max_per_name", self.max_per_name, 0.0, 1.0)
        self._check_range("max_per_board", self.max_per_board, 0.0, 1.0)
        self._check_range("cash_buffer_min", self.cash_buffer_min, 0.0, 1.0)
        self._check_range("max_leverage", self.max_leverage, 0.0, 10.0)
        if not isinstance(self.mode, RiskConstraintMode):
            raise RiskConstraintError(
                "MinimalRiskConstraints.mode must be a "
                f"RiskConstraintMode, got {type(self.mode).__name__}."
            )

    @staticmethod
    def _check_range(name: str, value: Any, lo: float, hi: float) -> None:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise RiskConstraintError(
                f"MinimalRiskConstraints.{name} must be a real "
                f"number, got {type(value).__name__}."
            )
        # ``nan``/``inf``/``-inf`` silently disable downstream
        # comparisons (``nan < lo`` and ``nan > hi`` are both
        # False, ``inf > hi`` would also dodge the lo branch).
        # Reject non-finite values explicitly so a stray
        # ``float('nan')`` in a config can't dismantle the
        # constraint that's supposed to be the official risk
        # limit. Codex P2 follow-up on PR #179.
        if not math.isfinite(value):
            raise RiskConstraintError(
                f"MinimalRiskConstraints.{name} must be finite "
                f"(not nan / inf / -inf), got {value!r}."
            )
        if value < lo or value > hi:
            raise RiskConstraintError(
                f"MinimalRiskConstraints.{name} must be in "
                f"[{lo}, {hi}], got {value}."
            )

    # ------------------------------------------------------------------
    # apply
    # ------------------------------------------------------------------

    def apply(
        self,
        positions_map: Mapping[str, Mapping[str, float]],
    ) -> RiskConstraintsApplyResult:
        """Validate and (in ``WARN_AND_CLIP`` mode) clip the
        per-day positions map against the four constraints.

        Parameters
        ----------
        positions_map
            ``{date_str: {instrument_code: weight_fraction}}``.
            Weights are fractions of NAV; cash is implicit
            (``cash_share = 1 - sum(weights)``) and the inner
            dict does NOT carry a cash entry. This matches the
            shape produced by
            ``src.core.backtest_runner._positions_to_weight_map``.

        Returns
        -------
        RiskConstraintsApplyResult
            * ``violations`` lists every violation across every day.
            * ``clipped_positions`` is the constraint-respecting
              allocation. Identical to ``positions_map`` if there
              were no violations. In ``RAISE`` mode the engine
              raises BEFORE returning, so callers only see this
              field on the no-violations path.
            * ``was_clipped`` summarises whether any weight moved.

        Raises
        ------
        RiskConstraintError
            ``RAISE`` mode only, when ``violations`` is non-empty.
            Message lists every violation date + constraint +
            actual + limit.
        """
        all_violations: list[RiskConstraintViolation] = []
        clipped_map: dict[str, dict[str, float]] = {}
        moved_any_weight = False

        for date_str, day_weights in positions_map.items():
            day_violations, day_clipped, day_moved = self._apply_day(
                date_str, dict(day_weights),
            )
            all_violations.extend(day_violations)
            clipped_map[date_str] = day_clipped
            moved_any_weight = moved_any_weight or day_moved

        if all_violations and self.mode is RiskConstraintMode.RAISE:
            self._raise_consolidated(all_violations)

        if all_violations and self.mode is RiskConstraintMode.WARN_AND_CLIP:
            for v in all_violations:
                _logger.warning(
                    "MinimalRiskConstraints (mode=WARN_AND_CLIP): "
                    "%s violation on %s for %s — actual=%.4f, "
                    "limit=%.4f. Clipped to limit; overflow "
                    "redistributed to cash. Audit P0-1.",
                    v.constraint_name, v.date, v.instrument_or_bucket,
                    v.actual, v.limit,
                )

        return RiskConstraintsApplyResult(
            violations=tuple(all_violations),
            clipped_positions=clipped_map,
            was_clipped=moved_any_weight,
        )

    # ------------------------------------------------------------------
    # Per-day apply helpers
    # ------------------------------------------------------------------

    def _apply_day(
        self,
        date_str: str,
        weights: dict[str, float],
    ) -> tuple[list[RiskConstraintViolation], dict[str, float], bool]:
        """Apply all four constraints to one day. Returns
        ``(violations, clipped_weights, moved_flag)``.

        Two-phase: detect every violation against the ORIGINAL
        weights first (no mutation), THEN apply the clipping
        ordered pipeline that produces the WARN_AND_CLIP-mode
        ``clipped_positions`` map. Without this split, RAISE mode
        would only report the FIRST violation per-instrument and
        miss downstream ones — e.g. ``{'SH600000': 0.80}`` violates
        BOTH ``max_per_name`` (0.80 > 0.05) AND ``max_per_board``
        (0.80 on SH_Main > 0.40), but if the per-name check
        clipped 0.80 → 0.05 before the per-board check ran, the
        latter would see 0.05 and report nothing. Codex P2
        follow-up on PR #179.

        Clipping order (applied to a separate ``work`` dict):

        1. ``max_per_name`` — per-instrument cap.
        2. ``max_per_board`` — aggregate cap per board bucket.
        3. ``cash_buffer_min`` — ensure cash share >= floor.
        4. ``max_leverage`` — ensure sum(|weight|) <= cap.

        The ordering matters because per-name clipping frees
        weight to cash (only IMPROVES per-board / cash / leverage),
        so running it first keeps the cascade monotonic. The
        cash-buffer scale-all step runs late so it does not undo
        earlier per-name / per-board decisions.
        """
        violations: list[RiskConstraintViolation] = []
        original = dict(weights)
        original_total = sum(original.values())

        # ------------------------------------------------------------------
        # Phase 1: detect every violation against the ORIGINAL snapshot.
        # No mutation — each check sees what the operator actually
        # passed in, so RAISE mode lists every original violation.
        # ------------------------------------------------------------------

        # 1. max_per_name (original).
        for inst, w in original.items():
            if w > self.max_per_name:
                violations.append(RiskConstraintViolation(
                    date=date_str,
                    constraint_name="max_per_name",
                    instrument_or_bucket=inst,
                    actual=float(w),
                    limit=float(self.max_per_name),
                ))

        # 2. max_per_board (original).
        by_board: dict[str, list[str]] = {}
        for inst in original:
            by_board.setdefault(classify_instrument(inst), []).append(inst)
        for board_id, board_insts in by_board.items():
            board_weight = sum(original[i] for i in board_insts)
            if board_weight > self.max_per_board:
                violations.append(RiskConstraintViolation(
                    date=date_str,
                    constraint_name="max_per_board",
                    instrument_or_bucket=board_id,
                    actual=float(board_weight),
                    limit=float(self.max_per_board),
                    details={"contributing_instruments": tuple(board_insts)},
                ))

        # 3. cash_buffer_min (original).
        original_cash = 1.0 - original_total
        if original_cash < self.cash_buffer_min:
            violations.append(RiskConstraintViolation(
                date=date_str,
                constraint_name="cash_buffer_min",
                instrument_or_bucket=_CASH_BUCKET,
                actual=float(original_cash),
                limit=float(self.cash_buffer_min),
            ))

        # 4. max_leverage (original).
        original_abs = sum(abs(w) for w in original.values())
        if original_abs > self.max_leverage:
            violations.append(RiskConstraintViolation(
                date=date_str,
                constraint_name="max_leverage",
                instrument_or_bucket=_PORTFOLIO_BUCKET,
                actual=float(original_abs),
                limit=float(self.max_leverage),
                details={"original_instrument_total": float(original_total)},
            ))

        # ------------------------------------------------------------------
        # Phase 2: apply the clipping pipeline to produce the
        # WARN_AND_CLIP-mode clipped map. RAISE mode never returns
        # this (it raises before reaching the result), but we still
        # compute it so the result type is consistent.
        # ------------------------------------------------------------------
        work = dict(original)
        moved = False

        # 1. max_per_name — clip each over-cap name to the cap.
        for inst, w in list(work.items()):
            if w > self.max_per_name:
                work[inst] = float(self.max_per_name)
                moved = True

        # 2. max_per_board — aggregate by board, scale down over-cap
        #    boards proportionally. The board id is recorded in
        #    phase 1's violation list; here we only need the
        #    grouping to do the scaling.
        for _board_id, board_insts in by_board.items():
            board_weight = sum(work[i] for i in board_insts)
            if board_weight > self.max_per_board:
                scale = float(self.max_per_board) / float(board_weight)
                for inst in board_insts:
                    work[inst] = work[inst] * scale
                moved = True

        # 3. cash_buffer_min — ensure cash share >= floor by
        #    proportionally scaling instrument weights down.
        cash_share = 1.0 - sum(work.values())
        if cash_share < self.cash_buffer_min:
            target_instrument_total = 1.0 - float(self.cash_buffer_min)
            current_total = sum(work.values())
            if current_total > 0:
                scale = target_instrument_total / current_total
                for inst in list(work.keys()):
                    work[inst] = work[inst] * scale
                moved = True

        # 4. max_leverage — sum(|weight|) cap. For long-only this
        #    duplicates cash_buffer_min in spirit; we still check
        #    because (a) future short-supporting strategies, (b)
        #    callers may set max_leverage < (1 - cash_buffer_min).
        total_abs = sum(abs(w) for w in work.values())
        if total_abs > self.max_leverage:
            scale = float(self.max_leverage) / float(total_abs)
            for inst in list(work.keys()):
                work[inst] = work[inst] * scale
            moved = True

        return violations, work, moved

    def _raise_consolidated(
        self,
        violations: list[RiskConstraintViolation],
    ) -> None:
        """Format a single error message listing every violation."""
        lines = [
            f"  - {v.date} {v.constraint_name} "
            f"{v.instrument_or_bucket}: actual={v.actual:.4f}, "
            f"limit={v.limit:.4f}"
            for v in violations
        ]
        # Cap the line count to keep the error readable; the full
        # list is still on the violations tuple if the caller needs
        # programmatic access.
        max_lines = 20
        if len(lines) > max_lines:
            tail = lines[max_lines:]
            lines = lines[:max_lines] + [
                f"  ... and {len(tail)} more violation(s)",
            ]
        raise RiskConstraintError(
            f"MinimalRiskConstraints (mode=RAISE) detected "
            f"{len(violations)} violation(s) in the backtest "
            f"positions map. Audit P0-1.\n"
            + "\n".join(lines)
            + "\n\nRe-run with mode=RiskConstraintMode.WARN_AND_CLIP "
            "to log violations and proceed with a clipped allocation."
        )


# ---------------------------------------------------------------------------
# Legacy fail-closed stub (preserved unchanged from pre-P0-1 contract)
# ---------------------------------------------------------------------------


class RiskConstraintEngine:
    """Fail-closed canonical compatibility surface.

    Pre-dates ``MinimalRiskConstraints`` (audit P0-1). Any call to
    :meth:`apply` raises ``RiskConstraintError``. New code MUST
    use ``MinimalRiskConstraints`` instead; this class is kept
    only so the legacy "calling apply() fails" governance contract
    stays exactly as it was.
    """

    @classmethod
    def apply(cls, *_args: Any, **_kwargs: Any) -> None:
        raise RiskConstraintError(
            "RiskConstraintEngine is the pre-P0-1 fail-closed stub. "
            "Use MinimalRiskConstraints (see "
            "openspec/changes/add-minimal-risk-constraints) instead."
        )


__all__ = (
    "MinimalRiskConstraints",
    "RiskConstraintEngine",
    "RiskConstraintError",
    "RiskConstraintMode",
    "RiskConstraintViolation",
    "RiskConstraintsApplyResult",
)
