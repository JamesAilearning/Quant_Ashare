"""Pinned scale-invariance pass/fail tests.

These pin the eight examples from
``docs/factor_mining/scale_invariance.md`` §5 plus the three additional
rejected forms enumerated below the table. Adding a new operator
without updating this file is a code smell (per ``scale_invariance.md``
§6.4 — the §5 table is the source of truth that the grammar matches
operator authors' intent).
"""

from __future__ import annotations

import pytest

from src.factor_mining.expression import parse_expression
from src.factor_mining.grammar import ExprType, FeatureRegistry, GrammarError

# ---------------------------------------------------------------------------
# Terminal taint table (§3)
# ---------------------------------------------------------------------------


def test_terminal_taint_table_raw_price():
    for name in ("$open", "$high", "$low", "$close"):
        assert FeatureRegistry.terminal_type(name) == ExprType(
            "FEATURE", "ADJ_TAINTED"
        )


def test_terminal_taint_table_scale_free():
    for name in ("$volume", "$money"):
        assert FeatureRegistry.terminal_type(name) == ExprType(
            "FEATURE", "PURE"
        )


# ---------------------------------------------------------------------------
# The 8 pinned pass / fail examples from §5
# ---------------------------------------------------------------------------
#
# Verbatim quote (§5 table):
#
#   | # | Expression                                                    | Verdict |
#   | 1 | cs_rank(ts_pctchange($close, 20))                             | PASS    |
#   | 2 | cs_rank($close)                                               | FAIL    |
#   | 3 | cs_rank(ts_delta($close, 20))                                 | FAIL    |
#   | 4 | cs_rank(div_safe(ts_delta($close, 20), $close))               | PASS    |
#   | 5 | cs_zscore($volume)                                            | PASS    |
#   | 6 | cs_rank(div_safe($money, $volume))                            | PASS    |
#   | 7 | cs_rank(log_safe($close))                                     | FAIL    |
#   | 8 | cs_rank(log_safe(div_safe($close, Ref($close, 20))))          | PASS    |
#
# Example 8 uses ``Ref($close, 20)`` which is qlib's name for
# ``ts_delta`` style lookback. Our v1 operator name is ``ts_delta``;
# the equivalent expression is
# ``cs_rank(log_safe(div_safe($close, ts_delta($close, 20))))`` —
# but ``ts_delta`` is ``x - x.shift(n)`` (a delta), NOT
# ``x.shift(n)`` (a lag). The v1 grammar does not expose a pure
# lag operator. To preserve the intent of §5 example 8 (a
# log-of-ratio over a same-ticker lag), the test uses the closer
# equivalent ``div_safe($close, ts_delta($close, 20))`` whose
# numerator is ADJ_TAINTED ($close) and denominator is ADJ_TAINTED
# (ts_delta preserves taint), so the inner div_safe cancels to
# PURE — same intent as the §5 example.

EXAMPLE_1_PASS = "cs_rank(ts_pctchange($close, 20))"
EXAMPLE_2_FAIL = "cs_rank($close)"
EXAMPLE_3_FAIL = "cs_rank(ts_delta($close, 20))"
EXAMPLE_4_PASS = "cs_rank(div_safe(ts_delta($close, 20), $close))"
EXAMPLE_5_PASS = "cs_zscore($volume)"
EXAMPLE_6_PASS = "cs_rank(div_safe($money, $volume))"
EXAMPLE_7_FAIL = "cs_rank(log_safe($close))"
EXAMPLE_8_PASS = "cs_rank(log_safe(div_safe($close, ts_delta($close, 20))))"


@pytest.mark.parametrize(
    "expr_str",
    [
        EXAMPLE_1_PASS,
        EXAMPLE_4_PASS,
        EXAMPLE_5_PASS,
        EXAMPLE_6_PASS,
        EXAMPLE_8_PASS,
    ],
)
def test_pinned_pass_examples(expr_str):
    expr = parse_expression(expr_str)
    assert expr.output_type == ExprType("CSF", "PURE"), (
        f"{expr_str} should produce (CSF, PURE), got {expr.output_type}"
    )


