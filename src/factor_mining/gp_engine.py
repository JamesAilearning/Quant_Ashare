"""Genetic-programming search loop.

Per ``docs/factor_mining/factor_mining_claude_code_design.md`` §6
Phase 3 and ``factor_mining_design.md`` §4.4 (genetic operations).
Tournament selection (k=3), elitism (top 5 %), type-preserving subtree
crossover, three mutation operators (subtree / point / constant),
per-generation hash dedup, deterministic with seed.

No qlib import, no ``src.pit`` import. The PIT layer is reached only
through the Phase 2 ``pit_adapter`` module; this file consumes
panel + forward-return data via parameters.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from random import Random

import numpy as np
import pandas as pd

from .evaluator import EvaluationResult, evaluate_factor, max_abs_corr
from .expression import Expression, OperatorCall, Terminal
from .factor_pool import LEGACY_METHOD_TAG, FactorPool, PoolEntry
from .fitness import FitnessConfig, compute_fitness, expression_size
from .grammar import (
    WINDOW_LITERALS,
    ExprType,
    FeatureRegistry,
    GrammarError,
    random_expression,
)

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Evaluator-method contract for fitness scoring.
#
# Changing this constant changes the semantics of every score in
# ``fitness_cache`` and ``_all_evaluated``: ``"normal"`` keeps Pearson
# IC and Spearman rank-IC as independent inputs to ``compute_fitness``,
# ``"rank"`` collapses both to Spearman (the pre-PR #142 contract that
# was double-counting rank IC). Checkpoint payloads embed the value
# this engine used so resumes across a method change clear stale
# scores instead of mixing semantics in one run. (Codex P1 on PR #142.)
# ---------------------------------------------------------------------------

FITNESS_EVALUATOR_METHOD = "normal"


# ---------------------------------------------------------------------------
# Configs and stats
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GPConfig:
    """Tunable GP search parameters."""

    population_size: int = 500
    n_generations: int = 50
    tournament_size: int = 3
    elite_frac: float = 0.05
    p_crossover: float = 0.7
    p_mutate_subtree: float = 0.15
    p_mutate_point: float = 0.10
    p_mutate_const: float = 0.05
    max_depth: int = 6
    min_depth: int = 2
    target_kind: str = "CSF"
    target_taint: str = "PURE"
    seed: int = 42

    @property
    def target_type(self) -> ExprType:
        return ExprType(self.target_kind, self.target_taint)  # type: ignore[arg-type]


@dataclass(frozen=True)
class GenerationStats:
    """Per-generation summary."""

    gen: int
    best_fitness: float
    mean_fitness: float
    median_fitness: float
    n_unique: int
    n_invalid: int
    best_expr_str: str


# ---------------------------------------------------------------------------
# Subtree helpers (path = tuple of child indices)
# ---------------------------------------------------------------------------


SubtreePath = tuple[int, ...]


def _enumerate_positions(
    expr: Expression, path: SubtreePath = ()
) -> list[tuple[SubtreePath, Expression]]:
    """Walk the AST and yield (path, subtree) for every node."""
    out: list[tuple[SubtreePath, Expression]] = [(path, expr)]
    if isinstance(expr, OperatorCall):
        for i, c in enumerate(expr.children):
            out.extend(_enumerate_positions(c, path + (i,)))
    return out


def _get_subtree(expr: Expression, path: SubtreePath) -> Expression:
    """Navigate to the subtree at ``path``."""
    node = expr
    for i in path:
        if not isinstance(node, OperatorCall):
            raise IndexError(f"Path {path!r} cannot index into a {type(node).__name__}")
        node = node.children[i]
    return node


def _replace_subtree(
    expr: Expression, path: SubtreePath, new_subtree: Expression
) -> Expression:
    """Return a new ``Expression`` with the subtree at ``path`` replaced."""
    if path == ():
        return new_subtree
    if not isinstance(expr, OperatorCall):
        raise IndexError(f"Cannot replace at {path!r} in a {type(expr).__name__}")
    i = path[0]
    children = list(expr.children)
    children[i] = _replace_subtree(expr.children[i], path[1:], new_subtree)
    return OperatorCall(expr.op_name, tuple(children))


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class GPEngine:
    """Genetic-programming engine: initial population → evaluate → next gen.

    All randomness flows through a single seeded ``random.Random`` so
    identical ``(config, fitness_config)`` inputs produce identical
    populations and final pools. Novelty is computed *within* the
    current generation only (against expressions already evaluated in
    this generation, in deterministic order), so the cache state
    survives checkpoint round-trips without affecting fitness scores.
    """

    def __init__(self, config: GPConfig, fitness_config: FitnessConfig) -> None:
        self.config = config
        self.fitness_config = fitness_config
        self.rng = Random(config.seed)
        self.population: list[Expression] = []
        self.fitness_cache: dict[int, float] = {}
        self.history: list[GenerationStats] = []
        self.current_gen: int = 0
        self._all_evaluated: dict[int, PoolEntry] = {}
        self._per_generation_values: dict[int, pd.DataFrame] = {}
        # Track (exception_type, expr_hash) we've already warned about
        # so a recurring per-expression failure doesn't spam the log.
        self._evaluation_warning_keys: set[tuple[str, int]] = set()
        # Optional universe-membership mask (date × ticker bool), set by
        # ``run`` on real-PIT runs so the evaluator measures coverage
        # members-only. None for synthetic / dense panels — see
        # evaluator._coverage.
        self._universe_mask: pd.DataFrame | None = None
        # Coverage-cache key under which the scores in fitness_cache /
        # _all_evaluated were produced: "all_cells" or "members:<mask
        # fingerprint>". Set on ``run`` and persisted in checkpoints so a
        # resume/reuse with a different mask (or mode) invalidates the now-
        # incomparable cache instead of silently mixing coverage semantics
        # (Codex P1+P2 on PR #217). None until the first run / checkpoint load.
        self._coverage_key: str | None = None

    # ------------------------------------------------------------------
    # Population lifecycle
    # ------------------------------------------------------------------

    def initialize_population(self) -> None:
        """Generate the initial population of unique random expressions."""
        target = self.config.target_type
        seen: set[int] = set()
        pop: list[Expression] = []
        target_size = self.config.population_size
        # Safety bound to avoid infinite loops on degenerate configs.
        max_attempts = target_size * 50
        attempts = 0
        while len(pop) < target_size and attempts < max_attempts:
            attempts += 1
            try:
                expr = random_expression(
                    target,
                    max_depth=self.config.max_depth,
                    min_depth=self.config.min_depth,
                    rng=self.rng,
                )
            except (GrammarError, ValueError):
                continue
            h = hash(expr)
            if h in seen:
                continue
            seen.add(h)
            pop.append(expr)
        self.population = pop

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate_individual(
        self,
        expr: Expression,
        panel,
        fwd_ret: pd.DataFrame,
    ) -> tuple[float, EvaluationResult | None]:
        """Score an expression; cached by structural hash."""
        h = hash(expr)
        if h in self.fitness_cache:
            return self.fitness_cache[h], None
        try:
            # ``FITNESS_EVALUATOR_METHOD = "normal"`` makes ic_mean=Pearson
            # and rank_ic_mean=Spearman independent. With method="rank" the
            # two were identical, so fitness double-counted rank IC
            # (w_ic·|rank| + w_rankic·|rank|). The constant lives at module
            # scope so checkpoint payloads can tag stored scores with the
            # method that produced them — see ``save_checkpoint``.
            result = evaluate_factor(
                expr, panel, fwd_ret, method=FITNESS_EVALUATOR_METHOD,
                universe_mask=self._universe_mask,
            )
        except KeyError as exc:
            # The evaluator raises KeyError only when a Terminal references
            # a feature missing from the panel — that's a setup-time
            # data-contract violation (panel doesn't cover the grammar's
            # feature set), not a per-expression arithmetic failure.
            # Fail fast so the bug surfaces instead of every random
            # expression silently scoring -inf.
            raise RuntimeError(
                "factor-mining panel is missing a feature referenced by "
                "the grammar; this is a setup-time data-contract "
                f"violation, not a per-expression failure ({exc!s})"
            ) from exc
        except Exception as exc:  # noqa: BLE001 — broad on purpose
            # Random GP expressions can legitimately blow up arithmetically
            # (overflow, undefined ops, NaN propagation, etc.). Log the
            # first occurrence per (exception_type, expr_hash) so noise
            # stays bounded, then cache -inf and continue the loop.
            warn_key = (type(exc).__name__, h)
            if warn_key not in self._evaluation_warning_keys:
                self._evaluation_warning_keys.add(warn_key)
                _log.warning(
                    "factor evaluation failed for expr_hash=%d: %s: %s "
                    "(caching as -inf; further warnings for this "
                    "expression suppressed)",
                    h, type(exc).__name__, exc,
                )
            self.fitness_cache[h] = float("-inf")
            return float("-inf"), None
        novelty = self._within_generation_novelty(result.factor_values)
        size = expression_size(expr)
        score = compute_fitness(
            result, expr_size=size, novelty_penalty=novelty, config=self.fitness_config,
        )
        self.fitness_cache[h] = score
        if score > float("-inf"):
            # Skip novelty-cache write when w_corr=0 — the cache is read
            # only by _within_generation_novelty which short-circuits to
            # 0.0 in that case. Saves O(pop_size × date × ticker) of peak
            # heap per generation, important on 12-feature panels where
            # pandas's MultiIndex stack pushed Windows into MemoryError
            # at modest pop sizes (see empirical_results_b_std.md
            # "Iteration 5: 12-feature universe").
            if self.fitness_config.w_corr != 0.0:
                self._per_generation_values[h] = result.factor_values
            self._all_evaluated[h] = PoolEntry.from_result(
                expr=expr,
                result=result,
                fitness=score,
                expr_size=size,
                method=FITNESS_EVALUATOR_METHOD,
            )
        return score, result

    def _within_generation_novelty(self, factor_values: pd.DataFrame) -> float:
        """Max abs Pearson correlation against same-generation cached values.

        Within-generation only — past-generation factor values are
        cleared at each generation boundary so the novelty calculation
        is invariant to long-history cache state (which would break
        determinism across checkpoint resume).

        Short-circuit: when ``fitness_config.w_corr == 0`` the novelty
        term contributes nothing to the score (``compute_fitness`` does
        ``- w_corr * novelty_penalty``), so we skip the expensive
        per-other-factor stack/join. On the B-std 12-feature universe
        the per-iteration novelty allocation pattern blew Python's
        pandas heap; users opting out of novelty pressure (the soft
        fitness recipe) get the GP search budget for free.
        """
        if self.fitness_config.w_corr == 0.0:
            return 0.0
        if not self._per_generation_values or factor_values.empty:
            return 0.0
        new_stack = factor_values.stack(future_stack=True)
        if new_stack.empty:
            return 0.0
        # Inner pairwise loop shared via evaluator.max_abs_corr; the w_corr / OOM
        # short-circuits above stay here (GP-specific). np.isfinite guard (was
        # pd.notna) now consistent across all three call sites.
        other_stacks = (
            other.stack(future_stack=True)
            for other in self._per_generation_values.values()
            if not other.empty
        )
        return max_abs_corr(new_stack, other_stacks)

    # ------------------------------------------------------------------
    # Genetic operators
    # ------------------------------------------------------------------

    def select(self, evaluated: list[tuple[Expression, float]]) -> Expression:
        """Tournament selection (k=tournament_size). Ties broken by index."""
        k = min(self.config.tournament_size, len(evaluated))
        if k <= 0:
            raise ValueError("evaluated population is empty")
        idxs = self.rng.sample(range(len(evaluated)), k)
        best_idx = max(idxs, key=lambda i: (evaluated[i][1], -i))
        return evaluated[best_idx][0]

    def crossover(self, parent_a: Expression, parent_b: Expression) -> Expression:
        """Type-preserving subtree exchange. Returns parent_a on failure."""
        positions_a = _enumerate_positions(parent_a)
        path_a, sub_a = self.rng.choice(positions_a)
        target_type = sub_a.output_type
        positions_b = [
            (p, s) for p, s in _enumerate_positions(parent_b)
            if s.output_type == target_type
        ]
        if not positions_b:
            return parent_a
        _, sub_b = self.rng.choice(positions_b)
        try:
            return _replace_subtree(parent_a, path_a, sub_b)
        except (GrammarError, IndexError, ValueError):
            return parent_a

    def mutate_subtree(self, expr: Expression) -> Expression:
        positions = _enumerate_positions(expr)
        pos_path, pos_sub = self.rng.choice(positions)
        target_type = pos_sub.output_type
        depth_used = len(pos_path)
        remaining = max(1, self.config.max_depth - depth_used)
        # min_depth must stay <= remaining
        sub_min = max(1, min(self.config.min_depth, remaining))
        try:
            new_sub = random_expression(
                target_type, max_depth=remaining, min_depth=sub_min, rng=self.rng,
            )
            return _replace_subtree(expr, pos_path, new_sub)
        except (GrammarError, ValueError):
            return expr

    def mutate_point(self, expr: Expression) -> Expression:
        positions = _enumerate_positions(expr)
        terminal_positions = [
            (p, t) for p, t in positions if isinstance(t, Terminal)
        ]
        if not terminal_positions:
            return expr
        pos_path, terminal = self.rng.choice(terminal_positions)
        try:
            new_term = self._random_terminal_same_type(
                terminal.output_type, exclude=terminal.name,
            )
        except GrammarError:
            return expr
        try:
            return _replace_subtree(expr, pos_path, new_term)
        except (GrammarError, ValueError):
            return expr

    def mutate_const(self, expr: Expression) -> Expression:
        positions = _enumerate_positions(expr)
        window_positions = [
            (p, t) for p, t in positions
            if isinstance(t, Terminal) and t.output_type.kind == "INT_WINDOW"
        ]
        if not window_positions:
            return expr
        pos_path, terminal = self.rng.choice(window_positions)
        alts = [str(w) for w in WINDOW_LITERALS if str(w) != terminal.name]
        if not alts:
            return expr
        new_term = Terminal(self.rng.choice(alts))
        try:
            return _replace_subtree(expr, pos_path, new_term)
        except (GrammarError, ValueError):
            return expr

    def _random_terminal_same_type(
        self, target: ExprType, exclude: str
    ) -> Terminal:
        if target.kind == "FEATURE":
            if target.taint == "ADJ_TAINTED":
                pool = [t for t in FeatureRegistry.V1_RAW_PRICE if t != exclude]
            else:
                pool = [t for t in FeatureRegistry.V1_SCALE_FREE if t != exclude]
            if not pool:
                raise GrammarError("no alternative terminal available")
            return Terminal(self.rng.choice(pool))
        if target.kind == "INT_WINDOW":
            pool = [str(w) for w in WINDOW_LITERALS if str(w) != exclude]
            if not pool:
                raise GrammarError("no alternative window")
            return Terminal(self.rng.choice(pool))
        raise GrammarError(f"no terminal pool for type {target!r}")

    # ------------------------------------------------------------------
    # Generation loop
    # ------------------------------------------------------------------

    def next_generation(
        self, evaluated: list[tuple[Expression, float]]
    ) -> list[Expression]:
        """Build the next generation from elitism + select + cross + mutate."""
        sorted_idx = sorted(
            range(len(evaluated)),
            key=lambda i: (-evaluated[i][1], i),
        )
        sorted_pop = [evaluated[i][0] for i in sorted_idx]
        target_size = self.config.population_size
        n_elite = max(1, int(self.config.elite_frac * target_size))
        new_pop: list[Expression] = list(sorted_pop[:n_elite])
        seen: set[int] = {hash(e) for e in new_pop}

        max_iters = target_size * 10
        iters = 0
        while len(new_pop) < target_size and iters < max_iters:
            iters += 1
            parent_a = self.select(evaluated)
            child = parent_a
            if self.rng.random() < self.config.p_crossover:
                parent_b = self.select(evaluated)
                child = self.crossover(parent_a, parent_b)
            r = self.rng.random()
            if r < self.config.p_mutate_subtree:
                child = self.mutate_subtree(child)
            elif r < self.config.p_mutate_subtree + self.config.p_mutate_point:
                child = self.mutate_point(child)
            elif (
                r
                < self.config.p_mutate_subtree
                + self.config.p_mutate_point
                + self.config.p_mutate_const
            ):
                child = self.mutate_const(child)
            h = hash(child)
            if h not in seen:
                seen.add(h)
                new_pop.append(child)

        # Top up with fresh randoms if dedup left the population short.
        topup_attempts = 0
        max_topup = target_size * 50
        while len(new_pop) < target_size and topup_attempts < max_topup:
            topup_attempts += 1
            try:
                fresh = random_expression(
                    self.config.target_type,
                    max_depth=self.config.max_depth,
                    min_depth=self.config.min_depth,
                    rng=self.rng,
                )
            except (GrammarError, ValueError):
                continue
            h = hash(fresh)
            if h in seen:
                continue
            seen.add(h)
            new_pop.append(fresh)
        return new_pop[:target_size]

    def run(
        self,
        panel,
        fwd_ret: pd.DataFrame,
        *,
        n_generations: int | None = None,
        universe_mask: pd.DataFrame | None = None,
    ) -> FactorPool:
        """Run the GP loop and return the final ``FactorPool``.

        ``universe_mask`` (date × ticker bool) is forwarded to the
        evaluator so coverage is measured members-only on real-PIT
        panels; None preserves the legacy all-cells coverage.
        """
        # Assign on EVERY run, including None: omitting the mask selects
        # all-cells coverage (per the docstring), so reusing an engine that
        # previously ran with a PIT mask for a later mask-free run must reset
        # rather than retain stale membership from the old panel. Codex P2
        # on #217.
        self._universe_mask = universe_mask
        # Guard a resume/reuse against an incomparable cache: scores cached
        # under a different coverage key — all-cells vs members, OR a
        # different member mask (different universe / date range) — are not
        # comparable to what this run would produce, so discard them and let
        # the run re-score cleanly. ``evaluate_individual`` returns cached
        # scores by expression hash without recomputing coverage, so a coarse
        # members/all-cells check is not enough; the key embeds a mask
        # fingerprint. Mirrors the evaluator_method invalidation in
        # load_checkpoint. Codex P1+P2 on #217.
        run_coverage_key = _coverage_key_for(self._universe_mask)
        if (
            self._coverage_key is not None
            and self._coverage_key != run_coverage_key
            and (self.fitness_cache or self._all_evaluated)
        ):
            _log.warning(
                "coverage cache key %r (prior run/checkpoint) != this run %r "
                "— discarding fitness_cache (%d) and all_evaluated (%d); "
                "re-scoring from scratch to avoid mixing coverage semantics "
                "across a mask/mode change.",
                self._coverage_key,
                run_coverage_key,
                len(self.fitness_cache),
                len(self._all_evaluated),
            )
            self.fitness_cache = {}
            self._all_evaluated = {}
        self._coverage_key = run_coverage_key
        if not self.population:
            self.initialize_population()
        n_gens = (
            n_generations if n_generations is not None else self.config.n_generations
        )
        target_final_gen = self.current_gen + n_gens
        while self.current_gen < target_final_gen:
            self._per_generation_values.clear()
            evaluated: list[tuple[Expression, float]] = []
            for expr in self.population:
                score, _ = self.evaluate_individual(expr, panel, fwd_ret)
                evaluated.append((expr, score))
            self.history.append(self._compute_stats(self.current_gen, evaluated))
            self.current_gen += 1
            # Always advance population so checkpoint + resume == continuous.
            # The cost of computing the post-loop "next gen" is minimal and
            # the determinism contract requires that `self.population` after
            # ``run(n)`` is the same as `self.population` mid-way through a
            # longer continuous run.
            self.population = self.next_generation(evaluated)
        pool = FactorPool()
        for entry in self._all_evaluated.values():
            pool.add(entry)
        return pool

    def _compute_stats(
        self, gen: int, evaluated: list[tuple[Expression, float]]
    ) -> GenerationStats:
        scores = np.array([f for _, f in evaluated], dtype=float)
        finite_mask = np.isfinite(scores)
        finite = scores[finite_mask]
        unique_hashes = {hash(e) for e, _ in evaluated}
        best_idx = int(np.argmax(scores)) if len(scores) > 0 else 0
        best_expr = evaluated[best_idx][0] if evaluated else Terminal("$volume")
        return GenerationStats(
            gen=gen,
            best_fitness=float(scores.max()) if len(scores) > 0 else float("-inf"),
            mean_fitness=float(finite.mean()) if len(finite) > 0 else float("-inf"),
            median_fitness=float(np.median(finite)) if len(finite) > 0 else float("-inf"),
            n_unique=len(unique_hashes),
            n_invalid=int((~finite_mask).sum()),
            best_expr_str=best_expr.to_qlib_string(),
        )

    # ------------------------------------------------------------------
    # Checkpointing
    # ------------------------------------------------------------------

    def save_checkpoint(self, path: str | Path) -> Path:
        """Write engine state to a JSON file. Factor-values cache is not
        persisted (rebuilt lazily on next evaluation)."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        state: dict = {
            "gp_config": asdict(self.config),
            # Tag every score in ``fitness_cache`` / ``all_evaluated`` with
            # the evaluator method that produced them. ``load_checkpoint``
            # uses this to decide whether cached scores can be trusted on
            # resume or must be discarded — see the Codex P1 note on the
            # ``FITNESS_EVALUATOR_METHOD`` constant above.
            "evaluator_method": FITNESS_EVALUATOR_METHOD,
            # Coverage-cache key the cached scores were produced under
            # ("all_cells" / "members:<mask fingerprint>"; Codex P1+P2 on
            # #217). ``run`` discards the cache on resume if this disagrees
            # with the resumed run's key (mode OR mask change).
            "coverage_key": (
                self._coverage_key
                if self._coverage_key is not None
                else _coverage_key_for(self._universe_mask)
            ),
            "current_gen": self.current_gen,
            "rng_state": _serialise_rng_state(self.rng.getstate()),
            "fitness_cache": {
                str(h): score for h, score in self.fitness_cache.items()
            },
            "population": [e.to_dict() for e in self.population],
            "history": [asdict(s) for s in self.history],
            "all_evaluated": {
                str(h): _pool_entry_to_dict(entry)
                for h, entry in self._all_evaluated.items()
            },
        }
        p.write_text(json.dumps(state, indent=2, sort_keys=False), encoding="utf-8")
        return p

    @classmethod
    def load_checkpoint(
        cls,
        path: str | Path,
        *,
        fitness_config: FitnessConfig,
    ) -> GPEngine:
        """Reconstruct an engine from a checkpoint file.

        If the checkpoint was written by an engine using a different
        evaluator method (``"rank"`` before PR #142, ``"normal"`` after),
        the cached fitness scores are not comparable to scores this
        engine would produce. In that case we discard
        ``fitness_cache`` and ``_all_evaluated`` so resumed generations
        re-score from scratch; ``population`` / ``current_gen`` / ``rng``
        are preserved so determinism of the resumed run is unaffected.

        Legacy checkpoints (pre PR #142) have no ``evaluator_method``
        field — they were written under ``method="rank"``, so the same
        invalidation path triggers.
        """
        p = Path(path)
        state = json.loads(p.read_text(encoding="utf-8"))
        gp_config = GPConfig(**state["gp_config"])
        engine = cls(gp_config, fitness_config)
        engine.rng.setstate(_deserialise_rng_state(state["rng_state"]))
        engine.current_gen = int(state["current_gen"])
        engine.population = [
            Expression.from_dict(d) for d in state["population"]
        ]
        engine.history = [GenerationStats(**s) for s in state["history"]]
        # Restore the coverage-cache key so ``run`` can detect a mode OR mask
        # change on resume and discard incomparable cached scores (Codex
        # P1+P2 on #217). Fall back to a mid-review ``coverage_denominator``
        # field, then to all-cells for legacy checkpoints that predate the
        # universe mask.
        engine._coverage_key = (
            state.get("coverage_key")
            or state.get("coverage_denominator")
            or "all_cells"
        )

        stored_method = state.get("evaluator_method")
        if stored_method != FITNESS_EVALUATOR_METHOD:
            _log.warning(
                "checkpoint evaluator_method=%r != engine %r — discarding "
                "fitness_cache (%d entries) and all_evaluated (%d entries); "
                "resumed generation will re-score from scratch to avoid "
                "mixing semantics across the method change.",
                stored_method,
                FITNESS_EVALUATOR_METHOD,
                len(state.get("fitness_cache", {})),
                len(state.get("all_evaluated", {})),
            )
            engine.fitness_cache = {}
            engine._all_evaluated = {}
            return engine

        engine.fitness_cache = {
            int(h): float(score) for h, score in state["fitness_cache"].items()
        }
        engine._all_evaluated = {
            int(h): _pool_entry_from_dict(d)
            for h, d in state["all_evaluated"].items()
        }
        return engine


