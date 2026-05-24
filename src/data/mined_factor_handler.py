"""MinedFactor handler — bridges a Phase 3 factor pool into the qlib pipeline.

The handler reads a factor pool (produced by ``python -m
src.factor_mining.miner``), evaluates each pool entry's expression
against an OHLCV panel (PIT-loaded for production, synthetic for
tests), and exposes the resulting feature panel to the existing
``FeatureDatasetBuilder`` registry boundary.

D5 strict gate: this module lives under ``src/data/``, NOT
``src/factor_mining/``, so it is permitted to import the qlib
runtime. It does so **lazily** — the top-level body imports nothing
from qlib. The qlib import only happens when the registered factory
is invoked at training-pipeline build time.

Lifecycle:

1. Phase 3 miner writes a pool to disk.
2. The application binds the pool at startup::

       from src.data.mined_factor_handler import (
           MinedFactorBundle, register_mined_factor_handler,
       )

       register_mined_factor_handler(MinedFactorBundle(
           pool_dir=Path("research/mined_factors/runs/<id>"),
           pit_provider_uri="D:/qlib_data/my_cn_data_pit",
           delisted_registry_path="...",
       ))

3. A ``PipelineConfig(feature_handler="MinedFactor", ...)`` invokes
   the registered factory, which calls
   ``make_mined_factor_features`` to materialise the panel and
   wraps it in a qlib ``DataHandlerLP``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from src.data.feature_dataset_builder import (
    FeatureDatasetConfig,
    register_feature_handler,
)
from src.factor_mining.evaluator import evaluate_expression
from src.factor_mining.factor_pool import (
    POOL_EXPR_JSON_FILENAME,
    POOL_PARQUET_FILENAME,
    FactorPool,
    PoolEntry,
)


class MinedFactorHandlerError(RuntimeError):
    """Raised by the MinedFactor handler on malformed bundles, empty
    pools, or PIT-mode invocations with empty PIT bindings."""


@dataclass(frozen=True)
class MinedFactorBundle:
    """Binds a registered MinedFactor handler to a specific pool + data source.

    Attributes
    ----------
    pool_dir
        Directory containing ``factor_pool.parquet`` and
        ``factor_expressions.json`` written by the Phase 3 miner.
    pit_provider_uri
        Path to the PIT-corrected qlib bundle. Empty string ``""``
        signals "synthetic mode" (tests supply the panel directly to
        ``make_mined_factor_features``).
    delisted_registry_path
        Path to the delisted-tickers registry parquet. Empty string
        ``""`` signals synthetic mode (must be empty iff
        ``pit_provider_uri`` is empty).
    universe_name_override
        Optional override for the universe passed to PITDataProvider.
        When None, the value is inherited from the
        ``FeatureDatasetConfig`` at factory-invocation time.
    """

    pool_dir: Path
    pit_provider_uri: str = ""
    delisted_registry_path: str = ""
    universe_name_override: str | None = None

    def __post_init__(self) -> None:
        # We must call object.__setattr__ to coerce because the dataclass is frozen.
        if not isinstance(self.pool_dir, Path):
            object.__setattr__(self, "pool_dir", Path(self.pool_dir))
        d = self.pool_dir
        if not d.exists():
            raise MinedFactorHandlerError(
                f"MinedFactorBundle.pool_dir does not exist: {d!r}"
            )
        if not (d / POOL_PARQUET_FILENAME).is_file():
            raise MinedFactorHandlerError(
                f"MinedFactorBundle.pool_dir is missing {POOL_PARQUET_FILENAME}: {d!r}"
            )
        if not (d / POOL_EXPR_JSON_FILENAME).is_file():
            raise MinedFactorHandlerError(
                f"MinedFactorBundle.pool_dir is missing {POOL_EXPR_JSON_FILENAME}: {d!r}"
            )


def _entry_sort_key(entry: PoolEntry) -> tuple[float, int]:
    """Fitness desc, expr_hash asc — deterministic across runs."""
    return (-entry.fitness, entry.expr_hash)


def _column_name_for(entry: PoolEntry) -> str:
    """``mf_<hex>`` with 16-char lowercase hex 64-bit hash."""
    return "mf_" + format(entry.expr_hash & 0xFFFFFFFFFFFFFFFF, "016x")


def _load_pool_or_raise(bundle: MinedFactorBundle) -> FactorPool:
    pool = FactorPool.load(bundle.pool_dir)
    if len(pool) == 0:
        raise MinedFactorHandlerError(
            f"MinedFactor pool at {bundle.pool_dir!r} is empty; "
            "run the Phase 3 miner first "
            "(python -m src.factor_mining.miner <config>)"
        )
    return pool


def _resolve_panel(
    bundle: MinedFactorBundle,
    config: FeatureDatasetConfig,
    *,
    panel: Mapping[str, pd.DataFrame] | None = None,
    forward_return: pd.DataFrame | None = None,
) -> tuple[Mapping[str, pd.DataFrame], pd.DataFrame | None]:
    """Either return the caller-supplied synthetic panel or load via PIT."""
    if panel is not None:
        return panel, forward_return
    # PIT mode — require both URIs.
    if not bundle.pit_provider_uri or not bundle.delisted_registry_path:
        raise MinedFactorHandlerError(
            "MinedFactor handler invoked in PIT mode but bundle has empty "
            "pit_provider_uri or delisted_registry_path. Either supply "
            "a panel directly (synthetic mode) or fill in the PIT paths "
            "in the bundle. See docs/factor_mining/inventory.md §F.3 "
            "for PIT-bundle build instructions."
        )
    # Local imports to keep the data gate clean and avoid pulling
    # qlib at module-load time.
    from src.factor_mining.pit_adapter import FactorMiningDataView  # noqa: PLC0415
    from src.pit.query import PITDataProvider  # noqa: PLC0415

    universe = bundle.universe_name_override or config.instruments
    provider = PITDataProvider(
        provider_uri=bundle.pit_provider_uri,
        delisted_registry_path=bundle.delisted_registry_path,
    )
    view = FactorMiningDataView(
        provider,
        start=config.train_start,
        end=config.test_end,
        universe_name=universe,
    )
    panel = view.load_panel()
    fwd = view.forward_return(horizon=1)
    return panel, fwd


def make_mined_factor_features(
    bundle: MinedFactorBundle,
    config: FeatureDatasetConfig,
    *,
    panel: Mapping[str, pd.DataFrame] | None = None,
    forward_return: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Materialise the mined-factor feature panel.

    Returns a ``pd.DataFrame`` indexed by ``(instrument, datetime)``
    MultiIndex with one column per pool entry. Columns are named
    ``mf_<hex_hash>`` and sorted by descending fitness, then
    ascending ``expr_hash`` (deterministic).
    """
    pool = _load_pool_or_raise(bundle)
    resolved_panel, _ = _resolve_panel(
        bundle, config, panel=panel, forward_return=forward_return,
    )
    sorted_entries = sorted(pool.all_entries(), key=_entry_sort_key)

    columns: list[pd.Series] = []
    column_names: list[str] = []
    for entry in sorted_entries:
        result = evaluate_expression(entry.expr, resolved_panel)
        if not isinstance(result, pd.DataFrame):
            raise MinedFactorHandlerError(
                f"Mined factor {entry.expr.to_qlib_string()!r} did not "
                "produce a DataFrame; pool may contain a malformed "
                "non-CSF expression"
            )
        stacked = result.stack(future_stack=True)
        stacked.index = stacked.index.set_names(["datetime", "instrument"])
        # qlib's StaticDataLoader expects the MultiIndex order
        # (datetime, instrument) — its load() does
        # df.loc(axis=0)[:, instruments] which treats level 0 as datetime
        # and level 1 as the instrument filter. The original
        # (instrument, datetime) order made pandas try to look up
        # SH600000 in the datetime level and raise KeyError.
        stacked = stacked.reorder_levels(["datetime", "instrument"]).sort_index()
        columns.append(stacked)
        column_names.append(_column_name_for(entry))

    features = pd.concat(columns, axis=1, keys=column_names)
    features.columns = column_names
    features.index = features.index.set_names(["datetime", "instrument"])
    return features


