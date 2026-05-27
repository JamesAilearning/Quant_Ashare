"""Type system, operator registry, and random expression generator.

Implements the normative type rules from
``docs/factor_mining/scale_invariance.md`` §3–§4 (cited as the
source-of-truth for taint propagation). The expression-construction
type gate (``cs_*`` rejects ``ADJ_TAINTED``) and the
random-expression generator (1000 samples, 100 % type-valid AND
scale-pure, ``min_depth=2``) both flow from this module.

This module has NO dependency on data access. It does not import
qlib, ``src.pit``, or any IO library beyond the compute functions in
``operators``. Phase 1's strict data gate D5 (see
``docs/factor_mining/decisions.md``) is satisfied trivially.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass
from random import Random
from typing import Literal

from . import operators as _ops

# ---------------------------------------------------------------------------
# Type system (per scale_invariance.md §3)
# ---------------------------------------------------------------------------

OutputKind = Literal["FEATURE", "FLOAT", "INT_WINDOW", "SCALAR", "CSF"]
ScaleTaint = Literal["PURE", "ADJ_TAINTED"]


@dataclass(frozen=True)
class ExprType:
    """The kind × taint type of an expression node."""

    kind: OutputKind
    taint: ScaleTaint = "PURE"


class GrammarError(ValueError):
    """Raised when an expression violates the grammar's type rules."""


# ---------------------------------------------------------------------------
# Feature universe (decisions.md D3 — six PIT bin fields)
# ---------------------------------------------------------------------------


class FeatureRegistry:
    """The v1 terminal feature universe.

    Exactly the six fields present in the PIT qlib bins per
    ``decisions.md`` D3. ``$vwap`` is a derived expression
    (``div_safe($money, $volume)``) and is NOT a terminal. ``$turn``
    is deferred to v2 pending separate Tushare ``daily_basic`` ingest.
    """

    V1_RAW_PRICE: tuple[str, ...] = ("$open", "$high", "$low", "$close")
    V1_SCALE_FREE: tuple[str, ...] = ("$volume", "$money")
    V1: tuple[str, ...] = V1_RAW_PRICE + V1_SCALE_FREE

    # Reserved for v2 (NOT enabled in Phase 1)
    V2_DEFERRED: tuple[str, ...] = ("$turn", "$pe", "$pb", "$ps", "$mktcap")

    @classmethod
    def current(cls) -> tuple[str, ...]:
        return cls.V1

    @classmethod
    def is_feature(cls, name: str) -> bool:
        return name in cls.V1

    @classmethod
    def terminal_type(cls, name: str) -> ExprType:
        if name in cls.V1_RAW_PRICE:
            return ExprType("FEATURE", "ADJ_TAINTED")
        if name in cls.V1_SCALE_FREE:
            return ExprType("FEATURE", "PURE")
        raise GrammarError(f"Unknown terminal feature: {name!r}")


# Integer window literals allowed as the second arg of ts_* operators.
WINDOW_LITERALS: tuple[int, ...] = (5, 10, 20, 40, 60)

# Floating scalar literals reserved for v2; v1 has no operator that
# consumes a scalar terminal, so the generator never picks one.
SCALAR_LITERALS: tuple[float, ...] = (0.5, 1.0, 2.0)


# ---------------------------------------------------------------------------
# Type-check helpers
# ---------------------------------------------------------------------------


def _is_float_like(t: ExprType) -> bool:
    """``T_FEATURE`` coerces to ``T_FLOAT`` per design doc §4.2."""
    return t.kind in ("FEATURE", "FLOAT")


def _check_float(t: ExprType, op_name: str, slot: str) -> None:
    if not _is_float_like(t):
        raise GrammarError(
            f"{op_name}: expected FLOAT-like input for {slot!r}, "
            f"got kind={t.kind!r}"
        )


def _check_int_window(t: ExprType, op_name: str) -> None:
    if t.kind != "INT_WINDOW":
        raise GrammarError(
            f"{op_name}: expected INT_WINDOW window, got kind={t.kind!r}"
        )


# ---------------------------------------------------------------------------
# Operator type rules (per scale_invariance.md §4)
# ---------------------------------------------------------------------------


