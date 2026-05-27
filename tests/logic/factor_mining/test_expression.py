"""Expression AST tests: round-trip, structural hash, commutativity."""

from __future__ import annotations

import pytest

from src.factor_mining.expression import (
    Expression,
    OperatorCall,
    Terminal,
    parse_expression,
)
from src.factor_mining.grammar import ExprType, GrammarError

# ---------------------------------------------------------------------------
# Terminal construction
# ---------------------------------------------------------------------------


def test_terminal_known_feature_adj_tainted():
    t = Terminal("$close")
    assert t.output_type == ExprType("FEATURE", "ADJ_TAINTED")


def test_terminal_known_feature_pure():
    for name in ("$volume", "$money"):
        t = Terminal(name)
        assert t.output_type == ExprType("FEATURE", "PURE")


def test_terminal_known_fundamental_pure():
    """daily_basic terminals (extend-feature-universe-with-daily-basic) are PURE."""
    for name in ("$pe", "$pb", "$ps", "$turnover_rate", "$circ_mv", "$total_mv"):
        t = Terminal(name)
        assert t.output_type == ExprType("FEATURE", "PURE")


def test_terminal_unknown_feature_raises():
    with pytest.raises(GrammarError, match="Unknown"):
        Terminal("$vwap")
    # $turn (absolute turnover) stays deferred — $turnover_rate is the
    # v1 daily_basic exposure.
    with pytest.raises(GrammarError):
        Terminal("$turn")
    # TTM ratios and raw share counts still deferred per the
    # extend-feature-universe-with-daily-basic proposal "held back".
    with pytest.raises(GrammarError):
        Terminal("$pe_ttm")
    with pytest.raises(GrammarError):
        Terminal("$float_share")


def test_terminal_window_literal():
    t = Terminal("20")
    assert t.output_type == ExprType("INT_WINDOW", "PURE")


def test_terminal_invalid_window_raises():
    # 7 is not in WINDOW_LITERALS
    with pytest.raises(GrammarError):
        Terminal("7")


def test_terminal_repr():
    assert repr(Terminal("$close")) == "Terminal('$close')"


# ---------------------------------------------------------------------------
# OperatorCall construction (type-check at __post_init__)
# ---------------------------------------------------------------------------


def test_operator_call_known_op():
    op = OperatorCall("add", (Terminal("$volume"), Terminal("$money")))
    assert op.output_type == ExprType("FLOAT", "PURE")


def test_operator_call_unknown_op_raises():
    with pytest.raises(GrammarError, match="Unknown operator"):
        OperatorCall("not_an_op", (Terminal("$close"),))


def test_add_taint_mismatch_raises():
    # $close is ADJ_TAINTED, $volume is PURE → add must reject
    with pytest.raises(GrammarError, match="taint mismatch"):
        OperatorCall("add", (Terminal("$close"), Terminal("$volume")))


def test_div_safe_same_taint_cancels():
    # (ADJ, ADJ) → PURE (same-ticker ratio cancels adj_factor)
    expr = OperatorCall("div_safe", (Terminal("$close"), Terminal("$open")))
    assert expr.output_type == ExprType("FLOAT", "PURE")


def test_div_safe_mixed_taint_stays_adj():
    # (ADJ, PURE) → ADJ
    expr = OperatorCall("div_safe", (Terminal("$close"), Terminal("$volume")))
    assert expr.output_type == ExprType("FLOAT", "ADJ_TAINTED")


def test_mul_pure_pure_is_pure():
    expr = OperatorCall("mul", (Terminal("$volume"), Terminal("$money")))
    assert expr.output_type == ExprType("FLOAT", "PURE")


def test_mul_adj_pure_is_adj():
    expr = OperatorCall("mul", (Terminal("$close"), Terminal("$volume")))
    assert expr.output_type == ExprType("FLOAT", "ADJ_TAINTED")


def test_ts_mean_preserves_taint():
    expr = OperatorCall("ts_mean", (Terminal("$close"), Terminal("20")))
    assert expr.output_type == ExprType("FLOAT", "ADJ_TAINTED")


def test_ts_pctchange_is_always_pure():
    expr = OperatorCall("ts_pctchange", (Terminal("$close"), Terminal("20")))
    assert expr.output_type == ExprType("FLOAT", "PURE")


def test_cs_rank_rejects_adj_tainted():
    with pytest.raises(GrammarError, match="PURE input"):
        OperatorCall("cs_rank", (Terminal("$close"),))


def test_cs_rank_accepts_pure():
    expr = OperatorCall("cs_rank", (Terminal("$volume"),))
    assert expr.output_type == ExprType("CSF", "PURE")


def test_ts_mean_requires_int_window_in_second_slot():
    # $volume is FEATURE/PURE, not INT_WINDOW
    with pytest.raises(GrammarError, match="INT_WINDOW"):
        OperatorCall("ts_mean", (Terminal("$close"), Terminal("$volume")))


def test_arity_mismatch_raises():
    # add takes 2 args
    with pytest.raises(GrammarError, match="expected 2 args"):
        OperatorCall("add", (Terminal("$volume"),))


# ---------------------------------------------------------------------------
# Round-trip serialisation
# ---------------------------------------------------------------------------


