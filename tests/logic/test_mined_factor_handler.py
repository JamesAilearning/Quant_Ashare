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
    assert features.index.names == ["datetime", "instrument"]
    assert features.shape[1] == 3  # 3 pool entries
    # Each column is named mf_<16 hex chars>
    for col in features.columns:
        assert col.startswith("mf_")
        assert len(col) == 3 + 16


def test_make_features_index_order_is_datetime_then_instrument(tmp_path):
    """Regression for the qlib StaticDataLoader integration bug:
    qlib's ``StaticDataLoader.load(instruments, start, end)`` runs
    ``df.loc(axis=0)[:, instruments]``, which treats level 0 as
    datetime and level 1 as the instrument-filter axis. The original
    implementation produced (instrument, datetime) order, which made
    pandas try to look up ticker codes against the datetime level and
    raise ``KeyError: 'SH600000'`` deep inside a fold."""
    _seed_pool(tmp_path / "pool")
    bundle = MinedFactorBundle(pool_dir=tmp_path / "pool")
    panel = _synthetic_panel()
    features = make_mined_factor_features(bundle, _config(), panel=panel)
    assert features.index.names == ["datetime", "instrument"]
    # Level 0 values are pandas Timestamps, level 1 are ticker strings.
    assert isinstance(features.index.get_level_values(0)[0], pd.Timestamp)
    assert isinstance(features.index.get_level_values(1)[0], str)
    # And the index is sorted, as StaticDataLoader assumes.
    assert features.index.is_monotonic_increasing


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


def test_factory_loads_pit_panel_only_once(tmp_path, monkeypatch):
    """T2-6: the registered PIT-mode factory must load the OHLCV panel
    ONCE. It previously resolved the panel for the features and AGAIN for
    the label, doubling the (expensive) per-fold PIT load. We count the
    PIT-load branch of ``_resolve_panel`` (the no-panel call); the panel
    must then be reused for the label, not re-loaded."""
    from src.data import mined_factor_handler as mfh

    _seed_pool(tmp_path)
    bundle = MinedFactorBundle(
        pool_dir=tmp_path,
        pit_provider_uri="pit://stub",
        delisted_registry_path="registry://stub",
    )

    pit_loads = 0

    def _fake_resolve(b, c, *, panel=None, forward_return=None):
        nonlocal pit_loads
        if panel is None:
            # The expensive PIT-load branch — count it.
            pit_loads += 1
            return _synthetic_panel(), None
        # Caller already holds the panel: cheap pass-through, no load.
        return panel, forward_return

    monkeypatch.setattr(mfh, "_resolve_panel", _fake_resolve)
    monkeypatch.setattr(mfh, "_make_qlib_handler", lambda *a, **k: "handler")

    factory = mfh._make_factory(bundle)
    factory(_config())

    assert pit_loads == 1


def test_factory_validates_pool_before_pit_load(tmp_path, monkeypatch):
    """codex P2 on #260: an empty/invalid pool must fail fast with its
    actionable diagnostic BEFORE the (expensive) PIT load — pool
    validation runs ahead of _resolve_panel, not after it."""
    from src.data import mined_factor_handler as mfh

    FactorPool().save(tmp_path)  # empty pool
    bundle = MinedFactorBundle(
        pool_dir=tmp_path,
        pit_provider_uri="pit://stub",
        delisted_registry_path="registry://stub",
    )

    resolve_called = False

    def _spy_resolve(*a, **k):
        nonlocal resolve_called
        resolve_called = True
        return _synthetic_panel(), None

    monkeypatch.setattr(mfh, "_resolve_panel", _spy_resolve)

    factory = mfh._make_factory(bundle)
    with pytest.raises(MinedFactorHandlerError, match="empty"):
        factory(_config())
    # The PIT load must NOT have happened — the pool error fails fast.
    assert resolve_called is False


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


