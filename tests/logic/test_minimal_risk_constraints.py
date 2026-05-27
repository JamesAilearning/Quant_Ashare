"""Unit tests for ``MinimalRiskConstraints``.

Covers the four constraints (max_per_name, max_per_board,
cash_buffer_min, max_leverage) × the two enforcement modes
(RAISE, WARN_AND_CLIP) per the OpenSpec design under
``openspec/changes/add-minimal-risk-constraints``.

Audit P0-1.
"""

from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.risk_constraints import (  # noqa: E402
    MinimalRiskConstraints,
    RiskConstraintError,
    RiskConstraintMode,
    RiskConstraintsApplyResult,
    RiskConstraintViolation,
)


# A two-day positions map keyed by a couple of A-share boards:
# SH600000  (Shanghai Main, banks)
# SH600001  (Shanghai Main, oil/gas — same board bucket)
# SZ000001  (Shenzhen Main, banks)
# SZ300001  (ChiNext, growth)
def _day(weights: dict[str, float]) -> dict[str, float]:
    return dict(weights)


class ConstructorValidationTests(unittest.TestCase):
    def test_defaults_match_documented_profile(self) -> None:
        c = MinimalRiskConstraints()
        self.assertEqual(c.max_per_name, 0.05)
        self.assertEqual(c.max_per_board, 0.40)
        self.assertEqual(c.cash_buffer_min, 0.01)
        self.assertEqual(c.max_leverage, 1.00)
        self.assertEqual(c.mode, RiskConstraintMode.RAISE)

    def test_rejects_negative_max_per_name(self) -> None:
        with self.assertRaisesRegex(RiskConstraintError, "max_per_name"):
            MinimalRiskConstraints(max_per_name=-0.05)

    def test_rejects_max_per_name_above_one(self) -> None:
        with self.assertRaisesRegex(RiskConstraintError, "max_per_name"):
            MinimalRiskConstraints(max_per_name=1.5)

    def test_rejects_bool_max_per_name(self) -> None:
        # bool is a subclass of int; reject so ``max_per_name=True``
        # doesn't silently become 1.0 ("100% cap").
        with self.assertRaisesRegex(RiskConstraintError, "max_per_name"):
            MinimalRiskConstraints(max_per_name=True)  # type: ignore[arg-type]

    def test_rejects_string_mode(self) -> None:
        with self.assertRaisesRegex(RiskConstraintError, "mode"):
            MinimalRiskConstraints(mode="raise")  # type: ignore[arg-type]

    def test_rejects_max_leverage_above_cap(self) -> None:
        with self.assertRaisesRegex(RiskConstraintError, "max_leverage"):
            MinimalRiskConstraints(max_leverage=20.0)

    def test_rejects_negative_cash_buffer_min(self) -> None:
        with self.assertRaisesRegex(RiskConstraintError, "cash_buffer_min"):
            MinimalRiskConstraints(cash_buffer_min=-0.01)

    def test_rejects_nan_constraint_values(self) -> None:
        """Codex P2 follow-up on PR #179.

        ``float('nan')`` returns False for both ``nan < lo`` and
        ``nan > hi``, so a bare range check lets it through and the
        downstream constraint comparison silently disables (``w >
        nan`` is False for every ``w``). Reject non-finite values
        at the constructor so a stray nan in a config can't
        dismantle the official risk limit.
        """
        nan = float("nan")
        for field_name, kwargs in [
            ("max_per_name", {"max_per_name": nan}),
            ("max_per_board", {"max_per_board": nan}),
            ("cash_buffer_min", {"cash_buffer_min": nan}),
            ("max_leverage", {"max_leverage": nan}),
        ]:
            with self.subTest(field=field_name):
                with self.assertRaisesRegex(
                    RiskConstraintError, "finite",
                ):
                    MinimalRiskConstraints(**kwargs)  # type: ignore[arg-type]

    def test_rejects_inf_constraint_values(self) -> None:
        """``inf``/``-inf`` also silently disables comparison
        semantics (``inf > hi`` is True so it'd hit the range
        check, but ``-inf < lo`` is also True so the diagnostic
        is misleading; reject up front so the message is honest:
        'must be finite').
        """
        for field_name, value in [
            ("max_per_name", float("inf")),
            ("max_leverage", float("-inf")),
        ]:
            with self.subTest(field=field_name, value=value):
                with self.assertRaisesRegex(
                    RiskConstraintError, "finite",
                ):
                    MinimalRiskConstraints(**{field_name: value})  # type: ignore[arg-type]


