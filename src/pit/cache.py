"""Bounded LRU cache for PIT query results.

Why bounded
-----------
Per docs/pit/pit_universe_design.md §6, the default cache size is 256.
Long backtests that re-query the same (universe, dates, fields) tuples
will OOM if the cache is unbounded — empirically ~5000 stocks × 5 years
× 6 float32 fields ≈ 1.5 GB per cached panel, so 256 of those is the
calibrated limit for a ~8 GB working set.

The cache key is intentionally NOT the raw arguments — it's a frozen
tuple of ``(universe_name, start, end, frozenset(fields))`` so equal
queries hit cache regardless of argument ordering. Field lists are
normalised to a frozenset because qlib accepts ``["$close", "$volume"]``
or ``["$volume", "$close"]`` equivalently.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Hashable
from typing import Generic, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class LRUCache(Generic[K, V]):
    """Simple ordered-dict LRU cache.

    Not thread-safe — the PITDataProvider is meant to be used from a
    single backtest / training thread. If concurrent access becomes a
    requirement, wrap calls in a lock at the caller.
    """

    def __init__(self, maxsize: int = 256) -> None:
        if maxsize < 1:
            raise ValueError(f"maxsize must be >= 1, got {maxsize}")
        self._maxsize = maxsize
        self._data: OrderedDict[K, V] = OrderedDict()

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: K) -> bool:
        return key in self._data

    def get(self, key: K) -> V | None:
        """Return value and mark key as most-recently-used; or None if missing."""
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return self._data[key]

    def put(self, key: K, value: V) -> None:
        """Insert / update; evict least-recently-used if over capacity."""
        if key in self._data:
            self._data.move_to_end(key)
            self._data[key] = value
            return
        self._data[key] = value
        if len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()
