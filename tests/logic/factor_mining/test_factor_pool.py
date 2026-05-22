"""Tests for FactorPool: dedup, novelty, persistence."""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

from src.factor_mining.expression import OperatorCall, Terminal
from src.factor_mining.factor_pool import (
    POOL_EXPR_JSON_FILENAME,
    POOL_PARQUET_FILENAME,
    FactorPool,
    PoolEntry,
)


def _make_entry(expr, fitness=1.0, ic_mean=0.05, ir=0.5, turnover=0.1, coverage=0.95):
    return PoolEntry(
        expr=expr,
        fitness=fitness,
        ic_mean=ic_mean,
        ic_std=0.1,
        ir=ir,
        rank_ic_mean=ic_mean,
        rank_ic_std=0.1,
        rank_ir=ir,
        turnover_daily=turnover,
        coverage=coverage,
        n_obs_per_day_min=20,
        expr_size=2,
        expr_hash=hash(expr),
    )


def _expr_cs_rank_volume():
    return OperatorCall("cs_rank", (Terminal("$volume"),))


def _expr_cs_rank_money():
    return OperatorCall("cs_rank", (Terminal("$money"),))


# ---------------------------------------------------------------------------
# add / dedup
# ---------------------------------------------------------------------------


def test_add_new_entry_returns_true():
    pool = FactorPool()
    e = _make_entry(_expr_cs_rank_volume())
    assert pool.add(e) is True
    assert len(pool) == 1


def test_add_duplicate_returns_false():
    pool = FactorPool()
    expr = _expr_cs_rank_volume()
    pool.add(_make_entry(expr, fitness=1.0))
    second = _make_entry(expr, fitness=2.0)
    assert pool.add(second) is False
    # The first wins; second is discarded.
    assert len(pool) == 1
    only = pool.all_entries()[0]
    assert only.fitness == 1.0


def test_commutative_dedup():
    """add($volume, $money) and add($money, $volume) hash identically
    (Phase 1 commutative-sort), so the pool dedups them."""
    pool = FactorPool()
    a = OperatorCall("add", (Terminal("$volume"), Terminal("$money")))
    b = OperatorCall("add", (Terminal("$money"), Terminal("$volume")))
    # These aren't CSF-typed at root, but for pool dedup we only test the hash.
    # Wrap them in cs_rank to make them legal CSF roots.
    wrapped_a = OperatorCall("cs_rank", (a,))
    wrapped_b = OperatorCall("cs_rank", (b,))
    assert pool.add(_make_entry(wrapped_a)) is True
    assert pool.add(_make_entry(wrapped_b)) is False
    assert len(pool) == 1


def test_contains_uses_expr_hash():
    pool = FactorPool()
    expr = _expr_cs_rank_volume()
    pool.add(_make_entry(expr))
    assert hash(expr) in pool
    assert 0 not in pool


# ---------------------------------------------------------------------------
# top_k
# ---------------------------------------------------------------------------


def test_top_k_orders_by_fitness_desc():
    pool = FactorPool()
    pool.add(_make_entry(_expr_cs_rank_volume(), fitness=1.0))
    pool.add(_make_entry(_expr_cs_rank_money(), fitness=3.0))
    pool.add(_make_entry(OperatorCall("cs_zscore", (Terminal("$volume"),)), fitness=2.0))
    top = pool.top_k(2)
    assert [e.fitness for e in top] == [3.0, 2.0]


def test_top_k_zero_returns_empty():
    pool = FactorPool()
    pool.add(_make_entry(_expr_cs_rank_volume()))
    assert pool.top_k(0) == []


def test_top_k_by_ir():
    pool = FactorPool()
    pool.add(_make_entry(_expr_cs_rank_volume(), ir=0.3))
    pool.add(_make_entry(_expr_cs_rank_money(), ir=0.8))
    top = pool.top_k(1, by="ir")
    assert top[0].ir == 0.8


