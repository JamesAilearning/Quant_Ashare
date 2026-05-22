"""Phase 1 integration smoke test — hand-built 20-day reversal.

Per ``docs/factor_mining/factor_mining_phase1_preflight.md`` §4.5 and
``factor_mining_claude_code_design.md`` §6 (Phase 1 acceptance). The
20-day reversal is also `scale_invariance.md` §5 example 4 — the
canonical "PIT-correct reversal" factor that Phase 2 will evaluate
against real PIT data.

This test exercises the full Phase 1 surface — parse, type-check,
round-trip, pretty-print, structural hash — but does NO data access.
"""

from __future__ import annotations

from src.factor_mining import (
    Expression,
    OperatorCall,
    Terminal,
    parse_expression,
)
from src.factor_mining.grammar import ExprType

REVERSAL_QLIB_STR = "cs_rank(div_safe(ts_delta($close, 20), $close))"


def test_smoke_20day_reversal_full_pipeline():
    # 1. Parse the canonical PIT-correct reversal string.
    expr = parse_expression(REVERSAL_QLIB_STR)

    # 2. Type-check assertion (root = CSF, PURE).
    assert expr.output_type == ExprType("CSF", "PURE")

    # 3. Round-trip through to_dict / from_dict.
    rebuilt = Expression.from_dict(expr.to_dict())
    assert rebuilt == expr
    assert hash(rebuilt) == hash(expr)

    # 4. Pretty-print is stable across round-trip.
    assert rebuilt.to_qlib_string() == REVERSAL_QLIB_STR

    # 5. Type info is preserved.
    assert rebuilt.output_type == expr.output_type

    # 6. Structural shape — root is cs_rank with one child, that child is
    #    div_safe with two children, the first is ts_delta($close, 20).
    assert isinstance(expr, OperatorCall)
    assert expr.op_name == "cs_rank"
    assert len(expr.children) == 1
    inner = expr.children[0]
    assert isinstance(inner, OperatorCall)
    assert inner.op_name == "div_safe"
    assert len(inner.children) == 2
    delta = inner.children[0]
    assert isinstance(delta, OperatorCall)
    assert delta.op_name == "ts_delta"
    assert delta.children == (Terminal("$close"), Terminal("20"))
    assert inner.children[1] == Terminal("$close")


def test_smoke_hand_built_expression_matches_parsed():
    """Manually constructing the same AST and the parsed version must be
    structurally equal and hash to the same value."""
    hand_built = OperatorCall(
        "cs_rank",
        (
            OperatorCall(
                "div_safe",
                (
                    OperatorCall(
                        "ts_delta",
                        (Terminal("$close"), Terminal("20")),
                    ),
                    Terminal("$close"),
                ),
            ),
        ),
    )
    parsed = parse_expression(REVERSAL_QLIB_STR)
    assert hand_built == parsed
    assert hash(hand_built) == hash(parsed)
    assert hand_built.to_qlib_string() == parsed.to_qlib_string()


def test_smoke_round_trip_via_dict_keeps_root_type():
    """A more thorough round-trip including a deeper tree."""
    expr_str = (
        "cs_zscore(div_safe(ts_decay_linear($money, 10), "
        "ts_mean($volume, 20)))"
    )
    expr = parse_expression(expr_str)
    assert expr.output_type == ExprType("CSF", "PURE")
    rebuilt = Expression.from_dict(expr.to_dict())
    assert rebuilt == expr
    assert rebuilt.output_type == expr.output_type
    assert rebuilt.to_qlib_string() == expr_str
