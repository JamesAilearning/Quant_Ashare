"""Expression AST: ``Terminal``, ``OperatorCall``, serialisation, hash.

The AST nodes are immutable frozen dataclasses. Type-checking is
triggered at construction time (``__post_init__``) via the operator
registry in ``grammar``; constructing an ill-typed expression raises
``GrammarError``. There is no "build now, validate later" mode
(per ``docs/factor_mining/scale_invariance.md`` §6.3).

Structural hashing is canonicalised: commutative operators (``add``,
``mul``) sort their child hashes before incorporating them, so
``add($close, $volume)`` and ``add($volume, $close)`` hash identically.
Equality is similarly commutative-aware. This is the per-Phase-1
guarantee that GP will not waste search budget on permutation-
equivalent duplicates (``factor_mining_phase1_preflight.md`` §4.2).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .grammar import (
    REGISTRY,
    WINDOW_LITERALS,
    ExprType,
    FeatureRegistry,
    GrammarError,
)

# Operators whose `compute_fn` is a bijective monotonic univariate transform
# on the relevant domain. `ts_corr(MONOTONIC_UNIVARIATE(X), X, N)` collapses
# to a near-constant value over a rolling window (residual variation comes
# from numerical compression / domain-boundary NaN, not from real signal).
# `abs` and `sign` are deliberately NOT in this set — `abs` folds the sign
# and the corr can legitimately diverge from ±1 in a meaningful way;
# `sign` is piecewise-constant so the corr is degenerate (the existing
# ts_corr ±Inf → NaN rule already handles it).
_BIJECTIVE_UNIVARIATE_OPS = frozenset({"neg", "log_safe", "sqrt_safe"})


def _ts_corr_is_trivial(a: Expression, b: Expression) -> bool:
    """Return True iff ``ts_corr(a, b, N)`` is a numerical pseudo-signal.

    Trivial forms:
      1. ``a == b`` structurally — zero variance per ticker (ts_corr → NaN
         per v1 §5.2 ts_corr rule, but we reject up-front so GP doesn't
         waste a slot on it).
      2. ``a`` is a bijective univariate transform of ``b`` (or vice versa)
         — e.g. ``ts_corr(log_safe($close), $close, N)``.

    The check is structural, not semantic — it does not catch every
    pseudo-signal (e.g. ``ts_corr(div_safe($close, $close), $close, N)``
    where the inner div_safe is the constant 1.0). It is intentionally
    narrow: only forms surfaced by the B-std empirical run are rejected,
    so the GP search space is not over-constrained.
    """
    if a == b:
        return True
    if isinstance(a, OperatorCall) and a.op_name in _BIJECTIVE_UNIVARIATE_OPS:
        if len(a.children) == 1 and a.children[0] == b:
            return True
    if isinstance(b, OperatorCall) and b.op_name in _BIJECTIVE_UNIVARIATE_OPS:
        if len(b.children) == 1 and b.children[0] == a:
            return True
    return False


class Expression:
    """Abstract base for AST nodes. Concrete: ``Terminal``, ``OperatorCall``."""

    @property
    def output_type(self) -> ExprType:  # pragma: no cover — abstract
        raise NotImplementedError

    def to_dict(self) -> dict:  # pragma: no cover — abstract
        raise NotImplementedError

    def to_qlib_string(self) -> str:  # pragma: no cover — abstract
        raise NotImplementedError

    def depth(self) -> int:  # pragma: no cover — abstract
        raise NotImplementedError

    @classmethod
    def from_dict(cls, d: dict) -> Expression:
        kind = d.get("type")
        if kind == "Terminal":
            return Terminal(d["name"])
        if kind == "OperatorCall":
            children = tuple(cls.from_dict(c) for c in d["children"])
            return OperatorCall(d["op"], children)
        raise GrammarError(f"Unknown serialised node type: {kind!r}")


@dataclass(frozen=True, eq=False)
class Terminal(Expression):
    """Leaf node: a feature like ``$close`` or a literal like ``"20"``."""

    name: str

    def __post_init__(self) -> None:
        # Validate at construction (raises GrammarError on unknown name) and
        # warm the output_type cache in the same pass.
        _ = self.output_type

    def _compute_type(self) -> ExprType:
        if FeatureRegistry.is_feature(self.name):
            return FeatureRegistry.terminal_type(self.name)
        # Integer window literal
        if self.name.isdigit():
            n = int(self.name)
            if n in WINDOW_LITERALS:
                return ExprType("INT_WINDOW", "PURE")
            raise GrammarError(
                f"Integer literal {n} is not in WINDOW_LITERALS={WINDOW_LITERALS}"
            )
        raise GrammarError(
            f"Unknown terminal {self.name!r} — must be in FeatureRegistry.V1 "
            f"({FeatureRegistry.V1}) or a window literal in {WINDOW_LITERALS}"
        )

    @property
    def output_type(self) -> ExprType:
        cached = self.__dict__.get("_output_type_cache")
        if cached is not None:
            return cached
        result = self._compute_type()
        object.__setattr__(self, "_output_type_cache", result)
        return result

    def to_dict(self) -> dict:
        return {"type": "Terminal", "name": self.name}

    def to_qlib_string(self) -> str:
        return self.name

    def depth(self) -> int:
        return 0

    def __hash__(self) -> int:
        return hash(("Terminal", self.name))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Terminal):
            return NotImplemented
        return self.name == other.name

    def __repr__(self) -> str:
        return f"Terminal({self.name!r})"


@dataclass(frozen=True, eq=False)
class OperatorCall(Expression):
    """Internal node: an operator name plus a tuple of child expressions."""

    op_name: str
    children: tuple[Expression, ...]

    def __post_init__(self) -> None:
        if self.op_name not in REGISTRY:
            raise GrammarError(
                f"Unknown operator {self.op_name!r}; registered operators: "
                f"{REGISTRY.names()}"
            )
        if not isinstance(self.children, tuple):  # pragma: no cover
            raise GrammarError(
                f"OperatorCall.children must be a tuple, got {type(self.children).__name__}"
            )
        # Trigger type-check. Raises GrammarError on illegal taint combos
        # or unknown operator inputs.
        _ = self.output_type
        # Reject pseudo-signal forms surfaced by the B-std empirical run
        # (see docs/factor_mining/empirical_results_b_std.md §"Top expressions
        # reveal pseudo-signals"). `ts_corr(f(X), X, N)` where ``f`` is a
        # bijective univariate transform on the relevant domain is
        # mechanically near ±1 over a rolling window — the residual variation
        # comes from numerical compression (e.g. log near zero), not predictive
        # content. GP picks these up as high IS-IC factors but they don't
        # transfer to OOS.
        if self.op_name == "ts_corr" and len(self.children) >= 2:
            if _ts_corr_is_trivial(self.children[0], self.children[1]):
                raise GrammarError(
                    f"ts_corr(a, b, N) where a and b are trivially related "
                    f"(same expression, or one is a bijective univariate "
                    f"transform — neg/log_safe/sqrt_safe — of the other) is "
                    f"a numerical pseudo-signal, not a factor; got "
                    f"a={self.children[0].to_qlib_string()!r}, "
                    f"b={self.children[1].to_qlib_string()!r}. "
                    "See docs/factor_mining/empirical_results_b_std.md "
                    "§\"Top expressions reveal pseudo-signals\" for the "
                    "B-std evidence motivating this rule."
                )

    @property
    def output_type(self) -> ExprType:
        # Memoized (see __hash__): GP crossover/mutation repeatedly reads
        # ``.output_type`` over subexpression lists; uncached it re-walks the
        # subtree each time. Warmed once in __post_init__.
        cached = self.__dict__.get("_output_type_cache")
        if cached is not None:
            return cached
        op = REGISTRY.get(self.op_name)
        assert op is not None  # checked in __post_init__
        input_types = tuple(c.output_type for c in self.children)
        result = op.output_type(*input_types)
        object.__setattr__(self, "_output_type_cache", result)
        return result

    def to_dict(self) -> dict:
        return {
            "type": "OperatorCall",
            "op": self.op_name,
            "children": [c.to_dict() for c in self.children],
        }

    def to_qlib_string(self) -> str:
        args = ", ".join(c.to_qlib_string() for c in self.children)
        return f"{self.op_name}({args})"

    def depth(self) -> int:
        if not self.children:
            return 1
        return 1 + max(c.depth() for c in self.children)

    def __hash__(self) -> int:
        # Memoized: nodes are frozen (immutable), and the GP loop hashes the
        # same node repeatedly (dedup sets, fitness_cache / _all_evaluated
        # keys). Uncached, every call re-walks + re-sorts the whole subtree.
        # ``object.__setattr__`` because the dataclass is frozen.
        cached = self.__dict__.get("_hash_cache")
        if cached is not None:
            return cached
        op = REGISTRY.get(self.op_name)
        assert op is not None
        child_hashes: Iterable[int] = (hash(c) for c in self.children)
        if op.commutative:
            child_hashes = sorted(child_hashes)
        else:
            child_hashes = list(child_hashes)
        h = hash(("OperatorCall", self.op_name, tuple(child_hashes)))
        object.__setattr__(self, "_hash_cache", h)
        return h

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, OperatorCall):
            return NotImplemented
        if self.op_name != other.op_name:
            return False
        if len(self.children) != len(other.children):
            return False
        op = REGISTRY.get(self.op_name)
        assert op is not None
        if op.commutative:
            return sorted(self.children, key=hash) == sorted(
                other.children, key=hash
            )
        return self.children == other.children

    def __repr__(self) -> str:
        return f"OperatorCall({self.op_name!r}, {self.children!r})"


# ---------------------------------------------------------------------------
# Parser — Lisp-style: op_name(arg1, arg2, ...) and $feature / integer literals
# ---------------------------------------------------------------------------


def _tokenize(s: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c in "(,)":
            tokens.append(c)
            i += 1
            continue
        if c == "$":
            j = i + 1
            while j < n and (s[j].isalnum() or s[j] == "_"):
                j += 1
            tokens.append(s[i:j])
            i = j
            continue
        if c.isalpha() or c == "_":
            j = i + 1
            while j < n and (s[j].isalnum() or s[j] == "_"):
                j += 1
            tokens.append(s[i:j])
            i = j
            continue
        if c.isdigit():
            j = i + 1
            while j < n and s[j].isdigit():
                j += 1
            tokens.append(s[i:j])
            i = j
            continue
        raise GrammarError(f"Unexpected character at position {i}: {c!r}")
    return tokens


def _parse_node(tokens: list[str], pos: int) -> tuple[Expression, int]:
    if pos >= len(tokens):
        raise GrammarError("Unexpected end of expression input")
    tok = tokens[pos]
    if tok.startswith("$"):
        return Terminal(tok), pos + 1
    if tok.isdigit():
        return Terminal(tok), pos + 1
    # Otherwise: operator name followed by '(...)'.
    if pos + 1 >= len(tokens) or tokens[pos + 1] != "(":
        raise GrammarError(
            f"Expected '(' after operator {tok!r} at position {pos + 1}"
        )
    pos += 2
    children: list[Expression] = []
    if pos < len(tokens) and tokens[pos] == ")":
        return OperatorCall(tok, ()), pos + 1
    while True:
        child, pos = _parse_node(tokens, pos)
        children.append(child)
        if pos >= len(tokens):
            raise GrammarError("Unexpected end of input inside argument list")
        if tokens[pos] == ",":
            pos += 1
            continue
        if tokens[pos] == ")":
            return OperatorCall(tok, tuple(children)), pos + 1
        raise GrammarError(
            f"Expected ',' or ')' at position {pos}, got {tokens[pos]!r}"
        )


def parse_expression(s: str) -> Expression:
    """Parse a Lisp-style expression string into an ``Expression``.

    The grammar is ``$feature | integer | op_name(arg, arg, ...)``.
    Constructing an ill-typed expression raises ``GrammarError`` per
    ``__post_init__``; the parser performs no separate type-check.
    """
    tokens = _tokenize(s)
    if not tokens:
        raise GrammarError("Empty expression input")
    expr, pos = _parse_node(tokens, 0)
    if pos < len(tokens):
        raise GrammarError(
            f"Unexpected trailing tokens after expression: {tokens[pos:]!r}"
        )
    return expr
