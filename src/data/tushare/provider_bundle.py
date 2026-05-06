"""Publish a qlib provider bundle from Tushare A-share daily data.

This module is intentionally runtime-adjacent, not canonical runtime:
it talks to Tushare, stages raw payloads, validates them, and emits a
qlib-compatible provider directory that operators can opt into by setting
``provider_uri`` explicitly.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from src.core.canonical_backtest_contract import (
    ADJUST_MODE_NONE,
    ADJUST_MODE_POST,
    ADJUST_MODE_PRE,
    SUPPORTED_ADJUST_MODES,
)
from src.core.logger import get_logger
from src.data.tushare.client import TushareClient, TushareClientError
from src.data.tushare.industry_publisher import _tushare_to_qlib_instrument

_logger = get_logger(__name__)


PUBLISHER_VERSION = "v1"
MANIFEST_SCHEMA_VERSION = "v1"
VALIDATION_SCHEMA_VERSION = "v1"
SOURCE_NAME = "tushare"
SOURCE_APIS: tuple[str, ...] = ("daily", "adj_factor", "trade_cal", "stock_basic")
INDEX_SOURCE_API = "index_daily"
FORBIDDEN_CONFIG_KEYS: tuple[str, ...] = ("tushare_token", "token", "api_token")

STAGED_DAILY_DIR = "daily"
STAGED_ADJ_FACTOR_DIR = "adj_factor"
STAGED_INDEX_DAILY_DIR = "index_daily"
STAGED_TRADE_CAL_FILE = "trade_cal.csv"
STAGED_STOCK_BASIC_FILE = "stock_basic.csv"

DEFAULT_MANIFEST_NAME = "tushare_provider_manifest.json"
DEFAULT_VALIDATION_NAME = "tushare_provider_validation.json"
DEFAULT_COMPARISON_NAME = "tushare_provider_comparison.json"


class TushareQlibProviderBundleError(RuntimeError):
    """Raised when a Tushare qlib provider bundle cannot be produced."""


@dataclass(frozen=True)
class TushareQlibProviderBundleConfig:
    """Configuration for publishing an opt-in Tushare qlib provider bundle."""

    output_dir: str
    start_date: str
    end_date: str
    data_adjust_mode: str
    instruments: tuple[str, ...] = ("all",)
    staging_dir: str | None = None
    manifest_path: str | None = None
    validation_path: str | None = None
    comparison_path: str | None = None
    baseline_provider_uri: str | None = None
    benchmark_indexes: tuple[tuple[str, str], ...] = tuple()
    reuse_staged: bool = True
    region: str = "cn"
    freq: str = "day"

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "TushareQlibProviderBundleConfig":
        """Build config from a YAML/JSON mapping while rejecting secrets."""
        if not isinstance(raw, Mapping):
            raise TushareQlibProviderBundleError(
                f"Config must be a mapping, got {type(raw).__name__}."
            )

        forbidden = sorted(k for k in raw if str(k).lower() in FORBIDDEN_CONFIG_KEYS)
        if forbidden:
            raise TushareQlibProviderBundleError(
                "Tushare token fields are forbidden in config: "
                f"{forbidden}. Use the TUSHARE_TOKEN environment variable."
            )

        valid_fields = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        unknown = sorted(set(raw) - valid_fields)
        if unknown:
            raise TushareQlibProviderBundleError(
                f"Unknown Tushare provider config keys: {unknown}."
            )

        values = dict(raw)
        if "instruments" in values:
            values["instruments"] = _normalize_instrument_scope(values["instruments"])
        if "benchmark_indexes" in values:
            values["benchmark_indexes"] = _normalize_benchmark_indexes(
                values["benchmark_indexes"]
            )
        return cls(**values)

    def __post_init__(self) -> None:
        _require_non_empty_str(self.output_dir, "output_dir")
        _parse_iso_date(self.start_date, "start_date")
        _parse_iso_date(self.end_date, "end_date")
        if self.start_date > self.end_date:
            raise TushareQlibProviderBundleError(
                f"start_date ({self.start_date}) must be <= end_date ({self.end_date})."
            )
        if self.data_adjust_mode not in SUPPORTED_ADJUST_MODES:
            raise TushareQlibProviderBundleError(
                "Unsupported data_adjust_mode "
                f"{self.data_adjust_mode!r}. Allowed: {SUPPORTED_ADJUST_MODES}."
            )
        if not self.instruments:
            raise TushareQlibProviderBundleError("instruments must not be empty.")
        normalized_scope = _normalize_instrument_scope(self.instruments)
        object.__setattr__(self, "instruments", normalized_scope)
        object.__setattr__(
            self,
            "benchmark_indexes",
            _normalize_benchmark_indexes(self.benchmark_indexes),
        )
        if self.freq != "day":
            raise TushareQlibProviderBundleError(
                f"Only day frequency is supported in v1; got {self.freq!r}."
            )
        if self.region.strip().lower() != "cn":
            raise TushareQlibProviderBundleError(
                f"Only cn region is supported for A-share Tushare bundles; got {self.region!r}."
            )
        object.__setattr__(self, "region", "cn")

    @property
    def output_path(self) -> Path:
        return Path(self.output_dir)

    @property
    def staging_path(self) -> Path:
        if self.staging_dir:
            return Path(self.staging_dir)
        return self.output_path.parent / f".{self.output_path.name}.staging"

    @property
    def manifest_file(self) -> Path:
        if self.manifest_path:
            return Path(self.manifest_path)
        return self.output_path / DEFAULT_MANIFEST_NAME

    @property
    def validation_file(self) -> Path:
        if self.validation_path:
            return Path(self.validation_path)
        return self.output_path / DEFAULT_VALIDATION_NAME

    @property
    def comparison_file(self) -> Path:
        if self.comparison_path:
            return Path(self.comparison_path)
        return self.output_path / DEFAULT_COMPARISON_NAME

    @property
    def requested_tushare_codes(self) -> tuple[str, ...] | None:
        if self.instruments == ("all",):
            return None
        converted = []
        for instrument in self.instruments:
            ts_code = _qlib_to_tushare_instrument(instrument)
            if ts_code is None:
                raise TushareQlibProviderBundleError(
                    f"Unsupported instrument code {instrument!r}; expected qlib "
                    "shape SH600000/SZ000001/BJ430047 or Tushare shape 600000.SH."
                )
            converted.append(ts_code)
        return tuple(sorted(set(converted)))


@dataclass(frozen=True)
class TushareQlibProviderValidationProfile:
    """Auditable validation result for staged Tushare market data."""

    schema_version: str
    health: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    requested_start_date: str
    requested_end_date: str
    coverage_start_date: str | None
    coverage_end_date: str | None
    calendar_count: int
    row_count: int
    index_row_count: int
    adj_factor_row_count: int
    stock_basic_row_count: int
    instrument_count: int
    benchmark_count: int
    duplicate_market_rows: int
    duplicate_factor_rows: int
    duplicate_index_rows: int
    invalid_ohlcv_rows: int
    invalid_index_ohlcv_rows: int
    non_calendar_rows: int
    non_calendar_index_rows: int
    missing_factor_rows: int
    data_adjust_mode: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TushareQlibProviderManifest:
    """Manifest written beside every generated qlib provider bundle."""

    schema_version: str
    source_name: str
    source_apis: tuple[str, ...]
    source_package_version: str | None
    publisher_version: str
    bundle_format: str
    output_dir: str
    requested_start_date: str
    requested_end_date: str
    coverage_start_date: str | None
    coverage_end_date: str | None
    snapshot_at: str
    data_adjust_mode: str
    instruments_requested: tuple[str, ...]
    benchmark_indexes: tuple[tuple[str, str], ...]
    instrument_count: int
    benchmark_count: int
    row_count: int
    index_row_count: int
    calendar_count: int
    validation_health: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TushareProviderComparisonReport:
    """Informational comparison against an existing qlib provider."""

    baseline_provider_uri: str
    generated_provider_uri: str
    baseline_instrument_count: int
    generated_instrument_count: int
    overlap_instrument_count: int
    missing_from_generated: tuple[str, ...]
    new_in_generated: tuple[str, ...]
    baseline_calendar_count: int
    generated_calendar_count: int
    overlap_calendar_count: int
    compared_close_points: int
    max_abs_close_delta: float | None
    compared_volume_points: int
    max_abs_volume_delta: float | None
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TushareQlibProviderPublishResult:
    """Summary returned by a successful provider publish."""

    output_dir: str
    manifest_path: str
    validation_path: str
    comparison_path: str | None
    manifest: TushareQlibProviderManifest
    validation_profile: TushareQlibProviderValidationProfile
    comparison_report: TushareProviderComparisonReport | None


@dataclass(frozen=True)
class TushareStagedMarketData:
    """Raw staged payloads and their paths."""

    daily: pd.DataFrame
    adj_factor: pd.DataFrame
    trade_calendar: pd.DataFrame
    stock_basic: pd.DataFrame
    staging_dir: str
    daily_files: tuple[str, ...]
    adj_factor_files: tuple[str, ...]
    index_daily: pd.DataFrame = field(default_factory=pd.DataFrame)
    index_daily_files: tuple[str, ...] = tuple()


@dataclass(frozen=True)
class _PreparedMarketData:
    qlib_frame: pd.DataFrame
    calendar: tuple[str, ...]
    instruments: tuple[str, ...]
    validation_profile: TushareQlibProviderValidationProfile


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
            _write_frame(daily_path, daily)
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
            _write_frame(adj_path, adj)
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
        if config.reuse_staged and path.exists():
            return _read_frame(path)
        try:
            result = client.call(api_name, **dict(params))
        except TushareClientError as exc:
            raise TushareQlibProviderBundleError(
                f"Tushare API {api_name!r} failed while staging provider data: {exc}"
            ) from exc
        frame = _ensure_frame(result, api_name)
        _write_frame(path, frame)
        return frame

    @classmethod
    def _fetch_stock_basic(
        cls,
        path: Path,
        *,
        config: TushareQlibProviderBundleConfig,
        client: TushareClient,
    ) -> pd.DataFrame:
        if config.reuse_staged and path.exists():
            return _read_frame(path)
        frames: list[pd.DataFrame] = []
        for status in ("L", "D", "P"):
            try:
                result = client.call(
                    "stock_basic",
                    exchange="",
                    list_status=status,
                    fields="ts_code,symbol,name,area,industry,list_date,delist_date,is_hs",
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
        return combined


class TushareQlibProviderPublisher:
    """Validate staged Tushare data and publish a qlib provider bundle."""

    @classmethod
    def publish(
        cls,
        config: TushareQlibProviderBundleConfig,
        *,
        client: Optional[TushareClient] = None,
    ) -> TushareQlibProviderPublishResult:
        staged = TushareMarketDataFetcher.stage(config, client=client)
        prepared = cls.prepare_staged_data(staged, config)

        staging_validation = config.staging_path / DEFAULT_VALIDATION_NAME
        _write_json(staging_validation, prepared.validation_profile.to_dict())
        if prepared.validation_profile.health == "error":
            raise TushareQlibProviderBundleError(
                "Staged Tushare data failed validation: "
                f"{prepared.validation_profile.errors}. Validation profile: {staging_validation}"
            )

        final_dir = config.output_path
        temp_dir = _temporary_publish_dir(final_dir)
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        try:
            cls._write_qlib_bundle(
                prepared.qlib_frame,
                calendar=prepared.calendar,
                instruments=prepared.instruments,
                output_dir=temp_dir,
            )
            manifest = TushareQlibProviderManifest(
                schema_version=MANIFEST_SCHEMA_VERSION,
                source_name=SOURCE_NAME,
                source_apis=_source_apis_for_config(config),
                source_package_version=_get_tushare_version(),
                publisher_version=PUBLISHER_VERSION,
                bundle_format="qlib_bin_day",
                output_dir=str(final_dir),
                requested_start_date=config.start_date,
                requested_end_date=config.end_date,
                coverage_start_date=prepared.validation_profile.coverage_start_date,
                coverage_end_date=prepared.validation_profile.coverage_end_date,
                snapshot_at=datetime.now(timezone.utc).isoformat(),
                data_adjust_mode=config.data_adjust_mode,
                instruments_requested=config.instruments,
                benchmark_indexes=config.benchmark_indexes,
                instrument_count=prepared.validation_profile.instrument_count,
                benchmark_count=prepared.validation_profile.benchmark_count,
                row_count=prepared.validation_profile.row_count,
                index_row_count=prepared.validation_profile.index_row_count,
                calendar_count=prepared.validation_profile.calendar_count,
                validation_health=prepared.validation_profile.health,
            )
            _write_json(temp_dir / DEFAULT_MANIFEST_NAME, manifest.to_dict())
            _write_json(
                temp_dir / DEFAULT_VALIDATION_NAME,
                prepared.validation_profile.to_dict(),
            )
            comparison = None
            if config.baseline_provider_uri:
                comparison = compare_provider_bundles(
                    generated_provider_uri=str(temp_dir),
                    baseline_provider_uri=config.baseline_provider_uri,
                )
                _write_json(temp_dir / DEFAULT_COMPARISON_NAME, comparison.to_dict())

            _replace_directory_atomically(temp_dir, final_dir)
            _copy_file(final_dir / DEFAULT_MANIFEST_NAME, config.manifest_file)
            _copy_file(final_dir / DEFAULT_VALIDATION_NAME, config.validation_file)
            comparison_path: str | None = None
            if comparison is not None:
                _copy_file(final_dir / DEFAULT_COMPARISON_NAME, config.comparison_file)
                comparison_path = str(config.comparison_file)
            return TushareQlibProviderPublishResult(
                output_dir=str(final_dir),
                manifest_path=str(config.manifest_file),
                validation_path=str(config.validation_file),
                comparison_path=comparison_path,
                manifest=manifest,
                validation_profile=prepared.validation_profile,
                comparison_report=comparison,
            )
        except Exception:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    @classmethod
    def prepare_staged_data(
        cls,
        staged: TushareStagedMarketData,
        config: TushareQlibProviderBundleConfig,
    ) -> _PreparedMarketData:
        daily, adj, calendar, index_daily, errors, warnings = cls._normalize_and_validate_inputs(staged, config)
        profile_base = {
            "schema_version": VALIDATION_SCHEMA_VERSION,
            "requested_start_date": config.start_date,
            "requested_end_date": config.end_date,
            "data_adjust_mode": config.data_adjust_mode,
            "stock_basic_row_count": int(len(staged.stock_basic)),
        }

        if errors:
            return _PreparedMarketData(
                qlib_frame=pd.DataFrame(),
                calendar=tuple(),
                instruments=tuple(),
                validation_profile=TushareQlibProviderValidationProfile(
                    health="error",
                    errors=tuple(errors),
                    warnings=tuple(warnings),
                    coverage_start_date=None,
                    coverage_end_date=None,
                    calendar_count=0,
                    row_count=0,
                    index_row_count=0,
                    adj_factor_row_count=int(len(staged.adj_factor)),
                    instrument_count=0,
                    benchmark_count=0,
                    duplicate_market_rows=0,
                    duplicate_factor_rows=0,
                    duplicate_index_rows=0,
                    invalid_ohlcv_rows=0,
                    invalid_index_ohlcv_rows=0,
                    non_calendar_rows=0,
                    non_calendar_index_rows=0,
                    missing_factor_rows=0,
                    **profile_base,
                ),
            )

        open_dates = tuple(sorted(calendar["date"].dt.strftime("%Y-%m-%d").unique()))
        daily = daily.copy()
        adj = adj.copy()
        index_daily = index_daily.copy()

        duplicate_market_rows = int(daily.duplicated(["ts_code", "trade_date"]).sum())
        duplicate_factor_rows = int(adj.duplicated(["ts_code", "trade_date"]).sum())
        duplicate_index_rows = (
            int(index_daily.duplicated(["ts_code", "trade_date"]).sum())
            if not index_daily.empty
            else 0
        )
        if duplicate_market_rows:
            errors.append("duplicate_market_rows")
        if duplicate_factor_rows:
            errors.append("duplicate_factor_rows")
        if duplicate_index_rows:
            errors.append("duplicate_index_rows")

        daily = daily.drop_duplicates(["ts_code", "trade_date"], keep="last")
        adj = adj.drop_duplicates(["ts_code", "trade_date"], keep="last")
        if not index_daily.empty:
            index_daily = index_daily.drop_duplicates(["ts_code", "trade_date"], keep="last")

        valid_calendar = set(open_dates)
        daily["date"] = daily["trade_date"].dt.strftime("%Y-%m-%d")
        adj["date"] = adj["trade_date"].dt.strftime("%Y-%m-%d")
        if not index_daily.empty:
            index_daily["date"] = index_daily["trade_date"].dt.strftime("%Y-%m-%d")
        non_calendar_rows = int((~daily["date"].isin(valid_calendar)).sum())
        non_calendar_index_rows = (
            int((~index_daily["date"].isin(valid_calendar)).sum())
            if not index_daily.empty
            else 0
        )
        if non_calendar_rows:
            errors.append("calendar_alignment")
        if non_calendar_index_rows:
            errors.append("index_calendar_alignment")

        invalid_ohlcv_rows = cls._count_invalid_ohlcv(daily)
        invalid_index_ohlcv_rows = (
            cls._count_invalid_ohlcv(index_daily)
            if not index_daily.empty
            else 0
        )
        if invalid_ohlcv_rows:
            errors.append("invalid_ohlcv")
        if invalid_index_ohlcv_rows:
            errors.append("invalid_index_ohlcv")

        merged = daily.merge(
            adj[["ts_code", "date", "adj_factor"]],
            on=["ts_code", "date"],
            how="left",
            validate="one_to_one",
        )
        missing_factor_rows = int(merged["adj_factor"].isna().sum())
        if config.data_adjust_mode in (ADJUST_MODE_PRE, ADJUST_MODE_POST) and missing_factor_rows:
            errors.append("missing_adjustment_factors")
        if config.data_adjust_mode == ADJUST_MODE_NONE and missing_factor_rows:
            warnings.append("missing_adjustment_factors_for_unadjusted_output")
            merged["adj_factor"] = merged["adj_factor"].fillna(1.0)

        merged["instrument"] = merged["ts_code"].map(_tushare_to_qlib_instrument)
        merged = merged.dropna(subset=["instrument"])
        if merged.empty:
            errors.append("empty_instrument_coverage")

        if config.benchmark_indexes:
            if index_daily.empty:
                errors.append("empty_benchmark_index_data")
            else:
                expected_ts_codes = {ts_code for _, ts_code in config.benchmark_indexes}
                index_daily["ts_code"] = index_daily["ts_code"].astype(str).str.upper()
                index_daily = index_daily[index_daily["ts_code"].isin(expected_ts_codes)].copy()
                index_daily["instrument"] = index_daily["ts_code"].map(_tushare_to_qlib_instrument)
                missing_benchmarks = [
                    qlib_code
                    for qlib_code, ts_code in config.benchmark_indexes
                    if ts_code not in set(index_daily["ts_code"])
                ]
                if missing_benchmarks:
                    errors.append(
                        "missing_benchmark_index_data:"
                        + ",".join(sorted(missing_benchmarks))
                    )
                index_daily = index_daily.dropna(subset=["instrument"])
        else:
            index_daily = pd.DataFrame()

        if errors:
            health = "error"
            qlib_frame = pd.DataFrame()
            instruments: tuple[str, ...] = tuple()
            benchmark_count = 0
            index_row_count = 0
            coverage_start = None
            coverage_end = None
            row_count = 0
        else:
            stock_qlib_frame = cls._build_qlib_frame(merged, config.data_adjust_mode)
            index_qlib_frame = cls._build_index_qlib_frame(index_daily)
            qlib_frame = _concat_frames([stock_qlib_frame, index_qlib_frame])
            instruments = tuple(sorted(stock_qlib_frame["instrument"].unique()))
            benchmark_count = int(index_qlib_frame["instrument"].nunique()) if not index_qlib_frame.empty else 0
            index_row_count = int(len(index_qlib_frame))
            coverage_start = str(qlib_frame["date"].min())
            coverage_end = str(qlib_frame["date"].max())
            row_count = int(len(qlib_frame))
            health = "warning" if warnings else "ok"

        profile = TushareQlibProviderValidationProfile(
            health=health,
            errors=tuple(dict.fromkeys(errors)),
            warnings=tuple(dict.fromkeys(warnings)),
            coverage_start_date=coverage_start,
            coverage_end_date=coverage_end,
            calendar_count=len(open_dates),
            row_count=row_count,
            index_row_count=index_row_count,
            adj_factor_row_count=int(len(adj)),
            instrument_count=len(instruments),
            benchmark_count=benchmark_count,
            duplicate_market_rows=duplicate_market_rows,
            duplicate_factor_rows=duplicate_factor_rows,
            duplicate_index_rows=duplicate_index_rows,
            invalid_ohlcv_rows=invalid_ohlcv_rows,
            invalid_index_ohlcv_rows=invalid_index_ohlcv_rows,
            non_calendar_rows=non_calendar_rows,
            non_calendar_index_rows=non_calendar_index_rows,
            missing_factor_rows=missing_factor_rows,
            **profile_base,
        )
        return _PreparedMarketData(
            qlib_frame=qlib_frame,
            calendar=open_dates,
            instruments=instruments,
            validation_profile=profile,
        )

    @staticmethod
    def _normalize_and_validate_inputs(
        staged: TushareStagedMarketData,
        config: TushareQlibProviderBundleConfig,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        daily = staged.daily.copy()
        adj = staged.adj_factor.copy()
        calendar = staged.trade_calendar.copy()
        index_daily = staged.index_daily.copy()

        required_daily = {"ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"}
        required_adj = {"ts_code", "trade_date", "adj_factor"}
        required_index = {"ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"}
        required_calendar = {"cal_date"}
        missing_daily = required_daily - set(daily.columns)
        missing_adj = required_adj - set(adj.columns)
        missing_index = required_index - set(index_daily.columns) if config.benchmark_indexes else set()
        missing_calendar = required_calendar - set(calendar.columns)
        if missing_daily:
            errors.append(f"schema_mismatch_daily:{sorted(missing_daily)}")
        if missing_adj:
            errors.append(f"schema_mismatch_adj_factor:{sorted(missing_adj)}")
        if missing_index:
            errors.append(f"schema_mismatch_index_daily:{sorted(missing_index)}")
        if missing_calendar:
            errors.append(f"schema_mismatch_trade_cal:{sorted(missing_calendar)}")
        if errors:
            return daily, adj, calendar, index_daily, errors, warnings

        daily["trade_date"] = _parse_tushare_date_series(daily["trade_date"], "daily.trade_date", errors)
        adj["trade_date"] = _parse_tushare_date_series(adj["trade_date"], "adj_factor.trade_date", errors)
        if config.benchmark_indexes:
            index_daily["trade_date"] = _parse_tushare_date_series(
                index_daily["trade_date"],
                "index_daily.trade_date",
                errors,
            )
        calendar["date"] = _parse_tushare_date_series(calendar["cal_date"], "trade_cal.cal_date", errors)
        if errors:
            return daily, adj, calendar, index_daily, errors, warnings

        if "is_open" in calendar.columns:
            calendar = calendar[calendar["is_open"].astype(str).str.strip().isin(("1", "1.0", "True", "true"))]
        calendar = calendar[(calendar["date"] >= pd.Timestamp(config.start_date)) & (calendar["date"] <= pd.Timestamp(config.end_date))]
        if calendar.empty:
            errors.append("empty_trading_calendar")

        daily = daily[(daily["trade_date"] >= pd.Timestamp(config.start_date)) & (daily["trade_date"] <= pd.Timestamp(config.end_date))]
        adj = adj[(adj["trade_date"] >= pd.Timestamp(config.start_date)) & (adj["trade_date"] <= pd.Timestamp(config.end_date))]
        if config.benchmark_indexes:
            index_daily = index_daily[
                (index_daily["trade_date"] >= pd.Timestamp(config.start_date))
                & (index_daily["trade_date"] <= pd.Timestamp(config.end_date))
            ]
        if daily.empty:
            errors.append("empty_market_data")
        if config.benchmark_indexes and index_daily.empty:
            errors.append("empty_benchmark_index_data")
        return daily, adj, calendar, index_daily, errors, warnings

    @staticmethod
    def _count_invalid_ohlcv(daily: pd.DataFrame) -> int:
        numeric_cols = ("open", "high", "low", "close", "vol", "amount")
        frame = daily.copy()
        for col in numeric_cols:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        invalid = (
            frame[["open", "high", "low", "close"]].isna().any(axis=1)
            | (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
            | frame[["vol", "amount"]].isna().any(axis=1)
            | (frame[["vol", "amount"]] < 0).any(axis=1)
            | (frame["high"] < frame[["open", "close", "low"]].max(axis=1))
            | (frame["low"] > frame[["open", "close", "high"]].min(axis=1))
        )
        return int(invalid.sum())

    @staticmethod
    def _build_qlib_frame(merged: pd.DataFrame, data_adjust_mode: str) -> pd.DataFrame:
        frame = merged.copy()
        for col in ("open", "high", "low", "close", "vol", "amount", "adj_factor"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.sort_values(["instrument", "date"])
        if data_adjust_mode == ADJUST_MODE_NONE:
            frame["_scale"] = 1.0
        elif data_adjust_mode == ADJUST_MODE_PRE:
            anchors = frame.groupby("instrument")["adj_factor"].transform("last")
            frame["_scale"] = frame["adj_factor"] / anchors
        elif data_adjust_mode == ADJUST_MODE_POST:
            anchors = frame.groupby("instrument")["adj_factor"].transform("first")
            frame["_scale"] = frame["adj_factor"] / anchors
        else:
            raise TushareQlibProviderBundleError(
                f"Unsupported data_adjust_mode {data_adjust_mode!r}."
            )

        for col in ("open", "high", "low", "close"):
            frame[col] = frame[col] * frame["_scale"]
        frame["volume"] = frame["vol"] * 100.0
        frame["money"] = frame["amount"] * 1000.0
        frame["vwap"] = np.where(
            frame["volume"] > 0,
            frame["money"] / frame["volume"],
            frame["close"],
        )
        frame["vwap"] = frame["vwap"] * frame["_scale"]
        frame["factor"] = frame["_scale"]
        if "pct_chg" in frame.columns:
            frame["change"] = pd.to_numeric(frame["pct_chg"], errors="coerce") / 100.0
        else:
            frame["change"] = frame.groupby("instrument")["close"].pct_change()
        if "change" in frame.columns:
            frame["change"] = frame["change"].fillna(0.0)
        fields = [
            "instrument",
            "date",
            "open",
            "high",
            "low",
            "close",
            "vwap",
            "volume",
            "money",
            "factor",
            "change",
        ]
        return frame[fields].sort_values(["instrument", "date"]).reset_index(drop=True)

    @staticmethod
    def _build_index_qlib_frame(index_daily: pd.DataFrame) -> pd.DataFrame:
        if index_daily.empty:
            return pd.DataFrame()
        frame = index_daily.copy()
        frame["adj_factor"] = 1.0
        return TushareQlibProviderPublisher._build_qlib_frame(
            frame,
            ADJUST_MODE_NONE,
        )

    @staticmethod
    def _write_qlib_bundle(
        qlib_frame: pd.DataFrame,
        *,
        calendar: Sequence[str],
        instruments: Sequence[str],
        output_dir: Path,
    ) -> None:
        if qlib_frame.empty:
            raise TushareQlibProviderBundleError("Cannot publish empty qlib frame.")
        calendars_dir = output_dir / "calendars"
        instruments_dir = output_dir / "instruments"
        features_dir = output_dir / "features"
        calendars_dir.mkdir(parents=True, exist_ok=True)
        instruments_dir.mkdir(parents=True, exist_ok=True)
        features_dir.mkdir(parents=True, exist_ok=True)

        calendar_list = tuple(sorted(calendar))
        calendar_index = {d: idx for idx, d in enumerate(calendar_list)}
        (calendars_dir / "day.txt").write_text(
            "\n".join(calendar_list) + "\n",
            encoding="utf-8",
        )

        instrument_lines: list[str] = []
        for instrument in sorted(instruments):
            inst_df = qlib_frame[qlib_frame["instrument"] == instrument]
            if inst_df.empty:
                continue
            start = str(inst_df["date"].min())
            end = str(inst_df["date"].max())
            instrument_lines.append(f"{instrument}\t{start}\t{end}")
        (instruments_dir / "all.txt").write_text(
            "\n".join(instrument_lines) + "\n",
            encoding="utf-8",
        )

        feature_fields = ("open", "high", "low", "close", "vwap", "volume", "money", "factor", "change")
        for instrument, inst_df in qlib_frame.groupby("instrument", sort=True):
            inst_dir = features_dir / str(instrument).lower()
            inst_dir.mkdir(parents=True, exist_ok=True)
            inst_df = inst_df.drop_duplicates("date", keep="last").set_index("date").sort_index()
            start = str(inst_df.index.min())
            end = str(inst_df.index.max())
            date_slice = calendar_list[calendar_index[start]: calendar_index[end] + 1]
            aligned = inst_df.reindex(date_slice)
            start_index = float(calendar_index[start])
            for field in feature_fields:
                values = pd.to_numeric(aligned[field], errors="coerce").astype("float32").to_numpy()
                payload = np.hstack([[start_index], values]).astype("<f4")
                payload.tofile(inst_dir / f"{field}.day.bin")


def compare_provider_bundles(
    *,
    generated_provider_uri: str,
    baseline_provider_uri: str,
    max_price_instruments: int = 50,
) -> TushareProviderComparisonReport:
    """Create an informational comparison between two qlib provider dirs."""
    generated = Path(generated_provider_uri)
    baseline = Path(baseline_provider_uri)
    warnings: list[str] = []

    generated_instruments = _read_provider_instruments(generated)
    baseline_instruments = _read_provider_instruments(baseline)
    generated_calendar = _read_provider_calendar(generated)
    baseline_calendar = _read_provider_calendar(baseline)

    overlap_instruments = sorted(set(generated_instruments) & set(baseline_instruments))
    overlap_calendar = sorted(set(generated_calendar) & set(baseline_calendar))

    max_close_delta: float | None = None
    max_volume_delta: float | None = None
    close_points = 0
    volume_points = 0
    for instrument in overlap_instruments[:max_price_instruments]:
        for field, current_max, current_points in (
            ("close", max_close_delta, close_points),
            ("volume", max_volume_delta, volume_points),
        ):
            try:
                generated_series = _read_provider_feature(generated, instrument, field, generated_calendar)
                baseline_series = _read_provider_feature(baseline, instrument, field, baseline_calendar)
            except OSError as exc:
                warnings.append(f"missing_{field}_feature:{instrument}:{exc}")
                continue
            joined = pd.concat(
                [generated_series.rename("generated"), baseline_series.rename("baseline")],
                axis=1,
                join="inner",
            ).dropna()
            if joined.empty:
                continue
            delta = (joined["generated"] - joined["baseline"]).abs()
            if field == "close":
                close_points += int(len(delta))
                value = float(delta.max())
                max_close_delta = value if max_close_delta is None else max(max_close_delta, value)
            else:
                volume_points += int(len(delta))
                value = float(delta.max())
                max_volume_delta = value if max_volume_delta is None else max(max_volume_delta, value)

    return TushareProviderComparisonReport(
        baseline_provider_uri=str(baseline),
        generated_provider_uri=str(generated),
        baseline_instrument_count=len(baseline_instruments),
        generated_instrument_count=len(generated_instruments),
        overlap_instrument_count=len(overlap_instruments),
        missing_from_generated=tuple(sorted(set(baseline_instruments) - set(generated_instruments))[:20]),
        new_in_generated=tuple(sorted(set(generated_instruments) - set(baseline_instruments))[:20]),
        baseline_calendar_count=len(baseline_calendar),
        generated_calendar_count=len(generated_calendar),
        overlap_calendar_count=len(overlap_calendar),
        compared_close_points=close_points,
        max_abs_close_delta=max_close_delta,
        compared_volume_points=volume_points,
        max_abs_volume_delta=max_volume_delta,
        warnings=tuple(warnings[:20]),
    )


def _normalize_instrument_scope(value: Any) -> tuple[str, ...]:
    if value is None:
        return ("all",)
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
    elif isinstance(value, Iterable):
        parts = [str(p).strip() for p in value if str(p).strip()]
    else:
        raise TushareQlibProviderBundleError(
            f"instruments must be 'all', a comma-separated string, or a list; got {type(value).__name__}."
        )
    if not parts:
        raise TushareQlibProviderBundleError("instruments must not be empty.")
    if any(p.lower() == "all" for p in parts):
        if len(parts) > 1:
            raise TushareQlibProviderBundleError(
                "instruments='all' cannot be combined with explicit symbols."
            )
        return ("all",)
    normalized: list[str] = []
    for part in parts:
        qlib_code = _tushare_to_qlib_instrument(part.upper()) if "." in part else part.upper()
        if qlib_code is None:
            qlib_code = part.upper()
        normalized.append(qlib_code)
    return tuple(sorted(set(normalized)))


def _normalize_benchmark_indexes(value: Any) -> tuple[tuple[str, str], ...]:
    if value is None:
        return tuple()
    if isinstance(value, Mapping):
        raw_items = list(value.items())
    elif isinstance(value, str):
        raise TushareQlibProviderBundleError(
            "benchmark_indexes must be a mapping or list of qlib/Tushare code pairs."
        )
    elif isinstance(value, Iterable):
        raw_items = []
        for item in value:
            if isinstance(item, Mapping):
                qlib_code = item.get("qlib_code") or item.get("qlib")
                ts_code = item.get("tushare_code") or item.get("ts_code")
                raw_items.append((qlib_code, ts_code))
            elif isinstance(item, Sequence) and not isinstance(item, str) and len(item) == 2:
                raw_items.append((item[0], item[1]))
            else:
                raise TushareQlibProviderBundleError(
                    "benchmark_indexes entries must be mappings or two-item pairs."
                )
    else:
        raise TushareQlibProviderBundleError(
            f"benchmark_indexes must be a mapping or list; got {type(value).__name__}."
        )

    normalized: list[tuple[str, str]] = []
    seen_qlib: set[str] = set()
    for raw_qlib_code, raw_ts_code in raw_items:
        qlib_code = _normalize_qlib_index_code(raw_qlib_code)
        ts_code = _normalize_tushare_index_code(raw_ts_code)
        if qlib_code is None or ts_code is None:
            raise TushareQlibProviderBundleError(
                "benchmark_indexes must map qlib index codes like SH000300 "
                "to Tushare index codes like 000300.SH."
            )
        expected_qlib_code = _tushare_to_qlib_instrument(ts_code)
        if expected_qlib_code != qlib_code:
            raise TushareQlibProviderBundleError(
                f"benchmark index mapping mismatch: {qlib_code!r} does not "
                f"match Tushare code {ts_code!r}."
            )
        if qlib_code in seen_qlib:
            raise TushareQlibProviderBundleError(
                f"Duplicate benchmark index qlib code {qlib_code!r}."
            )
        seen_qlib.add(qlib_code)
        normalized.append((qlib_code, ts_code))
    return tuple(sorted(normalized))


def _normalize_qlib_index_code(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    if "." in text:
        return _tushare_to_qlib_instrument(text)
    if len(text) != 8:
        return None
    suffix, code = text[:2], text[2:]
    if suffix not in ("SH", "SZ") or not code.isdigit():
        return None
    return text


def _normalize_tushare_index_code(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if _tushare_to_qlib_instrument(text) is None:
        return None
    return text


def _qlib_to_tushare_instrument(instrument: str) -> str | None:
    text = str(instrument).strip().upper()
    if "." in text:
        return text if _tushare_to_qlib_instrument(text) is not None else None
    if len(text) != 8:
        return None
    suffix, code = text[:2], text[2:]
    if suffix not in ("SH", "SZ", "BJ") or not code.isdigit():
        return None
    return f"{code}.{suffix}"


def _source_apis_for_config(config: TushareQlibProviderBundleConfig) -> tuple[str, ...]:
    if config.benchmark_indexes:
        return SOURCE_APIS + (INDEX_SOURCE_API,)
    return SOURCE_APIS


def _require_non_empty_str(value: Any, field_name: str) -> None:
    if not str(value or "").strip():
        raise TushareQlibProviderBundleError(f"{field_name} is required.")


def _parse_iso_date(value: Any, field_name: str) -> date:
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise TushareQlibProviderBundleError(
            f"{field_name} must be ISO date YYYY-MM-DD; got {value!r}."
        ) from exc


def _to_tushare_date(value: str) -> str:
    return _parse_iso_date(value, "date").strftime("%Y%m%d")


def _parse_tushare_date_series(series: pd.Series, field_name: str, errors: list[str]) -> pd.Series:
    parsed = pd.to_datetime(series.astype(str), format="%Y%m%d", errors="coerce")
    if parsed.isna().any():
        errors.append(f"unparseable_date:{field_name}")
    return parsed


def _ensure_frame(result: Any, api_name: str) -> pd.DataFrame:
    if isinstance(result, pd.DataFrame):
        return result.copy()
    try:
        return pd.DataFrame(result)
    except Exception as exc:
        raise TushareQlibProviderBundleError(
            f"Tushare API {api_name!r} did not return DataFrame-like data."
        ) from exc


def _write_frame(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8")


def _read_frame(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def _concat_frames(frames: Sequence[pd.DataFrame]) -> pd.DataFrame:
    non_empty = [f for f in frames if f is not None and not f.empty]
    if not non_empty:
        for frame in frames:
            if frame is not None:
                return frame.copy().iloc[0:0]
        return pd.DataFrame()
    return pd.concat(non_empty, ignore_index=True, sort=False)


def _filter_tushare_codes(frame: pd.DataFrame, requested_codes: tuple[str, ...] | None) -> pd.DataFrame:
    if requested_codes is None or frame.empty or "ts_code" not in frame.columns:
        return frame
    return frame[frame["ts_code"].astype(str).str.upper().isin(set(requested_codes))].copy()


def _extract_open_trade_dates(trade_calendar: pd.DataFrame) -> tuple[str, ...]:
    if trade_calendar.empty or "cal_date" not in trade_calendar.columns:
        return tuple()
    frame = trade_calendar.copy()
    if "is_open" in frame.columns:
        frame = frame[frame["is_open"].astype(str).str.strip().isin(("1", "1.0", "True", "true"))]
    return tuple(sorted(frame["cal_date"].astype(str).str.replace("-", "", regex=False).unique()))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _copy_file(src: Path, dst: Path) -> None:
    if src.resolve() == dst.resolve():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _temporary_publish_dir(final_dir: Path) -> Path:
    return final_dir.parent / f".{final_dir.name}.publishing"


def _replace_directory_atomically(temp_dir: Path, final_dir: Path) -> None:
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    backup_dir = final_dir.parent / f".{final_dir.name}.previous"
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    if final_dir.exists():
        final_dir.rename(backup_dir)
    try:
        temp_dir.rename(final_dir)
    except Exception:
        if backup_dir.exists() and not final_dir.exists():
            backup_dir.rename(final_dir)
        raise
    if backup_dir.exists():
        shutil.rmtree(backup_dir)


def _get_tushare_version() -> str | None:
    try:
        import tushare as ts  # type: ignore[import-not-found]
    except ImportError:
        return None
    return str(getattr(ts, "__version__", "unknown"))


def _read_provider_calendar(provider_dir: Path) -> tuple[str, ...]:
    path = provider_dir / "calendars" / "day.txt"
    if not path.exists():
        return tuple()
    return tuple(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _read_provider_instruments(provider_dir: Path) -> tuple[str, ...]:
    path = provider_dir / "instruments" / "all.txt"
    if not path.exists():
        return tuple()
    instruments = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        instruments.append(line.split("\t")[0].strip().upper())
    return tuple(sorted(set(instruments)))


def _read_provider_feature(
    provider_dir: Path,
    instrument: str,
    field: str,
    calendar: Sequence[str],
) -> pd.Series:
    path = provider_dir / "features" / instrument.lower() / f"{field}.day.bin"
    payload = np.fromfile(path, dtype="<f4")
    if payload.size == 0:
        return pd.Series(dtype="float32")
    start_idx = int(payload[0])
    values = payload[1:]
    dates = list(calendar[start_idx: start_idx + len(values)])
    return pd.Series(values, index=pd.Index(dates, name="date"))