def _mask_fingerprint(mask: pd.DataFrame) -> str:
    """Stable 16-hex content fingerprint of a universe-membership mask.

    Captures index, columns, and the boolean cells so two different masks
    (different universe or date range) yield different keys — needed to
    invalidate cached scores when the mask CHANGES across a reuse/resume,
    not only when the coarse members/all-cells mode changes (Codex P2 on
    #217). Uses sha256 (not the salted builtin ``hash``) so it is stable
    across processes and round-trips through a checkpoint.
    """
    h = hashlib.sha256()
    h.update("|".join(map(str, mask.index)).encode("utf-8"))
    h.update(b"\x00cols\x00")
    h.update("|".join(map(str, mask.columns)).encode("utf-8"))
    h.update(np.ascontiguousarray(mask.to_numpy(dtype=bool)).tobytes())
    return h.hexdigest()[:16]


def _coverage_key_for(mask: pd.DataFrame | None) -> str:
    """Coverage-cache key for a run: ``"all_cells"`` when no mask, else
    ``"members:<fingerprint>"`` so a different mask invalidates the cache."""
    return "all_cells" if mask is None else f"members:{_mask_fingerprint(mask)}"


def _serialise_rng_state(state):
    """``random.Random.getstate`` returns a tuple of (version, tuple, None)."""
    version, internal, gauss = state
    return {
        "version": version,
        "internal": list(internal),
        "gauss": gauss,
    }