def test_factory_resolves_universe_name_to_ticker_list(monkeypatch):
    """Regression for the qlib StaticDataLoader integration bug:
    ``StaticDataLoader.load(instruments, ...)`` treats ``instruments``
    as a ticker-list filter, NOT a qlib universe name. Passing
    ``"csi300"`` raises ``KeyError: 'csi300'`` deep inside pandas
    MultiIndex lookup. The handler factory MUST resolve the universe
    string via ``qlib.data.D.list_instruments`` and pass the resolved
    list to ``DataHandlerLP``.

    Verified here by stubbing qlib so the test runs without qlib
    installed and without touching the real registry."""
    import types

    import pandas as pd

    captured: dict = {}

    class _FakeD:
        @staticmethod
        def instruments(name):
            return {"_universe_name": name}

        @staticmethod
        def list_instruments(spec, start_time=None, end_time=None, as_list=True):
            captured["resolved_universe_spec"] = spec
            captured["resolve_start"] = start_time
            captured["resolve_end"] = end_time
            return ["SH600000", "SH600519", "SZ000001"]

    class _FakeStaticDataLoader:
        def __init__(self, config):
            captured["loader_config_keys"] = sorted(config.keys())

    class _FakeDataHandlerLP:
        def __init__(self, *, instruments, start_time, end_time, data_loader):
            captured["handler_instruments"] = instruments
            captured["handler_start"] = start_time
            captured["handler_end"] = end_time

    qlib_data_mod = types.ModuleType("qlib.data")
    qlib_data_mod.D = _FakeD
    qlib_handler_mod = types.ModuleType("qlib.data.dataset.handler")
    qlib_handler_mod.DataHandlerLP = _FakeDataHandlerLP
    qlib_loader_mod = types.ModuleType("qlib.data.dataset.loader")
    qlib_loader_mod.StaticDataLoader = _FakeStaticDataLoader

    monkeypatch.setitem(sys.modules, "qlib.data", qlib_data_mod)
    monkeypatch.setitem(sys.modules, "qlib.data.dataset.handler", qlib_handler_mod)
    monkeypatch.setitem(sys.modules, "qlib.data.dataset.loader", qlib_loader_mod)

    from src.data.feature_dataset_builder import FeatureDatasetConfig
    from src.data.mined_factor_handler import _make_qlib_handler

    features = pd.DataFrame(
        index=pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2024-01-02"), "SH600000")],
            names=["datetime", "instrument"],
        ),
        data={"mf_x": [0.1]},
    )
    cfg = FeatureDatasetConfig(
        instruments="csi300", feature_handler="MinedFactor",
        train_start="2024-01-01", train_end="2024-06-30",
        valid_start="2024-07-01", valid_end="2024-08-31",
        test_start="2024-09-01", test_end="2024-12-31",
    )
    _make_qlib_handler(features, None, cfg)

    # The handler must NOT receive the raw "csi300" string. It must
    # receive the resolved ticker list returned by D.list_instruments.
    assert captured["handler_instruments"] == ["SH600000", "SH600519", "SZ000001"]
    assert captured["resolve_start"] == "2024-01-01"
    assert captured["resolve_end"] == "2024-12-31"


def test_factory_passes_through_explicit_ticker_list(monkeypatch):
    """When the caller already gave a list of tickers (not a universe
    name), the handler MUST pass the list through unchanged — no
    re-resolution via D.list_instruments."""
    import types

    import pandas as pd

    captured: dict = {}

    class _FakeD:
        @staticmethod
        def list_instruments(*args, **kwargs):
            captured["unexpected_resolve"] = True
            return []

    class _FakeStaticDataLoader:
        def __init__(self, config):
            pass

    class _FakeDataHandlerLP:
        def __init__(self, *, instruments, start_time, end_time, data_loader):
            captured["handler_instruments"] = instruments

    qlib_data_mod = types.ModuleType("qlib.data")
    qlib_data_mod.D = _FakeD
    qlib_handler_mod = types.ModuleType("qlib.data.dataset.handler")
    qlib_handler_mod.DataHandlerLP = _FakeDataHandlerLP
    qlib_loader_mod = types.ModuleType("qlib.data.dataset.loader")
    qlib_loader_mod.StaticDataLoader = _FakeStaticDataLoader

    monkeypatch.setitem(sys.modules, "qlib.data", qlib_data_mod)
    monkeypatch.setitem(sys.modules, "qlib.data.dataset.handler", qlib_handler_mod)
    monkeypatch.setitem(sys.modules, "qlib.data.dataset.loader", qlib_loader_mod)

    from src.data.feature_dataset_builder import FeatureDatasetConfig
    from src.data.mined_factor_handler import _make_qlib_handler

    features = pd.DataFrame(
        index=pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2024-01-02"), "SH600000")],
            names=["datetime", "instrument"],
        ),
        data={"mf_x": [0.1]},
    )
    # Construct a config whose `instruments` is non-str (a list).
    # FeatureDatasetConfig type hints `str` but our handler is
    # defensive: if it gets a non-string, pass through without
    # re-resolution.
    cfg = object.__new__(FeatureDatasetConfig)
    object.__setattr__(cfg, "instruments", ["SH600519", "SH600036"])
    object.__setattr__(cfg, "feature_handler", "MinedFactor")
    object.__setattr__(cfg, "train_start", "2024-01-01")
    object.__setattr__(cfg, "train_end", "2024-06-30")
    object.__setattr__(cfg, "valid_start", "2024-07-01")
    object.__setattr__(cfg, "valid_end", "2024-08-31")
    object.__setattr__(cfg, "test_start", "2024-09-01")
    object.__setattr__(cfg, "test_end", "2024-12-31")

    _make_qlib_handler(features, None, cfg)
    assert captured["handler_instruments"] == ["SH600519", "SH600036"]
    assert "unexpected_resolve" not in captured


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
