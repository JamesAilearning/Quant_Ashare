"""Tushare market data fetcher with staged caching."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

import pandas as pd

from src.core.logger import get_logger
from src.data.tushare.client import TushareClient, TushareClientError

from src.data.tushare.provider_bundle._types import (
    INDEX_SOURCE_API,
    SOURCE_APIS,
    STAGED_ADJ_FACTOR_DIR,
    STAGED_DAILY_DIR,
    STAGED_INDEX_DAILY_DIR,
    STAGED_STOCK_BASIC_FILE,
    STAGED_TRADE_CAL_FILE,
    TushareQlibProviderBundleError,
    TushareStagedMarketData,
)
from src.data.tushare.provider_bundle._utils import (
    _concat_frames,
    _ensure_frame,
    _parse_tushare_date_series,
    _read_frame,
    _staged_cache_metadata_matches,
    _to_tushare_date,
    _write_frame,
    _write_staged_cache_metadata,
)
from src.data.tushare.provider_bundle.config import TushareQlibProviderBundleConfig

_logger = get_logger(__name__)

class TushareMarketDataFetcher:
    """Fetch and stage Tushare OHLCV payloads before validation."""

    @classmethod
    def stage(
        cls,
        config: TushareQlibProviderBundleConfig,
        *,
        client: Optional[TushareClient] = None,
    ) -> TushareStagedMarketData:
        if client is None:
            try:
                client = TushareClient.from_environment()
            except TushareClientError as exc:
                raise TushareQlibProviderBundleError(str(exc)) from exc

        staging_dir = config.staging_path
        raw_dir = staging_dir / "raw"
        daily_dir = raw_dir / STAGED_DAILY_DIR
        adj_dir = raw_dir / STAGED_ADJ_FACTOR_DIR
        index_dir = raw_dir / STAGED_INDEX_DAILY_DIR
        daily_dir.mkdir(parents=True, exist_ok=True)
        adj_dir.mkdir(parents=True, exist_ok=True)
        if config.benchmark_indexes:
            index_dir.mkdir(parents=True, exist_ok=True)

        trade_cal_path = raw_dir / STAGED_TRADE_CAL_FILE
        stock_basic_path = raw_dir / STAGED_STOCK_BASIC_FILE

        trade_calendar = cls._fetch_or_read(
            trade_cal_path,
            config=config,
            api_name="trade_cal",
            client=client,
            params={
                "exchange": "",
                "start_date": _to_tushare_date(config.start_date),
                "end_date": _to_tushare_date(config.end_date),
            },
        )
        open_dates = _extract_open_trade_dates(trade_calendar)
        if not open_dates:
            raise TushareQlibProviderBundleError(
                "Tushare trade_cal returned no open dates for requested range."
            )

        stock_basic = cls._fetch_stock_basic(
            stock_basic_path,
            config=config,
            client=client,
        )

        daily_frames: list[pd.DataFrame] = []
        adj_frames: list[pd.DataFrame] = []
        index_frames: list[pd.DataFrame] = []
        daily_files: list[str] = []
        adj_files: list[str] = []
        index_files: list[str] = []
        requested_codes = config.requested_tushare_codes

        for trade_date in open_dates:
            daily_path = daily_dir / f"{trade_date}.csv"
            daily = cls._fetch_or_read(
                daily_path,
                config=config,
                api_name="daily",
                client=client,
                params={"trade_date": trade_date},
            )
            daily = _filter_tushare_codes(daily, requested_codes)
            daily_frames.append(daily)
            daily_files.append(str(daily_path))

            adj_path = adj_dir / f"{trade_date}.csv"
            adj = cls._fetch_or_read(
                adj_path,
                config=config,
                api_name="adj_factor",
                client=client,
                params={"trade_date": trade_date},
            )
            adj = _filter_tushare_codes(adj, requested_codes)
            adj_frames.append(adj)
            adj_files.append(str(adj_path))

        for qlib_code, ts_code in config.benchmark_indexes:
            index_path = index_dir / f"{qlib_code}.csv"
            index_daily = cls._fetch_or_read(
                index_path,
                config=config,
                api_name=INDEX_SOURCE_API,
                client=client,
                params={
                    "ts_code": ts_code,
                    "start_date": _to_tushare_date(config.start_date),
                    "end_date": _to_tushare_date(config.end_date),
                },
            )
            index_daily = _filter_tushare_codes(index_daily, (ts_code,))
            _write_frame(index_path, index_daily)
            index_frames.append(index_daily)
            index_files.append(str(index_path))

        daily_all = _concat_frames(daily_frames)
        adj_all = _concat_frames(adj_frames)
        index_all = _concat_frames(index_frames)
        return TushareStagedMarketData(
            daily=daily_all,
            adj_factor=adj_all,
            trade_calendar=trade_calendar,
            stock_basic=stock_basic,
            staging_dir=str(staging_dir),
            daily_files=tuple(daily_files),
            adj_factor_files=tuple(adj_files),
            index_daily=index_all,
            index_daily_files=tuple(index_files),
        )

    @staticmethod
    def _fetch_or_read(
        path: Path,
        *,
        config: TushareQlibProviderBundleConfig,
        api_name: str,
        client: TushareClient,
        params: Mapping[str, Any],
    ) -> pd.DataFrame:
        if config.reuse_staged and _staged_cache_metadata_matches(
            path,
            api_name=api_name,
            params=params,
        ):
            return _read_frame(path)
        try:
            result = client.call(api_name, **dict(params))
        except TushareClientError as exc:
            raise TushareQlibProviderBundleError(
                f"Tushare API {api_name!r} failed while staging provider data: {exc}"
            ) from exc
        frame = _ensure_frame(result, api_name)
        _write_frame(path, frame)
        _write_staged_cache_metadata(path, api_name=api_name, params=params)
        return frame

    @classmethod
    def _fetch_stock_basic(
        cls,
        path: Path,
        *,
        config: TushareQlibProviderBundleConfig,
        client: TushareClient,
    ) -> pd.DataFrame:
        fields = "ts_code,symbol,name,area,industry,list_date,delist_date,is_hs"
        params = {
            "exchange": "",
            "list_status": ("L", "D", "P"),
            "fields": fields,
        }
        if config.reuse_staged and _staged_cache_metadata_matches(
            path,
            api_name="stock_basic",
            params=params,
        ):
            return _read_frame(path)
        frames: list[pd.DataFrame] = []
        for status in ("L", "D", "P"):
            try:
                result = client.call(
                    "stock_basic",
                    exchange="",
                    list_status=status,
                    fields=fields,
                )
            except TushareClientError as exc:
                raise TushareQlibProviderBundleError(
                    f"Tushare API 'stock_basic' failed for list_status={status}: {exc}"
                ) from exc
            frames.append(_ensure_frame(result, "stock_basic"))
        combined = _concat_frames(frames)
        if "ts_code" in combined.columns:
            combined = combined.drop_duplicates(subset=["ts_code"], keep="first")
        _write_frame(path, combined)
        _write_staged_cache_metadata(path, api_name="stock_basic", params=params)
        return combined



