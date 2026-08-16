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

from collections.abc import Callable, Mapping, Sequence
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
        # The declared type is ``Path`` but the dataclass init accepts
        # any path-like at runtime (e.g. ``str`` from YAML loaders);
        # mypy sees the isinstance check as unreachable because of the
        # declared type, but the runtime coercion is still needed.
        if not isinstance(self.pool_dir, Path):
            object.__setattr__(self, "pool_dir", Path(self.pool_dir))  # type: ignore[unreachable,unused-ignore]
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
    return _materialise_features(pool, resolved_panel)



def _refuse_fundamental_entries(entries: Sequence[object]) -> None:
    """Defense in depth: refuse to materialise FINANCIAL-STATEMENT factors.

    The primary refusal lives at the WRITER (``promote._refuse_fundamental_
    pool_in_production``), before the production directory is even created —
    a check here alone would fire only at some later materialisation, by which
    point promotion had already written the pool into production.

    This one exists because "the writer refused" is a claim about one code
    path, while this is the path that would actually serve the numbers: we
    evaluate against a qlib panel with NO report-period provenance, so a
    fundamental factor materialised here would be served WITHOUT the
    cross-endpoint alignment mask that decided its promotion.
    """
    from src.factor_mining.expression import feature_terminals  # noqa: PLC0415
    from src.factor_mining.grammar import FeatureRegistry  # noqa: PLC0415

    # Registry minus default — same rule as the writer-side guard, so the two
    # layers cannot disagree on what counts as fundamental ($X__prior included).
    financial = set(FeatureRegistry.ALL_REGISTERED) - set(FeatureRegistry.V1)
    offenders = [
        e.expr.to_qlib_string() for e in entries  # type: ignore[attr-defined]
        if feature_terminals(e.expr) & financial  # type: ignore[attr-defined]
    ]
    if not offenders:
        return
    raise MinedFactorHandlerError(
        f"refusing to materialise {len(offenders)} FUNDAMENTAL factor(s): "
        f"{offenders}. This path evaluates without report-period provenance, "
        "so they would be served WITHOUT the cross-endpoint alignment mask "
        "that decided their promotion. The fundamental-panel wiring for this "
        "consumer is a separate change; until it lands such a pool must not "
        "reach production (the promotion writer refuses it too)."
    )