def _rule_addsub_factory(op_name: str) -> Callable[..., ExprType]:
    def _rule(a: ExprType, b: ExprType) -> ExprType:
        _check_float(a, op_name, "first")
        _check_float(b, op_name, "second")
        if a.taint != b.taint:
            raise GrammarError(
                f"{op_name}: taint mismatch ({a.taint!r}, {b.taint!r}); "
                "PURE and ADJ_TAINTED cannot be mixed in additive ops "
                "(units error, scale_invariance.md §4)"
            )
        return ExprType("FLOAT", a.taint)

    return _rule


def _rule_mul(a: ExprType, b: ExprType) -> ExprType:
    _check_float(a, "mul", "first")
    _check_float(b, "mul", "second")
    taint: ScaleTaint = (
        "ADJ_TAINTED"
        if (a.taint == "ADJ_TAINTED" or b.taint == "ADJ_TAINTED")
        else "PURE"
    )
    return ExprType("FLOAT", taint)


def _rule_div_safe(a: ExprType, b: ExprType) -> ExprType:
    _check_float(a, "div_safe", "numerator")
    _check_float(b, "div_safe", "denominator")
    # (ADJ, ADJ) → PURE  (same-ticker ratio cancels adj_factor)
    # (PURE, PURE) → PURE
    # (PURE, ADJ) → ADJ; (ADJ, PURE) → ADJ
    if a.taint == b.taint:
        return ExprType("FLOAT", "PURE")
    return ExprType("FLOAT", "ADJ_TAINTED")


def _rule_unary_preserves_factory(op_name: str) -> Callable[..., ExprType]:
    def _rule(a: ExprType) -> ExprType:
        _check_float(a, op_name, "input")
        return ExprType("FLOAT", a.taint)

    return _rule


def _rule_sign(a: ExprType) -> ExprType:
    _check_float(a, "sign", "input")
    # sign(a × x) = sign(x) when a > 0 (adj_factor is positive). Always PURE.
    return ExprType("FLOAT", "PURE")


def _rule_ts_linear_factory(op_name: str) -> Callable[..., ExprType]:
    def _rule(a: ExprType, n: ExprType) -> ExprType:
        _check_float(a, op_name, "first")
        _check_int_window(n, op_name)
        return ExprType("FLOAT", a.taint)

    return _rule


def _rule_ts_invariant_factory(op_name: str) -> Callable[..., ExprType]:
    def _rule(a: ExprType, n: ExprType) -> ExprType:
        _check_float(a, op_name, "first")
        _check_int_window(n, op_name)
        return ExprType("FLOAT", "PURE")

    return _rule


def _rule_ts_corr(a: ExprType, b: ExprType, n: ExprType) -> ExprType:
    _check_float(a, "ts_corr", "first")
    _check_float(b, "ts_corr", "second")
    _check_int_window(n, "ts_corr")
    return ExprType("FLOAT", "PURE")


def _rule_cs_factory(op_name: str) -> Callable[..., ExprType]:
    def _rule(a: ExprType) -> ExprType:
        _check_float(a, op_name, "input")
        if a.taint != "PURE":
            raise GrammarError(
                f"{op_name}: requires PURE input (scale-invariance gate per "
                f"scale_invariance.md §4 cs_* rule); got taint={a.taint!r}. "
                "Wrap ADJ_TAINTED price expressions in a same-ticker ratio "
                "(div_safe(a, b)) or use a taint-invariant ts_* op (e.g. "
                "ts_pctchange, ts_rank, ts_corr) to neutralise."
            )
        return ExprType("CSF", "PURE")

    return _rule


def _rule_where(cond: ExprType, a: ExprType, b: ExprType) -> ExprType:
    _check_float(cond, "where", "cond")
    _check_float(a, "where", "a")
    _check_float(b, "where", "b")
    if cond.taint != "PURE":
        raise GrammarError(
            "where: cond must be PURE (a threshold against ADJ_TAINTED is "
            f"itself ADJ_TAINTED); got cond.taint={cond.taint!r}"
        )
    if a.taint != b.taint:
        raise GrammarError(
            f"where: a/b taints must match; got ({a.taint!r}, {b.taint!r})"
        )
    return ExprType("FLOAT", a.taint)