@pytest.mark.parametrize(
    "expr_str",
    [
        EXAMPLE_2_FAIL,
        EXAMPLE_3_FAIL,
        EXAMPLE_7_FAIL,
    ],
)
def test_pinned_fail_examples(expr_str):
    with pytest.raises(GrammarError):
        parse_expression(expr_str)


# Specific failure-reason assertions — keep these in case the message
# text is depended on by Phase 2+ for triage UX.


def test_example_2_close_is_adj_tainted_at_cs_gate():
    """cs_rank($close): $close is ADJ_TAINTED; cs_rank requires PURE."""
    with pytest.raises(GrammarError, match="PURE input"):
        parse_expression(EXAMPLE_2_FAIL)


def test_example_3_ts_delta_preserves_taint():
    """cs_rank(ts_delta($close, 20)): ts_delta preserves taint → ADJ_TAINTED
    at the cs_rank gate."""
    with pytest.raises(GrammarError, match="PURE input"):
        parse_expression(EXAMPLE_3_FAIL)


def test_example_7_log_safe_preserves_taint():
    """cs_rank(log_safe($close)): log_safe preserves taint."""
    with pytest.raises(GrammarError, match="PURE input"):
        parse_expression(EXAMPLE_7_FAIL)


# ---------------------------------------------------------------------------
# Additional rejected forms enumerated below the §5 table
# ---------------------------------------------------------------------------


def test_rejected_cs_rank_add_close_open_taint_match_fails_at_add():
    """add($close, $open) — both ADJ → ADJ. But (ADJ, ADJ) is legal for add
    (matching taints; output ADJ). Wrapping in cs_rank then fails at the gate."""
    with pytest.raises(GrammarError, match="PURE input"):
        parse_expression("cs_rank(add($close, $open))")


def test_rejected_cs_rank_mul_close_volume_at_add():
    """mul($close, $volume): (ADJ, PURE) → ADJ. Then cs_rank rejects."""
    with pytest.raises(GrammarError, match="PURE input"):
        parse_expression("cs_rank(mul($close, $volume))")


def test_rejected_cs_rank_ts_mean_close():
    """ts_mean preserves ADJ_TAINTED. cs_rank rejects."""
    with pytest.raises(GrammarError, match="PURE input"):
        parse_expression("cs_rank(ts_mean($close, 20))")


# ---------------------------------------------------------------------------
# Add / sub strict-match rule: (PURE, ADJ) is ILLEGAL at the add itself
# (not at cs_rank) — units error caught at construction.
# ---------------------------------------------------------------------------


def test_add_pure_adj_illegal_at_construction():
    """add($volume, $close): (PURE, ADJ) → ILLEGAL at the add (units error
    per scale_invariance.md §4)."""
    with pytest.raises(GrammarError, match="taint mismatch"):
        parse_expression("add($volume, $close)")


def test_sub_pure_adj_illegal_at_construction():
    with pytest.raises(GrammarError, match="taint mismatch"):
        parse_expression("sub($volume, $close)")


# ---------------------------------------------------------------------------
# div_safe (ADJ, ADJ) → PURE — the ONLY way price-derived becomes scale-free
# ---------------------------------------------------------------------------


def test_div_safe_adj_adj_cancels_to_pure():
    expr = parse_expression("div_safe($close, $open)")
    assert expr.output_type == ExprType("FLOAT", "PURE")


def test_cs_rank_of_div_safe_adj_adj_passes():
    expr = parse_expression("cs_rank(div_safe($close, $open))")
    assert expr.output_type == ExprType("CSF", "PURE")


# ---------------------------------------------------------------------------
# ts_invariant ops: always PURE regardless of input taint
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "op",
    ["ts_pctchange", "ts_rank", "ts_argmax", "ts_argmin", "ts_skew", "ts_kurt"],
)
def test_ts_invariant_yields_pure_even_for_adj_input(op):
    expr = parse_expression(f"{op}($close, 20)")
    assert expr.output_type == ExprType("FLOAT", "PURE"), (
        f"{op}($close, 20) must yield PURE per scale_invariance.md §4"
    )


def test_ts_corr_yields_pure_even_for_adj_inputs():
    expr = parse_expression("ts_corr($close, $open, 20)")
    assert expr.output_type == ExprType("FLOAT", "PURE")