def _sample_expr() -> Expression:
    return OperatorCall(
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


def test_to_dict_from_dict_round_trip():
    expr = _sample_expr()
    rebuilt = Expression.from_dict(expr.to_dict())
    assert rebuilt == expr


def test_round_trip_preserves_hash():
    expr = _sample_expr()
    rebuilt = Expression.from_dict(expr.to_dict())
    assert hash(rebuilt) == hash(expr)


def test_round_trip_preserves_qlib_string():
    expr = _sample_expr()
    rebuilt = Expression.from_dict(expr.to_dict())
    assert rebuilt.to_qlib_string() == expr.to_qlib_string()


def test_round_trip_preserves_output_type():
    expr = _sample_expr()
    rebuilt = Expression.from_dict(expr.to_dict())
    assert rebuilt.output_type == expr.output_type


# ---------------------------------------------------------------------------
# Structural hash + commutativity
# ---------------------------------------------------------------------------


def test_commutative_hash_add():
    a = OperatorCall("add", (Terminal("$volume"), Terminal("$money")))
    b = OperatorCall("add", (Terminal("$money"), Terminal("$volume")))
    assert hash(a) == hash(b)
    assert a == b


def test_commutative_hash_mul():
    a = OperatorCall("mul", (Terminal("$volume"), Terminal("$money")))
    b = OperatorCall("mul", (Terminal("$money"), Terminal("$volume")))
    assert hash(a) == hash(b)
    assert a == b


def test_non_commutative_hash_sub():
    a = OperatorCall("sub", (Terminal("$volume"), Terminal("$money")))
    b = OperatorCall("sub", (Terminal("$money"), Terminal("$volume")))
    # Order matters for sub
    assert hash(a) != hash(b)
    assert a != b


def test_non_commutative_hash_div_safe():
    a = OperatorCall("div_safe", (Terminal("$volume"), Terminal("$money")))
    b = OperatorCall("div_safe", (Terminal("$money"), Terminal("$volume")))
    assert hash(a) != hash(b)
    assert a != b


def test_commutative_hash_nested():
    """Commutative-equality holds at deeper nesting too."""
    inner_a = OperatorCall("add", (Terminal("$volume"), Terminal("$money")))
    inner_b = OperatorCall("add", (Terminal("$money"), Terminal("$volume")))
    outer_a = OperatorCall(
        "mul", (inner_a, OperatorCall("ts_pctchange", (Terminal("$close"), Terminal("5"))))
    )
    outer_b = OperatorCall(
        "mul", (OperatorCall("ts_pctchange", (Terminal("$close"), Terminal("5"))), inner_b)
    )
    assert hash(outer_a) == hash(outer_b)
    assert outer_a == outer_b


def test_hash_stable_for_terminal():
    a = Terminal("$close")
    b = Terminal("$close")
    assert hash(a) == hash(b)
    assert a == b


def test_terminal_inequality():
    assert Terminal("$close") != Terminal("$open")


def test_operatorcall_inequality_different_op():
    a = OperatorCall("add", (Terminal("$volume"), Terminal("$money")))
    b = OperatorCall("sub", (Terminal("$volume"), Terminal("$money")))
    assert a != b


def test_operatorcall_inequality_different_children():
    a = OperatorCall("add", (Terminal("$volume"), Terminal("$money")))
    b = OperatorCall("add", (Terminal("$volume"), Terminal("$volume")))
    assert a != b


# ---------------------------------------------------------------------------
# Depth
# ---------------------------------------------------------------------------


def test_depth_terminal():
    assert Terminal("$close").depth() == 0


def test_depth_single_op():
    expr = OperatorCall("cs_rank", (Terminal("$volume"),))
    assert expr.depth() == 1


def test_depth_nested():
    expr = _sample_expr()
    # cs_rank → div_safe → ts_delta → terminal
    assert expr.depth() == 3


# ---------------------------------------------------------------------------
# to_qlib_string
# ---------------------------------------------------------------------------


def test_qlib_string_terminal():
    assert Terminal("$close").to_qlib_string() == "$close"
    assert Terminal("20").to_qlib_string() == "20"


def test_qlib_string_operator_call():
    expr = _sample_expr()
    assert expr.to_qlib_string() == (
        "cs_rank(div_safe(ts_delta($close, 20), $close))"
    )


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parse_terminal_feature():
    assert parse_expression("$close") == Terminal("$close")


def test_parse_terminal_window():
    assert parse_expression("20") == Terminal("20")


def test_parse_single_op():
    expr = parse_expression("cs_rank($volume)")
    assert expr == OperatorCall("cs_rank", (Terminal("$volume"),))


def test_parse_nested_op():
    expr = parse_expression("cs_rank(div_safe(ts_delta($close, 20), $close))")
    assert expr == _sample_expr()


def test_parse_round_trip_qlib_string():
    expr = _sample_expr()
    rebuilt = parse_expression(expr.to_qlib_string())
    assert rebuilt == expr
    assert rebuilt.to_qlib_string() == expr.to_qlib_string()


def test_parse_rejects_unknown_op():
    with pytest.raises(GrammarError, match="Unknown operator"):
        parse_expression("not_a_real_op($close)")


def test_parse_rejects_ill_typed_expression():
    # cs_rank($close) — ADJ_TAINTED input to cs_rank
    with pytest.raises(GrammarError, match="PURE input"):
        parse_expression("cs_rank($close)")


def test_parse_rejects_unbalanced_parens():
    with pytest.raises(GrammarError):
        parse_expression("cs_rank($volume")


def test_parse_rejects_trailing_tokens():
    with pytest.raises(GrammarError, match="trailing"):
        parse_expression("$close)")