# ---------------------------------------------------------------------------
# Operator dataclass and registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Operator:
    """A registered factor-mining operator.

    ``arg_kinds`` is a tuple of symbolic kinds (``"FLOAT"`` or
    ``"INT_WINDOW"``) used by the random generator to enumerate
    candidate input types. The full taint-aware type rule lives in
    ``output_type_fn``.
    """

    name: str
    arity: int
    arg_kinds: tuple[str, ...]
    output_type_fn: Callable[..., ExprType]
    compute_fn: Callable
    commutative: bool = False

    def output_type(self, *input_types: ExprType) -> ExprType:
        if len(input_types) != self.arity:
            raise GrammarError(
                f"{self.name}: expected {self.arity} args, got {len(input_types)}"
            )
        return self.output_type_fn(*input_types)


class _OperatorRegistry:
    """Map from operator name to its ``Operator`` record."""

    def __init__(self) -> None:
        self._ops: dict[str, Operator] = {}

    def register(self, op: Operator) -> None:
        if op.name in self._ops:
            raise ValueError(f"Operator {op.name!r} already registered")
        self._ops[op.name] = op

    def get(self, name: str) -> Operator | None:
        return self._ops.get(name)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._ops

    def all_operators(self) -> tuple[Operator, ...]:
        return tuple(self._ops.values())

    def names(self) -> tuple[str, ...]:
        return tuple(self._ops.keys())


REGISTRY = _OperatorRegistry()


def _register_all() -> None:
    # Arithmetic
    REGISTRY.register(
        Operator(
            "add", 2, ("FLOAT", "FLOAT"),
            _rule_addsub_factory("add"), _ops.add, commutative=True,
        )
    )
    REGISTRY.register(
        Operator(
            "sub", 2, ("FLOAT", "FLOAT"),
            _rule_addsub_factory("sub"), _ops.sub, commutative=False,
        )
    )
    REGISTRY.register(
        Operator(
            "mul", 2, ("FLOAT", "FLOAT"),
            _rule_mul, _ops.mul, commutative=True,
        )
    )
    REGISTRY.register(
        Operator(
            "div_safe", 2, ("FLOAT", "FLOAT"),
            _rule_div_safe, _ops.div_safe, commutative=False,
        )
    )

    # Unary
    REGISTRY.register(
        Operator(
            "neg", 1, ("FLOAT",),
            _rule_unary_preserves_factory("neg"), _ops.neg,
        )
    )
    REGISTRY.register(
        Operator(
            "abs", 1, ("FLOAT",),
            _rule_unary_preserves_factory("abs"), _ops.abs_,
        )
    )
    REGISTRY.register(
        Operator(
            "sign", 1, ("FLOAT",),
            _rule_sign, _ops.sign,
        )
    )
    REGISTRY.register(
        Operator(
            "log_safe", 1, ("FLOAT",),
            _rule_unary_preserves_factory("log_safe"), _ops.log_safe,
        )
    )
    REGISTRY.register(
        Operator(
            "sqrt_safe", 1, ("FLOAT",),
            _rule_unary_preserves_factory("sqrt_safe"), _ops.sqrt_safe,
        )
    )

    # Time-series — taint-preserving (linear)
    for name, fn in [
        ("ts_mean", _ops.ts_mean),
        ("ts_std", _ops.ts_std),
        ("ts_max", _ops.ts_max),
        ("ts_min", _ops.ts_min),
        ("ts_sum", _ops.ts_sum),
        ("ts_delta", _ops.ts_delta),
        ("ts_decay_linear", _ops.ts_decay_linear),
    ]:
        REGISTRY.register(
            Operator(
                name, 2, ("FLOAT", "INT_WINDOW"),
                _rule_ts_linear_factory(name), fn,
            )
        )

    # Time-series — taint-invariant (always PURE output)
    for name, fn in [
        ("ts_pctchange", _ops.ts_pctchange),
        ("ts_rank", _ops.ts_rank),
        ("ts_argmax", _ops.ts_argmax),
        ("ts_argmin", _ops.ts_argmin),
        ("ts_skew", _ops.ts_skew),
        ("ts_kurt", _ops.ts_kurt),
    ]:
        REGISTRY.register(
            Operator(
                name, 2, ("FLOAT", "INT_WINDOW"),
                _rule_ts_invariant_factory(name), fn,
            )
        )

    # ts_corr — three-arg
    REGISTRY.register(
        Operator(
            "ts_corr", 3, ("FLOAT", "FLOAT", "INT_WINDOW"),
            _rule_ts_corr, _ops.ts_corr,
        )
    )

    # Cross-sectional — the scale-invariance gate
    for name, fn in [
        ("cs_rank", _ops.cs_rank),
        ("cs_zscore", _ops.cs_zscore),
        ("cs_demean", _ops.cs_demean),
        ("cs_winsorize", _ops.cs_winsorize),
    ]:
        REGISTRY.register(
            Operator(
                name, 1, ("FLOAT",),
                _rule_cs_factory(name), fn,
            )
        )

    # Conditional
    REGISTRY.register(
        Operator(
            "where", 3, ("FLOAT", "FLOAT", "FLOAT"),
            _rule_where, _ops.where,
        )
    )