class MaxPerNameTests(unittest.TestCase):
    """Single-instrument cap. Default 5%."""

    def test_below_cap_passes_silently(self) -> None:
        c = MinimalRiskConstraints()  # cap=0.05
        result = c.apply({"2024-01-02": _day({"SH600000": 0.04, "SH600001": 0.04})})
        self.assertEqual(result.violations, tuple())
        self.assertFalse(result.was_clipped)
        # No-op: clipped_positions equals input.
        self.assertEqual(
            result.clipped_positions["2024-01-02"],
            {"SH600000": 0.04, "SH600001": 0.04},
        )

    def test_exceeds_cap_raise_mode(self) -> None:
        c = MinimalRiskConstraints()
        with self.assertRaisesRegex(RiskConstraintError, "max_per_name"):
            c.apply({"2024-01-02": _day({"SH600000": 0.08, "SH600001": 0.04})})

    def test_exceeds_cap_warn_and_clip_mode(self) -> None:
        c = MinimalRiskConstraints(mode=RiskConstraintMode.WARN_AND_CLIP)
        with self.assertLogs("src.core.risk_constraints", level="WARNING") as captured:
            result = c.apply(
                {"2024-01-02": _day({"SH600000": 0.08, "SH600001": 0.04})},
            )
        self.assertEqual(len(result.violations), 1)
        v = result.violations[0]
        self.assertEqual(v.constraint_name, "max_per_name")
        self.assertEqual(v.instrument_or_bucket, "SH600000")
        self.assertAlmostEqual(v.actual, 0.08, places=6)
        self.assertAlmostEqual(v.limit, 0.05, places=6)
        # Clipped: SH600000 capped at 0.05.
        self.assertAlmostEqual(
            result.clipped_positions["2024-01-02"]["SH600000"], 0.05, places=6,
        )
        # Other name unchanged.
        self.assertAlmostEqual(
            result.clipped_positions["2024-01-02"]["SH600001"], 0.04, places=6,
        )
        self.assertTrue(result.was_clipped)
        # One WARN per violation.
        warns = [r for r in captured.records if r.levelno == logging.WARNING]
        self.assertEqual(len(warns), 1)
        self.assertIn("max_per_name", warns[0].getMessage())


class MaxPerBoardTests(unittest.TestCase):
    """Aggregate per-board cap. Default 40%."""

    def test_below_cap_passes(self) -> None:
        c = MinimalRiskConstraints()  # board cap=0.40
        # Three SH names totalling 12% — well under 40%.
        result = c.apply({"2024-01-02": _day({
            "SH600000": 0.04,
            "SH600001": 0.04,
            "SH600002": 0.04,
        })})
        self.assertEqual(result.violations, tuple())

    def test_exceeds_cap_raise_mode(self) -> None:
        c = MinimalRiskConstraints()
        # 10 SH-main names at 5% each → 50% on the SH-main board.
        weights = {f"SH60{i:04d}": 0.05 for i in range(10)}
        with self.assertRaisesRegex(RiskConstraintError, "max_per_board"):
            c.apply({"2024-01-02": _day(weights)})

    def test_exceeds_cap_warn_and_clip_mode_scales_proportionally(self) -> None:
        c = MinimalRiskConstraints(mode=RiskConstraintMode.WARN_AND_CLIP)
        # 10 SH-main names at 5% each → 50% total. Cap=0.40 → scale=0.8 each.
        weights = {f"SH60{i:04d}": 0.05 for i in range(10)}
        result = c.apply({"2024-01-02": _day(weights)})
        # One per-board violation. (No per-name violations because
        # each is at 0.05 which equals the cap, not above.)
        per_board = [
            v for v in result.violations if v.constraint_name == "max_per_board"
        ]
        self.assertEqual(len(per_board), 1)
        self.assertEqual(per_board[0].instrument_or_bucket, "board_SH_Main")
        self.assertAlmostEqual(per_board[0].actual, 0.50, places=6)
        self.assertAlmostEqual(per_board[0].limit, 0.40, places=6)
        # After clipping each name should be 0.05 × 0.8 = 0.04.
        for name in weights:
            self.assertAlmostEqual(
                result.clipped_positions["2024-01-02"][name], 0.04, places=6,
            )


