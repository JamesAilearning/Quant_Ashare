"""Tests for the MinedFactor handler bridge (src/data/mined_factor_handler.py)."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.data.feature_dataset_builder import (
    FeatureDatasetConfig,
    _reset_feature_handler_registry_to_defaults,
    list_supported_feature_handlers,
)
from src.data.mined_factor_handler import (
    MinedFactorBundle,
    MinedFactorHandlerError,
    _column_name_for,
    _entry_sort_key,
    make_mined_factor_features,
    register_mined_factor_handler,
)
from src.factor_mining.expression import OperatorCall, Terminal
from src.factor_mining.factor_pool import FactorPool, PoolEntry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _synthetic_panel(n_tickers=6, n_dates=40, seed=0):
    rng = np.random.default_rng(seed)
    tickers = [f"T{i:03d}" for i in range(n_tickers)]
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")
    panel = {}
    for f in ["$open", "$high", "$low", "$close", "$volume", "$money"]:
        data = rng.normal(100, 5, size=(n_dates, n_tickers))
        panel[f] = pd.DataFrame(
            data,
            index=pd.Index(dates, name="datetime"),
            columns=pd.Index(tickers, name="instrument"),
        )
    return panel


def _make_entry(expr, fitness):
    return PoolEntry(
        expr=expr,
        fitness=fitness,
        ic_mean=0.05,
        ic_std=0.10,
        ir=0.5,
        rank_ic_mean=0.04,
        rank_ic_std=0.08,
        rank_ir=0.5,
        turnover_daily=0.10,
        coverage=0.95,
        n_obs_per_day_min=20,
        expr_size=2,
        expr_hash=hash(expr),
    )


def _seed_pool(tmp_dir, fitnesses=(1.5, 2.5, 0.7)):
    pool = FactorPool()
    exprs = [
        OperatorCall("cs_rank", (Terminal("$volume"),)),
        OperatorCall("cs_rank", (Terminal("$money"),)),
        OperatorCall("cs_zscore", (Terminal("$volume"),)),
    ]
    for expr, fit in zip(exprs, fitnesses, strict=True):
        pool.add(_make_entry(expr, fit))
    pool.save(tmp_dir)
    return pool


def _config():
    return FeatureDatasetConfig(
        instruments="csi300",
        feature_handler="MinedFactor",
        train_start="2024-01-01",
        train_end="2024-01-20",
        valid_start="2024-01-21",
        valid_end="2024-01-30",
        test_start="2024-01-31",
        test_end="2024-02-08",
    )


# ---------------------------------------------------------------------------
# MinedFactorBundle
# ---------------------------------------------------------------------------


def test_bundle_accepts_valid_pool(tmp_path):
    _seed_pool(tmp_path / "pool")
    bundle = MinedFactorBundle(pool_dir=tmp_path / "pool")
    assert bundle.pool_dir == tmp_path / "pool"
    assert bundle.pit_provider_uri == ""


def test_bundle_coerces_str_to_path(tmp_path):
    _seed_pool(tmp_path / "pool")
    bundle = MinedFactorBundle(pool_dir=str(tmp_path / "pool"))
    assert isinstance(bundle.pool_dir, Path)


def test_bundle_rejects_missing_dir(tmp_path):
    with pytest.raises(MinedFactorHandlerError, match="does not exist"):
        MinedFactorBundle(pool_dir=tmp_path / "nonexistent")


def test_bundle_rejects_missing_parquet(tmp_path):
    d = tmp_path / "pool"
    d.mkdir()
    (d / "factor_expressions.json").write_text("{}")
    with pytest.raises(MinedFactorHandlerError, match="factor_pool.parquet"):
        MinedFactorBundle(pool_dir=d)


def test_bundle_rejects_missing_json(tmp_path):
    d = tmp_path / "pool"
    d.mkdir()
    # Write a minimal parquet stand-in via pandas
    pd.DataFrame({"expr_hash": []}).to_parquet(d / "factor_pool.parquet", index=False)
    with pytest.raises(MinedFactorHandlerError, match="factor_expressions.json"):
        MinedFactorBundle(pool_dir=d)


# ---------------------------------------------------------------------------
# make_mined_factor_features
# ---------------------------------------------------------------------------


def test_make_features_returns_multiindex_dataframe(tmp_path):
    _seed_pool(tmp_path / "pool")
    bundle = MinedFactorBundle(pool_dir=tmp_path / "pool")
    panel = _synthetic_panel()
    features = make_mined_factor_features(bundle, _config(), panel=panel)
    assert isinstance(features, pd.DataFrame)
    assert features.index.names == ["instrument", "datetime"]
    assert features.shape[1] == 3  # 3 pool entries
    # Each column is named mf_<16 hex chars>
    for col in features.columns:
        assert col.startswith("mf_")
        assert len(col) == 3 + 16


def test_make_features_column_order_is_fitness_desc(tmp_path):
    _seed_pool(tmp_path / "pool", fitnesses=(1.5, 2.5, 0.7))
    bundle = MinedFactorBundle(pool_dir=tmp_path / "pool")
    panel = _synthetic_panel()
    features = make_mined_factor_features(bundle, _config(), panel=panel)
    # We loaded with fitnesses [1.5, 2.5, 0.7] — fitness desc order is
    # 2.5, 1.5, 0.7 → second, first, third expression respectively.
    pool = FactorPool.load(tmp_path / "pool")
    sorted_entries = sorted(pool.all_entries(), key=_entry_sort_key)
    expected_cols = [_column_name_for(e) for e in sorted_entries]
    assert list(features.columns) == expected_cols


def test_make_features_deterministic_across_loads(tmp_path):
    _seed_pool(tmp_path / "pool")
    bundle = MinedFactorBundle(pool_dir=tmp_path / "pool")
    panel = _synthetic_panel()
    f1 = make_mined_factor_features(bundle, _config(), panel=panel)
    f2 = make_mined_factor_features(bundle, _config(), panel=panel)
    pd.testing.assert_frame_equal(f1, f2)


def test_make_features_empty_pool_raises(tmp_path):
    pool = FactorPool()
    pool.save(tmp_path / "pool")
    bundle = MinedFactorBundle(pool_dir=tmp_path / "pool")
    with pytest.raises(MinedFactorHandlerError, match="empty"):
        make_mined_factor_features(bundle, _config(), panel=_synthetic_panel())


def test_make_features_pit_mode_empty_uri_raises(tmp_path):
    _seed_pool(tmp_path / "pool")
    bundle = MinedFactorBundle(
        pool_dir=tmp_path / "pool",
        pit_provider_uri="",  # PIT mode requires non-empty
        delisted_registry_path="",
    )
    with pytest.raises(MinedFactorHandlerError, match="pit_provider_uri"):
        # No panel supplied → PIT branch — and the bundle's PIT URIs are empty.
        make_mined_factor_features(bundle, _config())


# ---------------------------------------------------------------------------
# register_mined_factor_handler
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry():
    """Ensure each test starts with the default registry (Alpha158 only)."""
    yield
    _reset_feature_handler_registry_to_defaults()


def test_register_handler_adds_minedfactor_to_registry(tmp_path):
    _seed_pool(tmp_path / "pool")
    bundle = MinedFactorBundle(pool_dir=tmp_path / "pool")
    assert "MinedFactor" not in list_supported_feature_handlers()
    register_mined_factor_handler(bundle)
    assert "MinedFactor" in list_supported_feature_handlers()


def test_register_handler_under_custom_name(tmp_path):
    _seed_pool(tmp_path / "pool")
    bundle = MinedFactorBundle(pool_dir=tmp_path / "pool")
    register_mined_factor_handler(bundle, name="MinedFactor:prod")
    assert "MinedFactor:prod" in list_supported_feature_handlers()


def test_register_handler_replace_rebinds(tmp_path):
    _seed_pool(tmp_path / "pool_a")
    _seed_pool(tmp_path / "pool_b")
    a = MinedFactorBundle(pool_dir=tmp_path / "pool_a")
    b = MinedFactorBundle(pool_dir=tmp_path / "pool_b")
    register_mined_factor_handler(a)
    # Replacing without replace=True must fail
    with pytest.raises(Exception, match="already registered"):
        register_mined_factor_handler(b)
    # With replace=True it succeeds
    register_mined_factor_handler(b, replace=True)


# ---------------------------------------------------------------------------
# Lazy qlib import (D5 + qlib-availability decoupling)
# ---------------------------------------------------------------------------


def test_importing_module_does_not_pull_qlib():
    # Drop any cached qlib modules from earlier tests (some other test
    # in the suite might have imported qlib).
    qlib_keys = [k for k in sys.modules if k.startswith("qlib")]
    for k in qlib_keys:
        del sys.modules[k]

    # Re-import the handler module (drop it first so the re-import goes
    # through __init__ logic again).
    if "src.data.mined_factor_handler" in sys.modules:
        del sys.modules["src.data.mined_factor_handler"]
    import src.data.mined_factor_handler  # noqa: F401

    # Importing the module SHALL NOT pull qlib.
    assert not any(k.startswith("qlib") for k in sys.modules), (
        f"qlib unexpectedly imported by mined_factor_handler module: "
        f"{[k for k in sys.modules if k.startswith('qlib')]}"
    )


def test_module_source_has_no_top_level_qlib_import():
    """Belt-and-suspenders: the module's AST MUST NOT contain a
    top-level ``from qlib`` or ``import qlib`` statement. The lazy
    imports inside function bodies are OK.

    Uses AST inspection (not substring matching) so docstring text
    that mentions ``from qlib`` doesn't trip the check.
    """
    import ast

    import src.data.mined_factor_handler as mod

    tree = ast.parse(inspect.getsource(mod))
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("qlib"), (
                    f"Top-level `import {alias.name}` found in "
                    "mined_factor_handler.py — qlib must be imported lazily"
                )
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith("qlib"), (
                f"Top-level `from {module} import ...` found in "
                "mined_factor_handler.py — qlib must be imported lazily"
            )
