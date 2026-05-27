"""Point-in-Time query layer for the corrected qlib provider.

Pipeline (Phase C.1, per docs/pit/pit_universe_design.md §6)
-----------------------------------------------------------
::

    <provider_dir> (Phase B.2 output)
    <delisted_registry_path> (Phase A.2 output)
       -> PITDataProvider
       -> get_universe(date, name)             → list of tickers
       -> get_universe_range(start, end, name) → dict[date -> tickers]
       -> get_features(fields, start, end, name, align) → DataFrame

Public guarantees
-----------------
- ``get_universe(date)`` never returns a ticker whose ``list_date >
  date`` or whose ``delist_date < date`` (no future-listed, no
  strictly-past-delisted). The ``delist_date`` itself IS included —
  it is the last valid trading day per Phase B's bin contract.
- ``get_features(...)`` returns a panel where every (ticker, date)
  position with ``date > delist_date`` is NaN — INCLUDING positions
  produced by qlib's window operators (Mean / Ref / Corr / etc.) that
  would otherwise leak across the NaN-after-delist boundary because
  qlib's default ``min_periods < N`` returns partial-window values.
  This is the load-bearing §4.3.2 mitigation surfaced by the Phase B
  validator: the bin storage is correct (NaN past delist), but qlib's
  operators are not strict enough. The post-process mask below closes
  the gap.

Cache contract
--------------
Repeated calls with identical ``(universe_name, start, end,
frozenset(fields))`` hit a bounded LRU (default 256 entries). The
mask post-processing happens BEFORE caching, so cached entries are
already PIT-clean.

Out of scope
------------
- No ``resolve_entity(ticker, date)`` method. A-share has no ticker
  reuse (PR #95), so ticker is the stable identifier.
- No write methods — read-only API.
- No index_membership-specific filters yet (consumers can call
  ``D.list_instruments(D.instruments("csi300"), ...)`` directly until
  Phase D wiring lands).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from src.core.logger import get_logger
from src.pit.cache import LRUCache

_logger = get_logger(__name__)

CacheKey = tuple[str, str, str, frozenset[str], frozenset[str] | None]


class PITDataProviderError(RuntimeError):
    """Raised when the provider cannot serve a query (missing provider,
    missing registry, qlib init failed, etc.)."""


@dataclass(frozen=True)
class _RegistryView:
    """Cheap-to-pass-around projection of the delisted registry."""

    delist_dates: dict[str, pd.Timestamp]  # ticker -> delist_date


class PITDataProvider:
    """Read-only PIT-correct query layer over a qlib provider directory.

    Construction loads the delisted registry into memory and
    initialises qlib via the canonical runtime entry point. After
    that, :meth:`get_universe` / :meth:`get_features` are the
    public API.
    """

    def __init__(
        self,
        provider_uri: str | Path,
        delisted_registry_path: str | Path,
        cache_max_entries: int = 256,
    ) -> None:
        self._provider_uri = Path(provider_uri)
        self._delisted_registry_path = Path(delisted_registry_path)
        self._registry = self._load_registry()
        self._init_qlib()
        # Single feature cache shared across get_features / get_universe.
        # 256-entry default per design §6; tune via constructor kwarg.
        self._cache: LRUCache[CacheKey, pd.DataFrame] = LRUCache(
            maxsize=cache_max_entries,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_universe(
        self,
        date: str | pd.Timestamp,
        universe_name: str = "all",
    ) -> list[str]:
        """Tickers tradable on ``date`` in ``universe_name``.

        Internally delegates to qlib's ``D.list_instruments`` then
        applies the registry-driven filter (a defence-in-depth pass
        in case the qlib instruments file leaks a delisted ticker on
        the day after delist — observed in older qlib bundles).
        """
        from qlib.data import D

        ts = pd.Timestamp(date)
        try:
            insts = D.list_instruments(
                D.instruments(universe_name),
                start_time=ts, end_time=ts, as_list=True,
            )
        except Exception as exc:
            raise PITDataProviderError(
                f"qlib.list_instruments failed for "
                f"({universe_name!r}, {ts.date()}): {exc}"
            ) from exc
        # Post-filter: drop any ticker whose registry delist_date <= date.
        # qlib's instruments file should already enforce this, but a
        # second check costs nothing and guards against stale instruments.
        return [t for t in insts if not self._is_past_delisted(t, ts)]

    def get_universe_range(
        self,
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
        universe_name: str = "all",
    ) -> dict[pd.Timestamp, list[str]]:
        """Per-trading-day universe map.

        Uses the qlib calendar to enumerate trading days in ``[start,
        end]``, then calls :meth:`get_universe` per day. For long
        ranges this is the per-call cost; consider caching at the
        caller if you call it repeatedly with overlapping ranges.
        """
        from qlib.data import D

        s, e = pd.Timestamp(start), pd.Timestamp(end)
        calendar = D.calendar(start_time=s, end_time=e, freq="day")
        return {d: self.get_universe(d, universe_name) for d in calendar}

    def get_features(
        self,
        fields: list[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
        universe_name: str = "all",
        align: str = "universe",
        instruments: list[str] | None = None,
    ) -> pd.DataFrame:
        """PIT-correct feature query.

        Parameters
        ----------
        fields
            qlib expression strings, e.g. ``["$close", "Ref($close, -1)",
            "Mean($close, 20)"]``.
        start, end
            ISO date strings or pandas Timestamps. Inclusive bounds.
        universe_name
            qlib instruments name; default ``"all"``. Ignored when
            ``instruments`` is provided.
        align
            ``"universe"`` (default) — returns the full panel; positions
            outside the universe on a given date are dropped from the
            row index.
            ``"tradable_only"`` — same shape, equivalent semantics; the
            distinction is reserved for downstream PIT filters in Phase
            D. For now both modes produce the same output.
        instruments
            Explicit list of qlib-style ticker codes (e.g. ``["SH600519",
            "SH600087"]``). When supplied, takes precedence over
            ``universe_name`` and the query targets exactly those
            tickers. Phase D wiring uses this form so caller-resolved
            ticker lists (e.g. from a factor DataFrame index) can be
            routed through the PIT mask.

        Returns
        -------
        DataFrame indexed by ``(instrument, datetime)`` with one column
        per field. Every ``(ticker, date)`` with ``date > delist_date``
        is NaN, including positions where a window operator would
        otherwise leak across the boundary.
        """
        if align not in ("universe", "tradable_only"):
            raise PITDataProviderError(
                f"unknown align mode {align!r}; valid: 'universe', 'tradable_only'"
            )
        ts_start = pd.Timestamp(start)
        ts_end = pd.Timestamp(end)
        if ts_start > ts_end:
            raise PITDataProviderError(
                f"start ({ts_start.date()}) > end ({ts_end.date()})"
            )

        # Cache key — frozenset(instruments) so caller-order doesn't
        # fragment cache entries the way frozenset(fields) doesn't.
        instruments_key = frozenset(instruments) if instruments is not None else None
        key: CacheKey = (
            universe_name, str(ts_start.date()), str(ts_end.date()),
            frozenset(fields), instruments_key,
        )
        cached = self._cache.get(key)
        if cached is not None:
            return cached.copy()  # defensive copy so consumers can mutate freely

        df = self._fetch_qlib_features(
            fields=fields, start=ts_start, end=ts_end,
            universe_name=universe_name, instruments=instruments,
        )
        df = self._mask_post_delist(df)
        self._cache.put(key, df)
        return df.copy()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_qlib_features(
        self,
        *,
        fields: list[str],
        start: pd.Timestamp,
        end: pd.Timestamp,
        universe_name: str,
        instruments: list[str] | None = None,
    ) -> pd.DataFrame:
        from qlib.data import D
        # Specific-instrument queries skip the universe lookup so callers
        # that already resolved a ticker list (factor mining, backtest)
        # can use the PIT mask without translating through a universe
        # name they don't have. Phase D wiring uses this form.
        #
        # pit-bypass-ok: this IS the PIT layer's raw fetch; the
        # ``_mask_post_delist`` step at the public ``get_features``
        # boundary applies the §4.3.2 mask on top of whatever this
        # returns. Routing this internal call through PIT would be
        # infinite recursion. Audit P0-6.
        target = instruments if instruments is not None else D.instruments(universe_name)
        try:
            return D.features(
                target, fields,
                start_time=start, end_time=end,
            )
        except Exception as exc:
            descriptor = (
                f"instruments={instruments[:3]}..." if instruments is not None
                else f"universe={universe_name!r}"
            )
            raise PITDataProviderError(
                f"qlib.features failed for ({descriptor}, "
                f"{fields}, {start.date()}, {end.date()}): {exc}"
            ) from exc

    def _mask_post_delist(self, df: pd.DataFrame) -> pd.DataFrame:
        """Set every (ticker, date) where date > delist_date to NaN.

        This is the load-bearing §4.3.2 mitigation (option a from the
        design): window operators like ``Mean($close, 20)`` return
        partial-window values past delist_date because qlib's default
        ``min_periods < N``. We mask AFTER qlib computes, which costs
        an extra pass over the panel but cleanly closes the gap
        without forking qlib's operator implementations.
        """
        if df.empty or not self._registry.delist_dates:
            return df
        # Pull the (instrument, datetime) levels into arrays for a
        # vectorised mask. df.index is a MultiIndex.
        inst_level = df.index.get_level_values("instrument")
        date_level = df.index.get_level_values("datetime")
        # Build a per-row delist_date Series; NaT for active tickers.
        delist_series = pd.Series(
            [self._registry.delist_dates.get(t, pd.NaT) for t in inst_level],
            index=df.index,
        )
        mask = (delist_series.notna()) & (date_level > delist_series.values)
        if mask.any():
            df = df.copy()
            df.loc[mask, :] = np.nan
        return df

    def _is_past_delisted(self, ticker: str, date: pd.Timestamp) -> bool:
        delist = self._registry.delist_dates.get(ticker)
        return delist is not None and delist < date

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _load_registry(self) -> _RegistryView:
        path = self._delisted_registry_path
        if not path.exists():
            raise PITDataProviderError(
                f"Missing delisted registry at {path}; run Phase A.2."
            )
        df = pd.read_parquet(path)
        required = {"ticker", "delist_date"}
        missing = required - set(df.columns)
        if missing:
            raise PITDataProviderError(
                f"{path} missing required columns: {sorted(missing)}"
            )
        delist_dates: dict[str, pd.Timestamp] = {
            str(r["ticker"]): pd.Timestamp(r["delist_date"])
            for _, r in df.iterrows()
        }
        return _RegistryView(delist_dates=delist_dates)

    def _init_qlib(self) -> None:
        # Route every qlib bootstrap through the canonical runtime entry
        # point so the governance guard at
        # tests/governance/test_publisher_uses_canonical_init.py stays
        # green. We pin ADJUST_MODE_POST to match what Phase B.2 wrote
        # into the bins (close × adj_factor).
        try:
            from src.core.canonical_backtest_contract import ADJUST_MODE_POST
            from src.core.qlib_runtime import (
                QlibRuntimeConfig,
                init_qlib_canonical,
            )
        except ImportError as exc:
            raise PITDataProviderError(
                f"Cannot import canonical qlib runtime: {exc}"
            ) from exc
        if not (self._provider_uri / "calendars" / "day.txt").exists():
            raise PITDataProviderError(
                f"{self._provider_uri} is not a valid qlib provider — "
                f"missing calendars/day.txt. Run Phase B.2 first."
            )
        config = QlibRuntimeConfig(
            provider_uri=str(self._provider_uri),
            region="cn",
            data_adjust_mode=ADJUST_MODE_POST,
        )
        with contextlib.redirect_stdout(None):
            init_qlib_canonical(config)