def test_top_k_unknown_key_raises():
    pool = FactorPool()
    pool.add(_make_entry(_expr_cs_rank_volume()))
    with pytest.raises(ValueError, match="Unknown sort key"):
        pool.top_k(1, by="not_a_key")


# ---------------------------------------------------------------------------
# correlation_with
# ---------------------------------------------------------------------------


def test_correlation_with_empty_pool_returns_zero():
    pool = FactorPool()
    new = pd.DataFrame(np.random.default_rng(0).normal(0, 1, size=(20, 5)))
    assert pool.correlation_with(new, {}) == 0.0


def test_correlation_with_self_returns_one():
    pool = FactorPool()
    pool.add(_make_entry(_expr_cs_rank_volume()))
    new = pd.DataFrame(
        np.random.default_rng(0).normal(0, 1, size=(20, 5)),
        index=pd.date_range("2024-01-01", periods=20),
        columns=list("ABCDE"),
    )
    existing = {hash(_expr_cs_rank_volume()): new.copy()}
    assert pool.correlation_with(new, existing) == pytest.approx(1.0, abs=1e-9)


def test_correlation_with_orthogonal_returns_near_zero():
    pool = FactorPool()
    rng = np.random.default_rng(7)
    new = pd.DataFrame(
        rng.normal(0, 1, size=(200, 10)),
        index=pd.date_range("2024-01-01", periods=200),
        columns=list("ABCDEFGHIJ"),
    )
    other = pd.DataFrame(
        rng.normal(0, 1, size=(200, 10)),
        index=pd.date_range("2024-01-01", periods=200),
        columns=list("ABCDEFGHIJ"),
    )
    existing = {12345: other}
    corr = pool.correlation_with(new, existing)
    assert corr < 0.3


# ---------------------------------------------------------------------------
# Persistence (save / load round-trip)
# ---------------------------------------------------------------------------


def test_save_writes_two_files(tmp_path):
    pool = FactorPool()
    pool.add(_make_entry(_expr_cs_rank_volume()))
    out = pool.save(tmp_path / "pool")
    assert (out / POOL_PARQUET_FILENAME).is_file()
    assert (out / POOL_EXPR_JSON_FILENAME).is_file()


def test_save_load_round_trip(tmp_path):
    pool = FactorPool()
    pool.add(_make_entry(_expr_cs_rank_volume(), fitness=1.5))
    pool.add(_make_entry(_expr_cs_rank_money(), fitness=2.5))
    pool.add(_make_entry(OperatorCall("cs_zscore", (Terminal("$volume"),)), fitness=0.5))

    pool.save(tmp_path / "pool")
    loaded = FactorPool.load(tmp_path / "pool")

    assert len(loaded) == len(pool)
    # All hashes survive
    original_hashes = {hash(e.expr) for e in pool.all_entries()}
    loaded_hashes = {hash(e.expr) for e in loaded.all_entries()}
    assert loaded_hashes == original_hashes

    # Top-k order matches
    a = pool.top_k(3)
    b = loaded.top_k(3)
    assert [e.fitness for e in a] == [e.fitness for e in b]


def test_save_load_empty_pool(tmp_path):
    pool = FactorPool()
    pool.save(tmp_path / "pool")
    loaded = FactorPool.load(tmp_path / "pool")
    assert len(loaded) == 0


def test_load_missing_parquet_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        FactorPool.load(tmp_path / "does_not_exist")


# ---------------------------------------------------------------------------
# D5 strict gate
# ---------------------------------------------------------------------------


def test_factor_pool_does_not_import_qlib_or_pit_directly():
    import src.factor_mining.factor_pool as mod

    src = inspect.getsource(mod)
    assert "from qlib" not in src
    assert "qlib.data" not in src
    assert "qlib.init" not in src
    assert "from src.pit" not in src
    assert "import src.pit" not in src


import pytest  # noqa: E402