def _materialise_features(
    pool: FactorPool,
    resolved_panel: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    """Evaluate every pool entry against ``resolved_panel`` into a
    ``(datetime, instrument)`` feature frame.

    Split out of :func:`make_mined_factor_features` so the PIT-mode
    factory can validate the pool (``_load_pool_or_raise``) BEFORE the
    single PIT load and still share this materialisation step
    (codex P2 on #260).
    """
    sorted_entries = sorted(pool.all_entries(), key=_entry_sort_key)
    _refuse_fundamental_entries(sorted_entries)

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


def _make_factory(bundle: MinedFactorBundle) -> Callable[[FeatureDatasetConfig], Any]:
    """Closure-style factory that captures ``bundle``."""

    def _factory(config: FeatureDatasetConfig) -> Any:
        # Validate the pool BEFORE the (single) PIT load so an empty /
        # invalid pool fails fast with its actionable diagnostic instead
        # of first paying the full per-fold PIT IO (codex P2 on #260).
        # The OHLCV panel load is the expensive per-fold step: resolve it
        # ONCE and reuse the panel for both features and the label
        # (previously it was loaded twice). This path is PIT-mode-only —
        # tests supply the panel via the make_mined_factor_features kwarg
        # and don't go through the registered factory. (T2-6)
        pool = _load_pool_or_raise(bundle)
        panel, fwd = _resolve_panel(bundle, config)
        features = _materialise_features(pool, panel)
        label = _build_label_dataframe(fwd)
        return _make_qlib_handler(features, label, config)

    _factory.__doc__ = (
        f"MinedFactor handler factory bound to pool_dir={bundle.pool_dir!r}"
    )
    return _factory


def _compute_bundle_cache_identity(bundle: MinedFactorBundle) -> str:
    """Sha256-derived identity covering the bundle's bound state.

    The identity changes whenever any of the following changes:

    * ``pool_dir`` (the directory of the bound factor pool).
    * ``pit_provider_uri`` (which PIT bundle the handler reads).
    * ``delisted_registry_path`` (which delisted-tickers registry).
    * ``universe_name_override``.
    * **The contents of ``pool_dir / factor_pool.parquet``** — re-running
      the miner against the same path with a new seed produces a
      semantically different pool even though the directory string is
      unchanged. The file's bytes are hashed so a cache-key collision
      with the prior pool is structurally impossible.

    Audit P2: before this helper existed, the feature-dataset cache
    keyed only on ``feature_handler="MinedFactor"`` (the registered
    name), so re-binding the handler to a different pool produced
    the same cache key and silently served stale features under the
    new pool's name. This composite identity makes the key sensitive
    to every bundle field the handler actually consults.

    Missing pool parquet → returns an identity computed from the
    other fields plus the literal ``"no-parquet"`` marker; cache
    callers still get a stable, unique key (just one that doesn't
    fingerprint pool contents).
    """
    import hashlib  # noqa: PLC0415

    h = hashlib.sha256()
    h.update(b"pool_dir=")
    h.update(str(bundle.pool_dir).encode("utf-8"))
    h.update(b"\x00pit_provider_uri=")
    h.update(str(bundle.pit_provider_uri).encode("utf-8"))
    h.update(b"\x00delisted_registry_path=")
    h.update(str(bundle.delisted_registry_path).encode("utf-8"))
    h.update(b"\x00universe_name_override=")
    h.update(str(bundle.universe_name_override or "").encode("utf-8"))

    # Hash the pool parquet bytes so re-mining the same dir invalidates
    # the cache. The parquet may be absent in synthetic-mode tests; in
    # that case we tag the identity so callers don't conflate "no
    # parquet" with "different parquet".
    pool_parquet = Path(bundle.pool_dir) / POOL_PARQUET_FILENAME
    try:
        if pool_parquet.is_file():
            h.update(b"\x00pool_parquet_sha256=")
            h.update(hashlib.sha256(pool_parquet.read_bytes()).digest())
        else:
            h.update(b"\x00pool_parquet=no-parquet")
    except OSError:
        # Permission denied / read failure — fall through to a stable
        # but pessimistic identity. Cache will fingerprint by other
        # fields only.
        h.update(b"\x00pool_parquet=unreadable")

    return f"mined_factor:{h.hexdigest()[:32]}"


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

    A cache identity that fingerprints ``bundle`` (pool_dir + PIT
    provider + delisted registry + universe override + pool parquet
    sha) is registered alongside the factory so the feature-dataset
    cache produces a distinct key per bound pool. Re-binding with
    ``replace=True`` overwrites both the factory and the identity.
    """
    factory = _make_factory(bundle)

    # Capture ``bundle`` in a closure for the identity callable so
    # ``register_feature_handler(replace=True)`` rebinding always
    # re-derives the identity from the freshly-bound bundle.
    def _identity() -> str:
        return _compute_bundle_cache_identity(bundle)

    register_feature_handler(
        name, factory, replace=replace, cache_identity=_identity,
    )


# ---------------------------------------------------------------------------
# Alpha158PlusMined — the paired-comparison treatment arm (PV-DP-7 step 2)
# ---------------------------------------------------------------------------

ALPHA158_PLUS_MINED_HANDLER_NAME = "Alpha158PlusMined"


def _make_alpha158_plus_mined_qlib_handler(
    mined_features: pd.DataFrame,
    config: FeatureDatasetConfig,
) -> Any:
    """Alpha158's own handler with the mined columns merged in.

    The treatment arm of a paired comparison may differ from the
    baseline arm in EXACTLY ONE respect: the extra feature columns.
    So this does not rebuild Alpha158's semantics — it defers
    Alpha158's own data load (``init_data=False``), wraps its own
    loader in qlib's ``NestedDataLoader`` alongside a
    ``StaticDataLoader`` carrying the mined columns, and lets the
    handler load. Alpha158's label expression, its default
    processors, and its row set therefore come from Alpha158 itself
    rather than from a re-derivation here:

    * label — built by Alpha158's loader from the same expression the
      baseline arm uses (``alpha158_label_expression``, passed
      explicitly so the horizon override applies identically to both
      arms);
    * processors — untouched, because the handler instance IS an
      ``Alpha158``;
    * rows — ``NestedDataLoader``'s ``join="left"`` keeps the FIRST
      loader's index, i.e. Alpha158's, so an instrument-date the
      baseline arm does not carry cannot enter through the mined side.
    """
    from qlib.contrib.data.handler import Alpha158  # noqa: PLC0415
    from qlib.data.dataset.loader import (  # noqa: PLC0415
        DataLoader,
        NestedDataLoader,
        StaticDataLoader,
    )

    from src.data.feature_dataset_builder import (  # noqa: PLC0415
        alpha158_label_expression,
    )

    class _InstrumentAgnosticLoader(DataLoader):  # type: ignore[misc] # noqa: PLC0415
        """Serve the mined frame whole; let the join select the rows.

        ``DataHandler.setup_data`` forwards ``self.instruments`` — the
        dynamic universe NAME — to every nested loader, and
        ``StaticDataLoader`` reads a string as a literal ticker filter:
        it raises ``KeyError``, which ``NestedDataLoader`` swallows
        before retrying with ``instruments=None``. Relying on a
        third-party exception path for correct behaviour is not a
        design, so the delegation is made explicit here.

        Row selection is NOT lost by ignoring the argument: the outer
        join is ``how="left"`` onto Alpha158's index, which is what
        carries point-in-time membership. Whatever the mined frame
        holds outside that index is dropped by the join.
        """

        def __init__(self, inner: Any) -> None:
            super().__init__()
            self._inner = inner

        def load(
            self,
            instruments: Any = None,
            start_time: Any = None,
            end_time: Any = None,
        ) -> pd.DataFrame:
            return self._inner.load(
                instruments=None, start_time=start_time, end_time=end_time)

    handler = Alpha158(
        # The DYNAMIC universe spec, exactly as the baseline arm passes
        # it. Flattening ``csi800`` to a ticker list here (r2's first
        # attempt) loads former and future constituents outside their
        # membership periods, so the treatment arm's index would be
        # wider than the baseline's and the pair would differ in
        # universe rows as well as features (codex #422 r3). Only the
        # static mined loader — which cannot read a universe name —
        # gets special handling, below.
        instruments=config.instruments,
        start_time=config.train_start,
        end_time=config.test_end,
        fit_start_time=config.train_start,
        fit_end_time=config.train_end,
        label=(
            [alpha158_label_expression(config.label_horizon_days)],
            ["LABEL0"],
        ),
        # Defer the load so the loader can be composed first; without
        # this the handler would load Alpha158 alone and then have to
        # be re-loaded, paying the qlib expression pass twice.
        init_data=False,
    )
    handler.data_loader = NestedDataLoader(
        dataloader_l=[
            handler.data_loader,
            _InstrumentAgnosticLoader(
                StaticDataLoader(config={"feature": mined_features})),
        ],
        join="left",
    )
    handler.setup_data()
    return handler


def _make_alpha158_plus_mined_factory(
    bundle: MinedFactorBundle,
) -> Callable[[FeatureDatasetConfig], Any]:
    """Closure-style factory for the combined handler."""

    def _factory(config: FeatureDatasetConfig) -> Any:
        # Same ordering discipline as the MinedFactor factory: validate
        # the pool before paying the PIT load.
        pool = _load_pool_or_raise(bundle)
        panel, _ = _resolve_panel(bundle, config)
        mined = _materialise_features(pool, panel)
        return _make_alpha158_plus_mined_qlib_handler(mined, config)

    _factory.__doc__ = (
        f"Alpha158PlusMined handler factory bound to "
        f"pool_dir={bundle.pool_dir!r}"
    )
    return _factory


def register_alpha158_plus_mined_handler(
    bundle: MinedFactorBundle,
    *,
    name: str = ALPHA158_PLUS_MINED_HANDLER_NAME,
    replace: bool = False,
) -> None:
    """Register the Alpha158 + mined-factor combined handler.

    The cache identity composes Alpha158's constant identity with the
    bundle fingerprint, so the feature-dataset cache can never serve a
    plain-Alpha158 dataset (or a differently-bound pool's dataset)
    under this name.
    """
    factory = _make_alpha158_plus_mined_factory(bundle)

    def _identity() -> str:
        # The bundle identity alone hashes the mined PIT/registry PATHS
        # and the pool bytes, but not the registry's CONTENT — so a
        # registry updated in place (or a distinct
        # mined_factor_pit_provider_uri refreshed in place) would reuse
        # a cached dataset built from the OLD inputs while the run
        # report stamped the NEW identity: stale treatment features
        # under fresh provenance (codex #422 r6). Fold the same input
        # identity the run stamp carries into the cache key.
        #
        # The plain MinedFactor handler's identity is deliberately left
        # as it shipped: changing it would invalidate every existing
        # cached dataset for a path this PR does not introduce.
        parts = ["alpha158_default", _compute_bundle_cache_identity(bundle)]
        if bundle.pit_provider_uri and bundle.delisted_registry_path:
            from src.factor_mining.promotion_binding import (  # noqa: PLC0415
                mined_input_identity,
            )

            inputs = mined_input_identity(
                pit_provider_uri=bundle.pit_provider_uri,
                delisted_registry_path=bundle.delisted_registry_path,
            )
            parts.append(inputs["pit_bundle_content_hash"])
            parts.append(f"registry:{inputs['delisted_registry_sha256']}")
        return "+".join(parts)

    register_feature_handler(
        name, factory, replace=replace, cache_identity=_identity,
    )
