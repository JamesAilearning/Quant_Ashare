"""Frozen dataclasses, error class, and module-level constants."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

import pandas as pd

from src.core.canonical_backtest_contract import ADJUST_MODE_NONE

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
STAGED_CACHE_METADATA_SUFFIX = ".meta.json"
STAGED_CACHE_METADATA_VERSION = "v1"

DEFAULT_MANIFEST_NAME = "tushare_provider_manifest.json"
DEFAULT_VALIDATION_NAME = "tushare_provider_validation.json"
DEFAULT_COMPARISON_NAME = "tushare_provider_comparison.json"

# Tushare → qlib unit conversions for the daily OHLCV table.
#
# Tushare's ``daily`` API publishes the A-share canonical units:
#   * ``vol``    — trading volume in *lots* (1 lot = 100 shares)
#   * ``amount`` — turnover in *thousands of CNY* (千元)
# qlib expects shares and yuan respectively, so we scale at ingest:
#   volume_shares = vol  * 100
#   money_yuan    = amount * 1000
# Naming the multipliers here keeps the units obvious at the call site
# in ``_build_qlib_frame`` and stops a later reader from "tidying up"
# the literal 100/1000 into the wrong scale.
_TUSHARE_VOL_LOTS_TO_SHARES: float = 100.0
_TUSHARE_AMOUNT_KYUAN_TO_YUAN: float = 1000.0


class TushareQlibProviderBundleError(RuntimeError):
    """Raised when a Tushare qlib provider bundle cannot be produced."""



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
    # Calendar-coverage gaps (open trading day with no market / index
    # rows). Surfaces upstream Tushare hiccups that would otherwise
    # silently drop a day from the published bundle.
    missing_market_dates: int
    missing_index_dates: int
    # adj_factor sanity: rows where the factor is non-finite (NaN/Inf,
    # often from a non-numeric Tushare value) or non-positive (zero or
    # negative). These would surface as NaN/Inf/sign-flipped adjusted
    # prices much later in ``_build_qlib_frame`` if not caught here.
    non_finite_factor_rows: int
    non_positive_factor_rows: int
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
