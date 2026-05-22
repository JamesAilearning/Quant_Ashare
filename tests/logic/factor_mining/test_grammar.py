"""Grammar tests: registry contents, random generator, feature universe."""

from __future__ import annotations

import inspect
from random import Random

import pytest

from src.factor_mining.expression import OperatorCall, Terminal
from src.factor_mining.grammar import (
    REGISTRY,
    WINDOW_LITERALS,
    ExprType,
    FeatureRegistry,
    random_expression,
)

# ---------------------------------------------------------------------------
# Operator catalogue invariants
# ---------------------------------------------------------------------------


EXPECTED_OPERATORS = {
    # Arithmetic (4)
    "add", "sub", "mul", "div_safe",
    # Unary (5)
    "neg", "abs", "sign", "log_safe", "sqrt_safe",
    # Time-series — taint-preserving (7)
    "ts_mean", "ts_std", "ts_max", "ts_min", "ts_sum", "ts_delta",
    "ts_decay_linear",
    # Time-series — taint-invariant (6)
    "ts_pctchange", "ts_rank", "ts_argmax", "ts_argmin", "ts_skew", "ts_kurt",
    # Time-series — 3-arg (1)
    "ts_corr",
    # Cross-sectional (4)
    "cs_rank", "cs_zscore", "cs_demean", "cs_winsorize",
    # Conditional (1)
    "where",
}


def test_registry_has_28_operators():
    assert len(REGISTRY.all_operators()) == 28


def test_registry_exact_operator_names():
    assert set(REGISTRY.names()) == EXPECTED_OPERATORS


def test_ts_cov_excluded_from_v1():
    """scale_invariance.md §4: ts_cov is excluded from v1 (cov(a·x, y) =
    a · cov(x, y) re-introduces adj_factor taint)."""
    assert "ts_cov" not in REGISTRY


def test_commutative_flags():
    assert REGISTRY.get("add").commutative is True
    assert REGISTRY.get("mul").commutative is True
    assert REGISTRY.get("sub").commutative is False
    assert REGISTRY.get("div_safe").commutative is False


def test_cs_operators_v1_no_group_by_in_compute():
    """decisions.md D2: v1 cs_* compute functions do not expose group_by.
    The hook is preserved in the design but not surfaced in v1's AST."""
    for op_name in ("cs_rank", "cs_zscore", "cs_demean", "cs_winsorize"):
        op = REGISTRY.get(op_name)
        params = inspect.signature(op.compute_fn).parameters
        assert "group_by" not in params, (
            f"{op_name}.compute_fn must not expose group_by in v1"
        )


# ---------------------------------------------------------------------------
# FeatureRegistry: exactly six fields per D3
# ---------------------------------------------------------------------------


def test_feature_universe_is_exactly_six():
    assert set(FeatureRegistry.V1) == {
        "$open", "$high", "$low", "$close", "$volume", "$money",
    }
    assert len(FeatureRegistry.V1) == 6


def test_feature_universe_raw_price_is_adj_tainted():
    for name in FeatureRegistry.V1_RAW_PRICE:
        assert FeatureRegistry.terminal_type(name) == ExprType(
            "FEATURE", "ADJ_TAINTED"
        )


def test_feature_universe_scale_free_is_pure():
    for name in FeatureRegistry.V1_SCALE_FREE:
        assert FeatureRegistry.terminal_type(name) == ExprType(
            "FEATURE", "PURE"
        )


def test_vwap_is_not_a_terminal():
    """$vwap is expressed as div_safe($money, $volume), not a leaf."""
    from src.factor_mining.grammar import GrammarError

    assert "$vwap" not in FeatureRegistry.V1
    with pytest.raises(GrammarError):
        FeatureRegistry.terminal_type("$vwap")


def test_turn_is_not_a_terminal_in_v1():
    """$turn deferred to v2 pending separate Tushare ingest (D3)."""
    assert "$turn" not in FeatureRegistry.V1
    assert "$turn" in FeatureRegistry.V2_DEFERRED


def test_window_literals_exact():
    assert WINDOW_LITERALS == (5, 10, 20, 40, 60)


# ---------------------------------------------------------------------------
# Random expression generator — the 1000-sample correctness gate
# ---------------------------------------------------------------------------