_register_all()


# ---------------------------------------------------------------------------
# Random expression generator (taint-aware)
# ---------------------------------------------------------------------------


_GENERATOR_TABLE: dict[tuple[OutputKind, ScaleTaint], list[tuple[Operator, tuple[ExprType, ...]]]] | None = None


def _enumerate_op_inputs(op: Operator) -> list[tuple[ExprType, ...]]:
    """All candidate input ExprType tuples for ``op``."""
    options_per_arg: list[list[ExprType]] = []
    for kind in op.arg_kinds:
        if kind == "FLOAT":
            options_per_arg.append(
                [ExprType("FLOAT", "PURE"), ExprType("FLOAT", "ADJ_TAINTED")]
            )
        elif kind == "INT_WINDOW":
            options_per_arg.append([ExprType("INT_WINDOW", "PURE")])
        else:  # pragma: no cover — defensive
            raise GrammarError(f"Unknown arg kind in operator {op.name!r}: {kind!r}")
    return [tuple(combo) for combo in itertools.product(*options_per_arg)]


def _build_generator_table() -> dict[
    tuple[OutputKind, ScaleTaint],
    list[tuple[Operator, tuple[ExprType, ...]]],
]:
    """Reverse-lookup table: ``(output_kind, output_taint) → [(op, inputs)]``.

    Built once on first call to ``random_expression``. Only includes
    operator/input combinations that actually type-check; illegal
    combos (e.g. ``add`` of mismatched taints) are skipped.
    """
    table: dict[tuple[OutputKind, ScaleTaint], list[tuple[Operator, tuple[ExprType, ...]]]] = {}
    for op in REGISTRY.all_operators():
        for inputs in _enumerate_op_inputs(op):
            try:
                out = op.output_type(*inputs)
            except GrammarError:
                continue
            key: tuple[OutputKind, ScaleTaint] = (out.kind, out.taint)
            table.setdefault(key, []).append((op, inputs))
    return table


def _generator_table() -> dict[
    tuple[OutputKind, ScaleTaint],
    list[tuple[Operator, tuple[ExprType, ...]]],
]:
    global _GENERATOR_TABLE
    if _GENERATOR_TABLE is None:
        _GENERATOR_TABLE = _build_generator_table()
    return _GENERATOR_TABLE


def _has_leaves_for(target_type: ExprType) -> bool:
    if target_type.kind in ("FEATURE", "FLOAT"):
        return True
    if target_type.kind == "INT_WINDOW":
        return True
    # SCALAR / CSF have no leaves in v1
    return False


def _random_leaf(target_type: ExprType, rng: Random):
    # Deferred import to break circularity with expression.py.
    from .expression import Terminal

    if target_type.kind in ("FEATURE", "FLOAT"):
        if target_type.taint == "ADJ_TAINTED":
            name = rng.choice(FeatureRegistry.V1_RAW_PRICE)
        else:
            name = rng.choice(FeatureRegistry.V1_SCALE_FREE)
        return Terminal(name)
    if target_type.kind == "INT_WINDOW":
        return Terminal(str(rng.choice(WINDOW_LITERALS)))
    raise GrammarError(
        f"No v1 leaf available for target_type={target_type!r}"
    )


