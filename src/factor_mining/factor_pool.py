"""Factor pool: dedup-by-hash, novelty scoring, parquet+JSON persistence.

Implements ``factor_mining_design.md`` §6.2 pool format:

- ``factor_pool.parquet`` — one row per ``PoolEntry``, columns =
  scalar metrics + ``expr_hash``. Fast to query with pandas /
  pyarrow.
- ``factor_expressions.json`` — mapping ``expr_hash`` → serialised
  expression dict. Human-inspectable; the AST is small enough that
  JSON is the right choice.

No qlib import, no ``src.pit`` import. Pure dedup + correlation +
serialisation arithmetic.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .evaluator import EvaluationResult
from .expression import Expression

_log = logging.getLogger(__name__)

POOL_PARQUET_FILENAME = "factor_pool.parquet"
POOL_EXPR_JSON_FILENAME = "factor_expressions.json"

# Default ``method`` for entries loaded from a parquet that predates the
# method-tagging contract (PR2). Old pools were produced by the miner
# bug where ``ic_mean`` actually carried Spearman IC; we tag them as
# "unknown" so downstream consumers (validators, promoters) can decide
# whether to trust ic_mean rather than silently assuming Pearson.
LEGACY_METHOD_TAG = "unknown"


@dataclass(frozen=True)
class PoolEntry:
    """A single entry in the factor pool.

    The structural ``expr_hash`` is the dedup key. Metric scalars are
    persisted to parquet; the ``Expression`` is persisted to JSON via
    ``Expression.to_dict``.

    ``method`` records which IC method produced ``ic_mean`` (``"normal"``
    for Pearson, ``"rank"`` for Spearman, or :data:`LEGACY_METHOD_TAG`
    for entries loaded from a pre-PR2 parquet without the column).
    Downstream validators and promoters use it to decide whether to
    compare ``ic_mean`` across runs.
    """

    expr: Expression
    fitness: float
    ic_mean: float
    ic_std: float
    ir: float
    rank_ic_mean: float
    rank_ic_std: float
    rank_ir: float
    turnover_daily: float
    coverage: float
    n_obs_per_day_min: int
    expr_size: int
    expr_hash: int
    method: str = "normal"

    @classmethod
    def from_result(
        cls,
        expr: Expression,
        result: EvaluationResult,
        fitness: float,
        expr_size: int,
        method: str = "normal",
    ) -> PoolEntry:
        return cls(
            expr=expr,
            fitness=float(fitness),
            ic_mean=float(result.ic_mean),
            ic_std=float(result.ic_std),
            ir=float(result.ir),
            rank_ic_mean=float(result.rank_ic_mean),
            rank_ic_std=float(result.rank_ic_std),
            rank_ir=float(result.rank_ir),
            turnover_daily=float(result.turnover_daily),
            coverage=float(result.coverage),
            n_obs_per_day_min=int(result.n_obs_per_day_min),
            expr_size=int(expr_size),
            expr_hash=hash(expr),
            method=str(method),
        )


class FactorPool:
    """Dedup-by-hash factor pool with parquet+JSON persistence."""

    def __init__(self) -> None:
        self._entries: dict[int, PoolEntry] = {}

    # ------------------------------------------------------------------
    # Mutators
    # ------------------------------------------------------------------

    def add(self, entry: PoolEntry) -> bool:
        """Add ``entry`` if not previously added (by structural hash).

        Returns True if the entry was added, False if it was a
        duplicate. Commutative-equivalent expressions (e.g.
        ``add($volume, $money)`` vs ``add($money, $volume)``) hash
        identically per Phase 1's structural-hash contract, so the
        second add is dropped here.
        """
        if entry.expr_hash in self._entries:
            return False
        self._entries[entry.expr_hash] = entry
        return True

    def clear(self) -> None:
        self._entries.clear()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, expr_hash: object) -> bool:
        return isinstance(expr_hash, int) and expr_hash in self._entries

    def all_entries(self) -> list[PoolEntry]:
        return list(self._entries.values())

    def top_k(self, k: int, by: str = "fitness") -> list[PoolEntry]:
        """Top-``k`` entries by the named scalar attribute (default
        ``"fitness"``). Larger is better."""
        entries = list(self._entries.values())
        if not hasattr(PoolEntry, by) and by not in PoolEntry.__dataclass_fields__:
            raise ValueError(f"Unknown sort key: {by!r}")
        entries.sort(key=lambda e: getattr(e, by), reverse=True)
        return entries[: max(0, int(k))]

    def correlation_with(
        self,
        factor_values: pd.DataFrame,
        existing_values: Mapping[int, pd.DataFrame] | None = None,
    ) -> float:
        """Max absolute Pearson correlation between ``factor_values``
        and the supplied ``existing_values`` mapping (expr_hash →
        factor DataFrame).

        Returns 0.0 if the pool is empty or no overlapping cells are
        available. The novelty term of fitness uses this value (the
        GP engine in Phase 3 maintains ``existing_values`` as a cache
        of recently-scored factor values).
        """
        if not existing_values or factor_values.empty:
            return 0.0
        max_abs = 0.0
        new_stack = factor_values.stack(future_stack=True)
        if new_stack.empty:
            return 0.0
        for _hash, other in existing_values.items():
            if other is None or other.empty:
                continue
            other_stack = other.stack(future_stack=True)
            joined = pd.concat({"new": new_stack, "old": other_stack}, axis=1).dropna()
            if len(joined) < 3:
                continue
            corr = joined["new"].corr(joined["old"])
            if not np.isfinite(corr):
                continue
            max_abs = max(max_abs, abs(float(corr)))
        return max_abs

    # ------------------------------------------------------------------
    # Persistence (v1 §6.2 format)
    # ------------------------------------------------------------------

    def save(self, dir_path: str | Path) -> Path:
        """Write the pool to ``dir_path/{factor_pool.parquet,factor_expressions.json}``.

        Returns the resolved directory path.
        """
        d = Path(dir_path)
        d.mkdir(parents=True, exist_ok=True)
        entries = list(self._entries.values())
        if entries:
            metrics = pd.DataFrame(
                [
                    {
                        "expr_hash": str(e.expr_hash),
                        "fitness": e.fitness,
                        "ic_mean": e.ic_mean,
                        "ic_std": e.ic_std,
                        "ir": e.ir,
                        "rank_ic_mean": e.rank_ic_mean,
                        "rank_ic_std": e.rank_ic_std,
                        "rank_ir": e.rank_ir,
                        "turnover_daily": e.turnover_daily,
                        "coverage": e.coverage,
                        "n_obs_per_day_min": e.n_obs_per_day_min,
                        "expr_size": e.expr_size,
                        "method": e.method,
                    }
                    for e in entries
                ]
            )
        else:
            metrics = pd.DataFrame(
                columns=[
                    "expr_hash", "fitness", "ic_mean", "ic_std", "ir",
                    "rank_ic_mean", "rank_ic_std", "rank_ir",
                    "turnover_daily", "coverage", "n_obs_per_day_min",
                    "expr_size", "method",
                ]
            )
        metrics.to_parquet(d / POOL_PARQUET_FILENAME, index=False)
        expr_map = {
            str(e.expr_hash): e.expr.to_dict()
            for e in entries
        }
        with (d / POOL_EXPR_JSON_FILENAME).open("w", encoding="utf-8") as fh:
            json.dump(expr_map, fh, indent=2, sort_keys=True)
        return d

    @classmethod
    def load(cls, dir_path: str | Path) -> FactorPool:
        """Reconstruct a pool from ``dir_path``.

        Verifies that every ``expr_hash`` in the parquet has a
        matching AST in the JSON and that the AST deserialises into
        a valid :class:`Expression`. Does NOT cross-check the
        deserialised expression's Python ``hash()`` against
        ``expr_hash`` — Python's ``hash()`` is randomised per
        interpreter process (``PYTHONHASHSEED``), so a hash recorded
        in one run is not comparable to one computed in the next.
        ``expr_hash`` is therefore best understood as a stable
        **cross-reference** between the parquet rows and the JSON
        map, not a cryptographic identity hash. Detecting silent
        AST-tampering (a different expression installed under an
        existing key) would require a stable canonical hash (e.g.
        sha256 of ``Expression.to_dict``) persisted alongside the
        parquet — a separate schema change, not done here.

        Codex P1 on PR #165 flagged that an earlier attempt at this
        cross-process hash check was rejecting legitimately-saved
        pools because the save-time hash and load-time hash differ
        by hash seed; the check has been intentionally removed and
        the docstring updated to match what the code actually
        guarantees.
        """
        d = Path(dir_path)
        metrics_path = d / POOL_PARQUET_FILENAME
        json_path = d / POOL_EXPR_JSON_FILENAME
        if not metrics_path.exists():
            raise FileNotFoundError(f"{metrics_path} does not exist")
        if not json_path.exists():
            raise FileNotFoundError(f"{json_path} does not exist")
        metrics = pd.read_parquet(metrics_path)
        with json_path.open("r", encoding="utf-8") as fh:
            expr_map = json.load(fh)
        has_method = "method" in metrics.columns
        if not has_method and not metrics.empty:
            _log.info(
                "pool at %s has no 'method' column (predates PR2); "
                "tagging entries as method=%r so callers know ic_mean "
                "semantics may have been Spearman under the old miner",
                d, LEGACY_METHOD_TAG,
            )
        pool = cls()
        for _, row in metrics.iterrows():
            h_key = str(row["expr_hash"])
            if h_key not in expr_map:
                raise ValueError(
                    f"pool integrity: expr_hash {h_key} present in parquet "
                    "but missing from JSON"
                )
            expr = Expression.from_dict(expr_map[h_key])
            # Re-hash under the current process's hash seed so the
            # in-memory entry's expr_hash is consistent with any other
            # ``hash(expr)`` call this process makes. NOT comparable to
            # ``h_key`` across processes; see docstring above for why
            # a stable identity check would need a schema change.
            actual_hash = hash(expr)
            entry = PoolEntry(
                expr=expr,
                fitness=float(row["fitness"]),
                ic_mean=float(row["ic_mean"]),
                ic_std=float(row["ic_std"]),
                ir=float(row["ir"]),
                rank_ic_mean=float(row["rank_ic_mean"]),
                rank_ic_std=float(row["rank_ic_std"]),
                rank_ir=float(row["rank_ir"]),
                turnover_daily=float(row["turnover_daily"]),
                coverage=float(row["coverage"]),
                n_obs_per_day_min=int(row["n_obs_per_day_min"]),
                expr_size=int(row["expr_size"]),
                expr_hash=actual_hash,
                method=str(row["method"]) if has_method else LEGACY_METHOD_TAG,
            )
            pool.add(entry)
        return pool
