"""Qlib binary bundle publisher from Tushare market data."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from src.core.canonical_backtest_contract import (
    ADJUST_MODE_NONE,
    ADJUST_MODE_POST,
    ADJUST_MODE_PRE,
)
from src.core.logger import get_logger
from src.data.tushare.client import TushareClient
from src.data.tushare.industry_publisher import _tushare_to_qlib_instrument
from src.data.tushare.provider_bundle.config import TushareQlibProviderBundleConfig
from src.data.tushare.provider_bundle._types import (
    DEFAULT_COMPARISON_NAME,
    DEFAULT_MANIFEST_NAME,
    DEFAULT_VALIDATION_NAME,
    MANIFEST_SCHEMA_VERSION,
    PUBLISHER_VERSION,
    SOURCE_NAME,
    VALIDATION_SCHEMA_VERSION,
    _PreparedMarketData,
    _TUSHARE_AMOUNT_KYUAN_TO_YUAN,
    _TUSHARE_VOL_LOTS_TO_SHARES,
    TushareProviderComparisonReport,
    TushareQlibProviderBundleError,
    TushareQlibProviderManifest,
    TushareQlibProviderPublishResult,
    TushareQlibProviderValidationProfile,
    TushareStagedMarketData,
)
from src.data.tushare.provider_bundle._utils import (
    _concat_frames,
    _copy_file,
    _get_tushare_version,
    _parse_tushare_date_series,
    _replace_directory_atomically,
    _source_apis_for_config,
    _temporary_publish_dir,
    _write_json,
)
from src.data.tushare.provider_bundle.fetcher import TushareMarketDataFetcher

_logger = get_logger(__name__)

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
                    missing_market_dates=0,
                    missing_index_dates=0,
                    non_finite_factor_rows=0,
                    non_positive_factor_rows=0,
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

        # Calendar-coverage check: every open trading day must have at
        # least one market row. ``non_calendar_rows`` already catches
        # extra rows whose date is *not* in the calendar; this catches
        # the inverse ??a calendar date with *zero* daily rows. Without
        # this, an upstream Tushare hiccup that returns an empty
        # response for one trading day silently drops that day from the
        # published bundle, and downstream qlib backtests skip it as if
        # the market were closed. Hard-fail the publish.
        market_dates = set(daily["date"].unique())
        missing_market_date_list = sorted(set(open_dates) - market_dates)
        missing_market_dates_count = len(missing_market_date_list)
        if missing_market_date_list:
            errors.append("missing_market_dates")
            _logger.warning(
                "Tushare provider bundle: %d calendar trading day(s) have "
                "no market rows: %s%s",
                missing_market_dates_count,
                missing_market_date_list[:5],
                "..." if missing_market_dates_count > 5 else "",
            )
        missing_index_dates_count = 0
        if not index_daily.empty:
            index_dates = set(index_daily["date"].unique())
            missing_index_date_list = sorted(set(open_dates) - index_dates)
            missing_index_dates_count = len(missing_index_date_list)
            if missing_index_date_list:
                errors.append("missing_index_dates")
                _logger.warning(
                    "Tushare provider bundle: %d calendar trading day(s) "
                    "have no index rows: %s%s",
                    missing_index_dates_count,
                    missing_index_date_list[:5],
                    "..." if missing_index_dates_count > 5 else "",
                )

        # adj_factor must be finite and strictly positive ??non-numeric
        # strings, zero, or negative values would survive ``isna()``
        # checks below and only surface as ``NaN`` / ``Inf`` / sign-flipped
        # adjusted prices much later in ``_build_qlib_frame``. Hard-fail
        # at validation time instead of writing corrupt bins.
        adj_numeric = pd.to_numeric(adj["adj_factor"], errors="coerce")
        non_finite_factor_rows = int((~np.isfinite(adj_numeric)).sum())
        non_positive_factor_rows = int((adj_numeric <= 0).sum())
        if non_finite_factor_rows:
            errors.append("non_finite_adjustment_factors")
        if non_positive_factor_rows:
            errors.append("non_positive_adjustment_factors")

        # ``validate="one_to_one"`` raises ``pandas.errors.MergeError`` if
        # either side has duplicate (ts_code, date) keys. We already
        # called ``drop_duplicates`` above on both ``daily`` and ``adj``,
        # so a MergeError here would only fire if the dedup also left
        # multiple keys (extremely unlikely ??would need a dtype-shift
        # bug in pandas itself). Catch and surface as a structured
        # error instead of letting it propagate as an opaque exception
        # that bypasses the rest of the contract validation, so the
        # caller still gets the full ``errors`` list.
        try:
            merged = daily.merge(
                adj[["ts_code", "date", "adj_factor"]],
                on=["ts_code", "date"],
                how="left",
                validate="one_to_one",
            )
        except pd.errors.MergeError as exc:
            _logger.warning(
                "Tushare provider bundle: one_to_one merge failed (%s). "
                "Falling back to a left merge so the rest of the contract "
                "validation can continue; the error code "
                "'duplicate_market_or_factor_keys_after_dedup' is appended "
                "for the caller.",
                exc,
            )
            errors.append("duplicate_market_or_factor_keys_after_dedup")
            merged = daily.merge(
                adj[["ts_code", "date", "adj_factor"]],
                on=["ts_code", "date"],
                how="left",
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
            missing_market_dates=missing_market_dates_count,
            missing_index_dates=missing_index_dates_count,
            non_finite_factor_rows=non_finite_factor_rows,
            non_positive_factor_rows=non_positive_factor_rows,
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

        raw_close = frame["close"].copy()
        for col in ("open", "high", "low", "close"):
            frame[col] = frame[col] * frame["_scale"]
        # Convert Tushare's lot/??? units to qlib's share/yuan units.
        # See module-level ``_TUSHARE_*`` constants for the unit
        # explanation; using named constants here makes the unit
        # mismatch visible without the reader having to remember A-share
        # market conventions.
        frame["volume"] = frame["vol"] * _TUSHARE_VOL_LOTS_TO_SHARES
        frame["money"] = frame["amount"] * _TUSHARE_AMOUNT_KYUAN_TO_YUAN
        frame["vwap"] = np.where(
            frame["volume"] > 0,
            frame["money"] / frame["volume"],
            raw_close,
        )
        frame["vwap"] = frame["vwap"] * frame["_scale"]
        frame["factor"] = frame["_scale"]
        # ``change`` semantics
        # --------------------
        # qlib uses ``$change`` for the daily *investment return* (i.e.
        # close[t]/close[t-1] - 1 on the same adjustment basis as the
        # OHLC columns). Tushare's ``pct_chg`` is the raw unadjusted
        # daily price change percentage, which on a dividend / split day
        # diverges from the adjusted-close return: an ex-dividend day
        # shows a sharp negative ``pct_chg`` even though the
        # post-adjustment investment return is roughly zero.
        #
        # Previously we wrote ``pct_chg / 100`` directly into ``change``
        # whenever it was present, so in PRE/POST adjust modes the
        # ``change`` column was on a *different basis* than ``close`` ??        # silently breaking any downstream factor that joins them.
        #
        # Fix: in adjusted modes always recompute ``change`` from the
        # already-adjusted ``close`` so the two columns stay on the same
        # basis. In ``ADJUST_MODE_NONE`` we keep ``pct_chg`` (cheaper +
        # avoids floating-point drift on a column the consumer already
        # treats as raw-price-change anyway).
        if data_adjust_mode == ADJUST_MODE_NONE and "pct_chg" in frame.columns:
            frame["change"] = pd.to_numeric(frame["pct_chg"], errors="coerce") / 100.0
            source_hint = "pct_chg"
        else:
            frame["change"] = frame.groupby("instrument")["close"].pct_change()
            source_hint = "change (from pct_change)"
        coerced = frame["change"].isna()
        if coerced.any():
            _logger.warning(
                "%s has %d non-numeric/missing values coerced to 0.0.",
                source_hint, int(coerced.sum()),
            )
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