class CashBufferMinTests(unittest.TestCase):
    """Minimum cash share. Default 1%."""

    def test_above_floor_passes(self) -> None:
        c = MinimalRiskConstraints()  # cash_buffer_min=0.01
        # Two names at 0.04 each on different boards → all four
        # constraints (per-name=0.05, per-board=0.40,
        # cash_buffer_min=0.01, leverage=1.0) are satisfied.
        result = c.apply({"2024-01-02": _day({"SH600000": 0.04, "SZ300001": 0.04})})
        cash_violations = [
            v for v in result.violations if v.constraint_name == "cash_buffer_min"
        ]
        self.assertEqual(cash_violations, [])
        # Sanity: the whole result is clean.
        self.assertEqual(result.violations, tuple())

    def test_below_floor_raise_mode(self) -> None:
        # Use a custom config with stricter cash_buffer_min so we
        # can trip it without first triggering the per-name cap.
        c = MinimalRiskConstraints(
            cash_buffer_min=0.10,
            max_per_name=0.99,  # disable so we test cash floor alone
            max_per_board=0.99,
            max_leverage=10.0,
        )
        # Sum=0.95 → cash=0.05, below floor 0.10.
        with self.assertRaisesRegex(RiskConstraintError, "cash_buffer_min"):
            c.apply({"2024-01-02": _day({"SH600000": 0.50, "SZ000001": 0.45})})

    def test_below_floor_warn_and_clip_scales_all_down(self) -> None:
        c = MinimalRiskConstraints(
            cash_buffer_min=0.10,
            max_per_name=0.99,
            max_per_board=0.99,
            max_leverage=10.0,
            mode=RiskConstraintMode.WARN_AND_CLIP,
        )
        result = c.apply(
            {"2024-01-02": _day({"SH600000": 0.50, "SZ000001": 0.45})},
        )
        clipped = result.clipped_positions["2024-01-02"]
        # After clip, sum of weights should be 1 - 0.10 = 0.90,
        # with each name scaled proportionally.
        total = sum(clipped.values())
        self.assertAlmostEqual(total, 0.90, places=6)
        # Original ratio 50:45 preserved.
        self.assertAlmostEqual(
            clipped["SH600000"] / clipped["SZ000001"],
            0.50 / 0.45,
            places=6,
        )


class MaxLeverageTests(unittest.TestCase):
    """Sum of absolute weights cap. Default 1.0."""

    def test_below_cap_passes(self) -> None:
        c = MinimalRiskConstraints()
        result = c.apply({"2024-01-02": _day({"SH600000": 0.04, "SZ000001": 0.04})})
        leverage_violations = [
            v for v in result.violations if v.constraint_name == "max_leverage"
        ]
        self.assertEqual(leverage_violations, [])

    def test_exceeds_cap_warn_and_clip_scales_all_down(self) -> None:
        # Test ``max_leverage`` in isolation. Strategy: pick weights
        # that pass all other caps and where ``cash_buffer_min`` is
        # already satisfied — so the only constraint that fires is
        # ``max_leverage``. With max_per_name=0.6, max_per_board=1.0
        # (effectively disabled for per-board because each name is
        # on a different board), cash_buffer_min=0.0, and
        # max_leverage=0.8, two names at 0.5 each (sum=1.0, cash=0)
        # don't trip per-name (0.5<0.6), per-board (each board=0.5),
        # or cash_buffer_min (0=0). Then leverage trips: sum=1.0 >
        # 0.8 → scale by 0.8 → each name becomes 0.4.
        c = MinimalRiskConstraints(
            max_per_name=0.6,
            max_per_board=1.0,
            cash_buffer_min=0.0,
            max_leverage=0.8,
            mode=RiskConstraintMode.WARN_AND_CLIP,
        )
        result = c.apply({"2024-01-02": _day({
            "SH600000": 0.5, "SZ300001": 0.5,  # different boards
        })})
        leverage_violations = [
            v for v in result.violations if v.constraint_name == "max_leverage"
        ]
        self.assertEqual(len(leverage_violations), 1)
        self.assertAlmostEqual(leverage_violations[0].actual, 1.0, places=6)
        self.assertAlmostEqual(leverage_violations[0].limit, 0.8, places=6)
        for name in ("SH600000", "SZ300001"):
            self.assertAlmostEqual(
                result.clipped_positions["2024-01-02"][name],
                0.5 * (0.8 / 1.0),
                places=6,
            )