def _random_operator(
    target_type: ExprType,
    max_depth: int,
    min_depth: int,
    rng: Random,
):
    from .expression import OperatorCall  # deferred

    key = (target_type.kind, target_type.taint)
    candidates = _generator_table().get(key, [])
    if not candidates:
        # No operator can produce this type; fall back to leaf if any.
        if _has_leaves_for(target_type):
            return _random_leaf(target_type, rng)
        raise GrammarError(
            f"Generator cannot produce target_type={target_type!r}: "
            "no operator candidates and no leaves available"
        )
    # Some operator-input combinations satisfy the static type check but
    # are rejected by the AST constructor's post-validation (e.g. the
    # ``ts_corr(f(X), X, N)`` pseudo-signal rule added after the B-std
    # empirical run — see docs/factor_mining/empirical_results_b_std.md).
    # Retry up to MAX_OP_RETRIES with fresh subtree samples before
    # falling back to a leaf; the rejection rate of the trivial form
    # is < 1% in practice so the retry budget is never exhausted.
    MAX_OP_RETRIES = 10
    last_err: GrammarError | None = None
    for _ in range(MAX_OP_RETRIES):
        op, input_types = rng.choice(candidates)
        children = tuple(
            _gen(t, max_depth - 1, min_depth - 1, rng) for t in input_types
        )
        try:
            return OperatorCall(op.name, children)
        except GrammarError as exc:
            last_err = exc
            continue
    # If retries are exhausted (extremely rare) fall back to a leaf when
    # one exists for this target_type; otherwise propagate the last error.
    if _has_leaves_for(target_type):
        return _random_leaf(target_type, rng)
    raise GrammarError(
        f"Generator cannot construct {target_type!r} after "
        f"{MAX_OP_RETRIES} retries: {last_err}"
    )


def _gen(
    target_type: ExprType,
    max_depth: int,
    min_depth: int,
    rng: Random,
):
    has_leaves = _has_leaves_for(target_type)
    if max_depth <= 0 and has_leaves and min_depth <= 0:
        return _random_leaf(target_type, rng)
    if not has_leaves:
        # Must use an operator (e.g. target=CSF with no leaves)
        return _random_operator(target_type, max_depth, min_depth, rng)
    if min_depth > 0:
        # Forced to descend through at least one more operator level
        return _random_operator(target_type, max_depth, min_depth, rng)
    if max_depth <= 0:
        return _random_leaf(target_type, rng)
    # Weighted choice — bias toward operators so trees aren't too shallow
    if rng.random() < 0.3:
        return _random_leaf(target_type, rng)
    return _random_operator(target_type, max_depth, min_depth, rng)


def random_expression(
    target_type: ExprType,
    max_depth: int,
    min_depth: int = 2,
    rng: Random | None = None,
):
    """Generate a random expression with the given target output type.

    The generator is taint-aware: when descending through a ``cs_*``
    operator, sub-expressions are sampled with ``taint=PURE`` as a hard
    constraint, so every produced expression is guaranteed type-valid
    AND scale-pure at the root.

    Parameters
    ----------
    target_type
        The required ``ExprType`` of the root. For factor-mining the
        canonical call is ``ExprType("CSF", "PURE")``.
    max_depth
        Maximum tree depth. Depth-0 = leaf; depth-1 = leaf-only
        operator (e.g. ``cs_rank($volume)``).
    min_depth
        Minimum tree depth. Default 2 — prevents trivial single-op
        trees per ``factor_mining_phase1_preflight.md`` §4.4.
    rng
        Optional ``random.Random`` for deterministic seeding.
    """
    if max_depth < min_depth:
        raise ValueError(
            f"max_depth ({max_depth}) must be >= min_depth ({min_depth})"
        )
    if rng is None:
        rng = Random()
    return _gen(target_type, max_depth, min_depth, rng)