def _build_label_dataframe(
    forward_return: pd.DataFrame | None,
) -> pd.DataFrame | None:
    """Stack the forward-return panel into qlib's (instrument, datetime) shape."""
    if forward_return is None or forward_return.empty:
        return None
    stacked = forward_return.stack(future_stack=True)
    stacked.index = stacked.index.set_names(["datetime", "instrument"])
    # qlib's StaticDataLoader expects the MultiIndex order
    # (datetime, instrument) — its load() does df.loc(axis=0)[:, instruments]
    # which treats level 0 as datetime and level 1 as the instrument filter.
    # The original (instrument, datetime) order made pandas try to look up
    # SH600000 in the datetime level and raise KeyError.
    stacked = stacked.reorder_levels(["datetime", "instrument"]).sort_index()
    return stacked.to_frame(name="LABEL0")


def _make_qlib_handler(
    features: pd.DataFrame,
    label: pd.DataFrame | None,
    config: FeatureDatasetConfig,
) -> Any:
    """Lazy-imported qlib handler construction.

    Wraps the materialised ``features`` (and optional ``label``)
    DataFrame in a qlib ``StaticDataLoader`` and returns a
    ``DataHandlerLP`` instance. qlib is imported INSIDE this function
    so importing the parent module never pulls qlib.

    Note on ``instruments``: ``StaticDataLoader.load(instruments, ...)``
    treats ``instruments`` as a list of ticker codes to ``df.loc[:,
    instruments]``-filter, NOT as a qlib universe name. Passing
    ``"csi300"`` directly raises ``KeyError: 'csi300'`` deep inside
    pandas MultiIndex lookup. We resolve the universe name to a
    concrete ticker list via ``qlib.data.D.list_instruments`` first.
    """
    from qlib.data import D  # noqa: PLC0415
    from qlib.data.dataset.handler import DataHandlerLP  # noqa: PLC0415
    from qlib.data.dataset.loader import StaticDataLoader  # noqa: PLC0415

    instruments = config.instruments
    if isinstance(instruments, str):
        # Resolve qlib universe name (e.g. "csi300") to the list of
        # tickers active in [train_start, test_end].
        instruments = D.list_instruments(
            D.instruments(instruments),
            start_time=config.train_start,
            end_time=config.test_end,
            as_list=True,
        )

    data_dict: dict[str, pd.DataFrame] = {"feature": features}
    if label is not None:
        data_dict["label"] = label
    loader = StaticDataLoader(config=data_dict)
    return DataHandlerLP(
        instruments=instruments,
        start_time=config.train_start,
        end_time=config.test_end,
        data_loader=loader,
    )


def _make_factory(bundle: MinedFactorBundle):
    """Closure-style factory that captures ``bundle``."""

    def _factory(config: FeatureDatasetConfig) -> Any:
        features = make_mined_factor_features(bundle, config)
        # The forward_return is reconstructed inline because the factory
        # path is PIT-mode-only; tests supply the panel via the kwarg
        # and don't go through the registered factory.
        _, fwd = _resolve_panel(bundle, config)
        label = _build_label_dataframe(fwd)
        return _make_qlib_handler(features, label, config)

    _factory.__doc__ = (
        f"MinedFactor handler factory bound to pool_dir={bundle.pool_dir!r}"
    )
    return _factory


def register_mined_factor_handler(
    bundle: MinedFactorBundle,
    *,
    name: str = "MinedFactor",
    replace: bool = False,
) -> None:
    """Register a MinedFactor handler under ``name`` (default ``"MinedFactor"``).

    The registered factory captures ``bundle`` by closure; a
    subsequent ``register_mined_factor_handler(other_bundle,
    replace=True)`` call rebinds the same registry slot to a new
    bundle.
    """
    factory = _make_factory(bundle)
    register_feature_handler(name, factory, replace=replace)