class MultipleConstraintsTests(unittest.TestCase):

    def test_single_oversize_name_reports_both_per_name_and_per_board(self) -> None:
        """Codex P2 follow-up on PR #179.

        A single oversize position on one board should produce BOTH
        a ``max_per_name`` violation AND a ``max_per_board``
        violation. With defaults (per-name=0.05, per-board=0.40),
        ``{'SH600000': 0.80}`` violates both. The previous one-phase
        ``apply()`` clipped per-name (0.80 → 0.05) before the
        per-board check ran, so the per-board check saw 0.05 (under
        0.40) and reported nothing — silently violating the RAISE-mode
        contract to collect every violation across the snapshot.
        """
        c = MinimalRiskConstraints(mode=RiskConstraintMode.WARN_AND_CLIP)
        result = c.apply({"2024-01-02": _day({"SH600000": 0.80})})
        names = {v.constraint_name for v in result.violations}
        self.assertIn("max_per_name", names)
        self.assertIn("max_per_board", names)
        # Both violations carry the ORIGINAL value, not the
        # post-clip value.
        per_name = next(
            v for v in result.violations if v.constraint_name == "max_per_name"
        )
        per_board = next(
            v for v in result.violations if v.constraint_name == "max_per_board"
        )
        self.assertAlmostEqual(per_name.actual, 0.80, places=6)
        self.assertAlmostEqual(per_board.actual, 0.80, places=6)

    def test_mixed_violations_all_listed_in_raise_message(self) -> None:
        """Mixed violations on the same day must ALL surface in the
        consolidated RAISE message — engine MUST NOT short-circuit
        on the first violation."""
        c = MinimalRiskConstraints()  # all defaults
        # SH600000 = 0.08 → per-name violation
        # 10 SH-main at 0.05 each = 0.50 → per-board violation
        weights = {f"SH60{i:04d}": 0.05 for i in range(10)}
        weights["SH600000"] = 0.08  # bump to trip per-name too
        with self.assertRaises(RiskConstraintError) as ctx:
            c.apply({"2024-01-02": _day(weights)})
        msg = str(ctx.exception)
        self.assertIn("max_per_name", msg)
        self.assertIn("max_per_board", msg)

    def test_violations_across_multiple_days_all_listed(self) -> None:
        c = MinimalRiskConstraints()
        positions = {
            "2024-01-02": _day({"SH600000": 0.08}),
            "2024-01-03": _day({"SH600001": 0.09}),
            "2024-01-04": _day({"SH600002": 0.10}),
        }
        with self.assertRaises(RiskConstraintError) as ctx:
            c.apply(positions)
        msg = str(ctx.exception)
        for d in ("2024-01-02", "2024-01-03", "2024-01-04"):
            self.assertIn(d, msg)


class EdgeCaseTests(unittest.TestCase):
    def test_empty_positions_map_is_no_op(self) -> None:
        c = MinimalRiskConstraints()
        result = c.apply({})
        self.assertEqual(result.violations, tuple())
        self.assertEqual(result.clipped_positions, {})
        self.assertFalse(result.was_clipped)

    def test_empty_day_weights_is_no_op(self) -> None:
        c = MinimalRiskConstraints(cash_buffer_min=0.0)
        result = c.apply({"2024-01-02": {}})
        self.assertEqual(result.violations, tuple())
        self.assertEqual(result.clipped_positions["2024-01-02"], {})

    def test_zero_weights_below_floor_is_no_op_when_floor_zero(self) -> None:
        # With cash_buffer_min=0.0 and zero weights, cash = 1.0 ≥ 0
        # → no violation.
        c = MinimalRiskConstraints(cash_buffer_min=0.0)
        result = c.apply({"2024-01-02": _day({"SH600000": 0.0})})
        self.assertEqual(result.violations, tuple())


class ApplyResultShapeTests(unittest.TestCase):
    def test_result_is_frozen_dataclass(self) -> None:
        c = MinimalRiskConstraints()
        result = c.apply({"2024-01-02": _day({"SH600000": 0.04})})
        self.assertIsInstance(result, RiskConstraintsApplyResult)
        # ``frozen=True`` — direct attribute assignment must fail.
        with self.assertRaises(AttributeError):  # frozen dataclass
            result.violations = ()  # type: ignore[misc]

    def test_violation_is_frozen_dataclass(self) -> None:
        c = MinimalRiskConstraints(mode=RiskConstraintMode.WARN_AND_CLIP)
        result = c.apply({"2024-01-02": _day({"SH600000": 0.08})})
        v = result.violations[0]
        self.assertIsInstance(v, RiskConstraintViolation)
        with self.assertRaises(AttributeError):  # frozen dataclass
            v.actual = 0.0  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