def _deserialise_rng_state(d):
    return (d["version"], tuple(d["internal"]), d["gauss"])


def _pool_entry_to_dict(entry: PoolEntry) -> dict:
    return {
        "expr": entry.expr.to_dict(),
        "fitness": entry.fitness,
        "ic_mean": entry.ic_mean,
        "ic_std": entry.ic_std,
        "ir": entry.ir,
        "rank_ic_mean": entry.rank_ic_mean,
        "rank_ic_std": entry.rank_ic_std,
        "rank_ir": entry.rank_ir,
        "turnover_daily": entry.turnover_daily,
        "coverage": entry.coverage,
        "n_obs_per_day_min": entry.n_obs_per_day_min,
        "expr_size": entry.expr_size,
        "method": entry.method,
    }


def _pool_entry_from_dict(d: dict) -> PoolEntry:
    expr = Expression.from_dict(d["expr"])
    # A pool entry dict that lacks the ``method`` field is by definition
    # pre-method-tagging: we have no record of which IC method produced
    # its ``ic_mean`` (it could be Pearson under the new contract or
    # Spearman under the pre-#142 contract that double-counted rank
    # IC). Default to ``LEGACY_METHOD_TAG`` so downstream validators
    # and promoters know not to treat the metric as Pearson-comparable.
    # Promoting the missing case to ``"normal"`` would silently
    # mislabel rank-derived numbers as Pearson — exactly the
    # cross-version metric corruption Codex flagged on PR #143.
    #
    # Cross-method checkpoints are already invalidated upstream by
    # ``load_checkpoint`` (the ``evaluator_method`` guard added in
    # PR #142), so in practice this default fires only when an
    # individual entry's payload is missing the tag inside an
    # otherwise-compatible checkpoint (partial migrations, hand-edited
    # files, parquet pools spliced into a JSON checkpoint, etc.).
    return PoolEntry(
        expr=expr,
        fitness=float(d["fitness"]),
        ic_mean=float(d["ic_mean"]),
        ic_std=float(d["ic_std"]),
        ir=float(d["ir"]),
        rank_ic_mean=float(d["rank_ic_mean"]),
        rank_ic_std=float(d["rank_ic_std"]),
        rank_ir=float(d["rank_ir"]),
        turnover_daily=float(d["turnover_daily"]),
        coverage=float(d["coverage"]),
        n_obs_per_day_min=int(d["n_obs_per_day_min"]),
        expr_size=int(d["expr_size"]),
        expr_hash=hash(expr),
        method=str(d.get("method", LEGACY_METHOD_TAG)),
    )