def test_random_generator_1000_samples_type_valid():
    """1000 random expressions must be 100 % type-valid AND
    scale-pure at the root (output kind=CSF, taint=PURE).

    Acceptance gate per docs/factor_mining/scale_invariance.md §6.2 and
    docs/factor_mining/factor_mining_phase1_preflight.md §6.
    """
    rng = Random(20260523)  # fixed seed for reproducibility
    target = ExprType("CSF", "PURE")
    samples = [
        random_expression(target, max_depth=6, min_depth=2, rng=rng)
        for _ in range(1000)
    ]
    # 100 % must have output type (CSF, PURE)
    bad = [
        (i, s, s.output_type)
        for i, s in enumerate(samples)
        if s.output_type != target
    ]
    assert not bad, (
        f"{len(bad)}/1000 samples have wrong output_type; "
        f"first bad: {bad[0] if bad else None}"
    )


def test_random_generator_min_depth_respected():
    rng = Random(20260523)
    target = ExprType("CSF", "PURE")
    for _ in range(200):
        expr = random_expression(target, max_depth=6, min_depth=2, rng=rng)
        assert expr.depth() >= 2, (
            f"min_depth=2 violated: depth={expr.depth()} expr={expr.to_qlib_string()}"
        )


def test_random_generator_max_depth_respected():
    rng = Random(42)
    target = ExprType("CSF", "PURE")
    for _ in range(200):
        expr = random_expression(target, max_depth=4, min_depth=2, rng=rng)
        assert expr.depth() <= 4, (
            f"max_depth=4 violated: depth={expr.depth()}"
        )


def test_random_generator_deterministic_with_seed():
    rng1 = Random(12345)
    rng2 = Random(12345)
    target = ExprType("CSF", "PURE")
    samples1 = [random_expression(target, 5, 2, rng1) for _ in range(50)]
    samples2 = [random_expression(target, 5, 2, rng2) for _ in range(50)]
    for a, b in zip(samples1, samples2, strict=True):
        assert a == b, (
            f"determinism broken:\n  a={a.to_qlib_string()}\n  b={b.to_qlib_string()}"
        )


def test_random_generator_root_is_cs_operator():
    """Every CSF-target sample's root SHALL be a cs_* operator."""
    rng = Random(101)
    cs_ops = {"cs_rank", "cs_zscore", "cs_demean", "cs_winsorize"}
    for _ in range(200):
        expr = random_expression(
            ExprType("CSF", "PURE"), max_depth=5, min_depth=2, rng=rng
        )
        assert isinstance(expr, OperatorCall)
        assert expr.op_name in cs_ops, (
            f"root must be a cs_* operator; got {expr.op_name}"
        )


def test_random_generator_subexpressions_are_scale_pure():
    """Inside a cs_* root, every direct child is PURE (the gate is enforced
    at construction; if anything slipped through, GrammarError would have
    fired)."""
    rng = Random(7)
    for _ in range(200):
        expr = random_expression(
            ExprType("CSF", "PURE"), max_depth=5, min_depth=2, rng=rng
        )
        assert isinstance(expr, OperatorCall)
        for child in expr.children:
            assert child.output_type.taint == "PURE"


def test_random_generator_max_depth_validation():
    """max_depth < min_depth must raise."""
    with pytest.raises(ValueError, match="max_depth"):
        random_expression(
            ExprType("CSF", "PURE"), max_depth=1, min_depth=2
        )


def test_random_generator_uses_only_six_features():
    """The generator MUST only emit terminals from FeatureRegistry.V1
    or window literals — never $vwap, $turn, or fundamentals."""
    rng = Random(555)
    legal_terminals = set(FeatureRegistry.V1) | {str(n) for n in WINDOW_LITERALS}
    samples = [
        random_expression(
            ExprType("CSF", "PURE"), max_depth=6, min_depth=2, rng=rng
        )
        for _ in range(200)
    ]

    def _collect_terminals(expr) -> set[str]:
        if isinstance(expr, Terminal):
            return {expr.name}
        out: set[str] = set()
        for c in expr.children:
            out |= _collect_terminals(c)
        return out

    seen: set[str] = set()
    for s in samples:
        seen |= _collect_terminals(s)
    illegal = seen - legal_terminals
    assert not illegal, f"generator emitted illegal terminals: {illegal}"
