"""Tests for Tushare OHLCV -> qlib provider bundle publishing."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ingest_tushare_qlib_provider import _load_config  # noqa: E402
from src.core.canonical_backtest_contract import ADJUST_MODE_NONE, ADJUST_MODE_PRE  # noqa: E402
from src.data.tushare.provider_bundle import (  # noqa: E402
    TushareMarketDataFetcher,
    TushareQlibProviderBundleConfig,
    TushareQlibProviderBundleError,
    TushareQlibProviderPublisher,
    TushareStagedMarketData,
    compare_provider_bundles,
)


class _StubClient:
    def __init__(
        self,
        *,
        missing_factor: bool = False,
        duplicate_daily: bool = False,
        duplicate_index: bool = False,
        missing_index: bool = False,
    ):
        self.missing_factor = missing_factor
        self.duplicate_daily = duplicate_daily
        self.duplicate_index = duplicate_index
        self.missing_index = missing_index
        self.calls: list[tuple[str, dict]] = []

    def call(self, api_name: str, **params):
        self.calls.append((api_name, dict(params)))
        if api_name == "trade_cal":
            rows = [
                {"cal_date": "20250102", "is_open": 1},
                {"cal_date": "20250103", "is_open": 1},
                {"cal_date": "20250106", "is_open": 1},
            ]
            start = str(params.get("start_date", "00000000"))
            end = str(params.get("end_date", "99999999"))
            return pd.DataFrame(
                [row for row in rows if start <= row["cal_date"] <= end]
            )
        if api_name == "stock_basic":
            status = params.get("list_status")
            if status == "L":
                return pd.DataFrame([
                    {"ts_code": "600000.SH", "symbol": "600000", "name": "PF Bank"},
                    {"ts_code": "000001.SZ", "symbol": "000001", "name": "PAB"},
                ])
            return pd.DataFrame(columns=["ts_code", "symbol", "name"])
        if api_name == "daily":
            trade_date = params["trade_date"]
            rows = {
                "20250102": [
                    {
                        "ts_code": "600000.SH",
                        "trade_date": "20250102",
                        "open": 10.0,
                        "high": 11.0,
                        "low": 9.0,
                        "close": 10.0,
                        "pct_chg": 0.0,
                        "vol": 100.0,
                        "amount": 100.0,
                    },
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": "20250102",
                        "open": 30.0,
                        "high": 31.0,
                        "low": 29.0,
                        "close": 30.0,
                        "pct_chg": 0.0,
                        "vol": 300.0,
                        "amount": 900.0,
                    },
                ],
                "20250103": [
                    {
                        "ts_code": "600000.SH",
                        "trade_date": "20250103",
                        "open": 20.0,
                        "high": 22.0,
                        "low": 18.0,
                        "close": 20.0,
                        "pct_chg": 100.0,
                        "vol": 200.0,
                        "amount": 400.0,
                    },
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": "20250103",
                        "open": 40.0,
                        "high": 41.0,
                        "low": 39.0,
                        "close": 40.0,
                        "pct_chg": 33.33,
                        "vol": 400.0,
                        "amount": 1600.0,
                    },
                ],
                "20250106": [
                    {
                        "ts_code": "600000.SH",
                        "trade_date": "20250106",
                        "open": 21.0,
                        "high": 23.0,
                        "low": 20.0,
                        "close": 22.0,
                        "pct_chg": 10.0,
                        "vol": 220.0,
                        "amount": 484.0,
                    },
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": "20250106",
                        "open": 41.0,
                        "high": 43.0,
                        "low": 40.0,
                        "close": 42.0,
                        "pct_chg": 5.0,
                        "vol": 420.0,
                        "amount": 1764.0,
                    },
                ],
            }[trade_date]
            if self.duplicate_daily and trade_date == "20250102":
                rows = rows + [dict(rows[0])]
            return pd.DataFrame(rows)
        if api_name == "adj_factor":
            if self.missing_factor and params["trade_date"] == "20250103":
                return pd.DataFrame(columns=["ts_code", "trade_date", "adj_factor"])
            factors = {
                "20250102": [
                    {"ts_code": "600000.SH", "trade_date": "20250102", "adj_factor": 2.0},
                    {"ts_code": "000001.SZ", "trade_date": "20250102", "adj_factor": 3.0},
                ],
                "20250103": [
                    {"ts_code": "600000.SH", "trade_date": "20250103", "adj_factor": 4.0},
                    {"ts_code": "000001.SZ", "trade_date": "20250103", "adj_factor": 4.0},
                ],
                "20250106": [
                    {"ts_code": "600000.SH", "trade_date": "20250106", "adj_factor": 4.4},
                    {"ts_code": "000001.SZ", "trade_date": "20250106", "adj_factor": 4.2},
                ],
            }[params["trade_date"]]
            return pd.DataFrame(factors)
        if api_name == "index_daily":
            if self.missing_index:
                return pd.DataFrame(columns=[
                    "ts_code",
                    "trade_date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "vol",
                    "amount",
                ])
            rows = [
                {
                    "ts_code": params["ts_code"],
                    "trade_date": "20250102",
                    "open": 4000.0,
                    "high": 4010.0,
                    "low": 3990.0,
                    "close": 4000.0,
                    "pct_chg": 0.0,
                    "vol": 10000.0,
                    "amount": 50000.0,
                },
                {
                    "ts_code": params["ts_code"],
                    "trade_date": "20250103",
                    "open": 4010.0,
                    "high": 4020.0,
                    "low": 4000.0,
                    "close": 4010.0,
                    "pct_chg": 0.25,
                    "vol": 12000.0,
                    "amount": 60000.0,
                },
            ]
            if self.duplicate_index:
                rows = rows + [dict(rows[0])]
            return pd.DataFrame(rows)
        raise AssertionError(f"unexpected API call: {api_name}")


def _base_config(tmp: Path, **overrides) -> TushareQlibProviderBundleConfig:
    values = {
        "output_dir": str(tmp / "qlib_tushare"),
        "staging_dir": str(tmp / "staging"),
        "manifest_path": str(tmp / "manifest.json"),
        "validation_path": str(tmp / "validation.json"),
        "start_date": "2025-01-02",
        "end_date": "2025-01-03",
        "data_adjust_mode": ADJUST_MODE_PRE,
        "instruments": ["SH600000"],
    }
    values.update(overrides)
    return TushareQlibProviderBundleConfig.from_mapping(values)


class ProviderConfigTests(unittest.TestCase):
    def test_rejects_token_fields(self) -> None:
        with self.assertRaisesRegex(TushareQlibProviderBundleError, "token"):
            TushareQlibProviderBundleConfig.from_mapping({
                "output_dir": "out",
                "start_date": "2025-01-01",
                "end_date": "2025-01-02",
                "data_adjust_mode": ADJUST_MODE_PRE,
                "tushare_token": "secret",
            })

    def test_rejects_unsupported_adjust_mode(self) -> None:
        with self.assertRaisesRegex(TushareQlibProviderBundleError, "Unsupported"):
            TushareQlibProviderBundleConfig.from_mapping({
                "output_dir": "out",
                "start_date": "2025-01-01",
                "end_date": "2025-01-02",
                "data_adjust_mode": "mystery",
            })

    def test_rejects_mismatched_benchmark_mapping(self) -> None:
        with self.assertRaisesRegex(TushareQlibProviderBundleError, "mismatch"):
            TushareQlibProviderBundleConfig.from_mapping({
                "output_dir": "out",
                "start_date": "2025-01-01",
                "end_date": "2025-01-02",
                "data_adjust_mode": ADJUST_MODE_PRE,
                "benchmark_indexes": {"SH000300": "399006.SZ"},
            })

    def test_script_loader_rejects_token_in_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.yaml"
            path.write_text(
                textwrap.dedent(
                    """
                    output_dir: out
                    start_date: "2025-01-01"
                    end_date: "2025-01-02"
                    data_adjust_mode: pre_adjusted
                    tushare_token: secret
                    """
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(TushareQlibProviderBundleError, "token"):
                _load_config(str(path))


class StagingTests(unittest.TestCase):
    @staticmethod
    def _call_count(client: _StubClient, api_name: str) -> int:
        return sum(1 for name, _params in client.calls if name == api_name)

    def test_staging_reuses_existing_payloads_for_same_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_text:
            tmp = Path(tmp_text)
            config = _base_config(tmp)
            client = _StubClient()
            staged = TushareMarketDataFetcher.stage(config, client=client)
            first_call_count = len(client.calls)
            self.assertEqual(len(staged.daily), 2)

            staged_again = TushareMarketDataFetcher.stage(config, client=client)
            self.assertEqual(len(staged_again.daily), 2)
            self.assertEqual(len(client.calls), first_call_count)

    def test_staging_refetches_cache_when_date_range_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_text:
            tmp = Path(tmp_text)
            short = _base_config(tmp, end_date="2025-01-02")
            client = _StubClient()
            staged_short = TushareMarketDataFetcher.stage(short, client=client)
            self.assertEqual(
                set(staged_short.daily["trade_date"].astype(str)),
                {"20250102"},
            )
            trade_cal_calls = self._call_count(client, "trade_cal")

            expanded = _base_config(tmp)
            staged_expanded = TushareMarketDataFetcher.stage(expanded, client=client)
            self.assertEqual(
                set(staged_expanded.trade_calendar["cal_date"].astype(str)),
                {"20250102", "20250103"},
            )
            self.assertEqual(
                set(staged_expanded.daily["trade_date"].astype(str)),
                {"20250102", "20250103"},
            )
            self.assertGreater(self._call_count(client, "trade_cal"), trade_cal_calls)

    def test_staging_preserves_raw_payloads_across_instrument_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_text:
            tmp = Path(tmp_text)
            subset = _base_config(tmp, instruments=["SH600000"])
            client = _StubClient()
            staged_subset = TushareMarketDataFetcher.stage(subset, client=client)
            self.assertEqual(set(staged_subset.daily["ts_code"].astype(str)), {"600000.SH"})

            raw_daily = pd.read_csv(staged_subset.daily_files[0])
            self.assertEqual(
                set(raw_daily["ts_code"].astype(str)),
                {"600000.SH", "000001.SZ"},
            )

            wider = _base_config(tmp, instruments=["all"])
            staged_wider = TushareMarketDataFetcher.stage(wider, client=client)
            self.assertEqual(
                set(staged_wider.daily["ts_code"].astype(str)),
                {"600000.SH", "000001.SZ"},
            )


class ProviderPublishTests(unittest.TestCase):
    def test_zero_volume_vwap_fallback_applies_adjustment_once(self) -> None:
        merged = pd.DataFrame(
            [
                {
                    "instrument": "SH600000",
                    "date": "2025-01-02",
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "vol": 0.0,
                    "amount": 0.0,
                    "adj_factor": 2.0,
                    "pct_chg": 0.0,
                },
                {
                    "instrument": "SH600000",
                    "date": "2025-01-03",
                    "open": 12.0,
                    "high": 12.0,
                    "low": 12.0,
                    "close": 12.0,
                    "vol": 100.0,
                    "amount": 120.0,
                    "adj_factor": 4.0,
                    "pct_chg": 20.0,
                },
            ]
        )

        frame = TushareQlibProviderPublisher._build_qlib_frame(
            merged, ADJUST_MODE_PRE,
        )

        first = frame.loc[frame["date"] == "2025-01-02"].iloc[0]
        self.assertAlmostEqual(float(first["factor"]), 0.5)
        self.assertAlmostEqual(float(first["close"]), 5.0)
        self.assertAlmostEqual(float(first["vwap"]), 5.0)

    def test_publish_writes_qlib_layout_manifest_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_text:
            tmp = Path(tmp_text)
            config = _base_config(tmp)
            result = TushareQlibProviderPublisher.publish(config, client=_StubClient())

            output = Path(result.output_dir)
            self.assertTrue((output / "calendars" / "day.txt").exists())
            self.assertTrue((output / "instruments" / "all.txt").exists())
            self.assertTrue((output / "features" / "sh600000" / "close.day.bin").exists())
            self.assertTrue(Path(result.manifest_path).exists())
            self.assertTrue(Path(result.validation_path).exists())

            with open(result.manifest_path, encoding="utf-8") as handle:
                manifest = json.load(handle)
            self.assertEqual(manifest["source_name"], "tushare")
            self.assertEqual(manifest["data_adjust_mode"], ADJUST_MODE_PRE)
            self.assertNotIn("token", json.dumps(manifest).lower())

            close_payload = np.fromfile(output / "features" / "sh600000" / "close.day.bin", dtype="<f4")
            self.assertEqual(close_payload[0], 0.0)
            self.assertAlmostEqual(float(close_payload[1]), 5.0)
            self.assertAlmostEqual(float(close_payload[2]), 20.0)

    def test_publish_writes_configured_benchmark_without_adding_to_universe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_text:
            tmp = Path(tmp_text)
            config = _base_config(
                tmp,
                benchmark_indexes={"SH000300": "000300.SH"},
            )
            result = TushareQlibProviderPublisher.publish(config, client=_StubClient())

            output = Path(result.output_dir)
            self.assertTrue((output / "features" / "sh000300" / "close.day.bin").exists())
            all_text = (output / "instruments" / "all.txt").read_text(encoding="utf-8")
            self.assertIn("SH600000", all_text)
            self.assertNotIn("SH000300", all_text)

            with open(result.manifest_path, encoding="utf-8") as handle:
                manifest = json.load(handle)
            self.assertEqual(manifest["benchmark_indexes"], [["SH000300", "000300.SH"]])
            self.assertEqual(manifest["benchmark_count"], 1)
            self.assertEqual(manifest["index_row_count"], 2)
            self.assertIn("index_daily", manifest["source_apis"])

            index_close_payload = np.fromfile(
                output / "features" / "sh000300" / "close.day.bin",
                dtype="<f4",
            )
            self.assertEqual(index_close_payload[0], 0.0)
            self.assertAlmostEqual(float(index_close_payload[1]), 4000.0)
            self.assertAlmostEqual(float(index_close_payload[2]), 4010.0)

    def test_unadjusted_output_does_not_require_complete_factors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_text:
            tmp = Path(tmp_text)
            config = _base_config(tmp, data_adjust_mode=ADJUST_MODE_NONE)
            result = TushareQlibProviderPublisher.publish(
                config,
                client=_StubClient(missing_factor=True),
            )
            self.assertEqual(result.validation_profile.health, "warning")
            self.assertIn(
                "missing_adjustment_factors_for_unadjusted_output",
                result.validation_profile.warnings,
            )

    def test_adjusted_output_fails_on_missing_factors_and_preserves_existing_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_text:
            tmp = Path(tmp_text)
            output = tmp / "qlib_tushare"
            output.mkdir()
            marker = output / "marker.txt"
            marker.write_text("old", encoding="utf-8")
            config = _base_config(tmp)

            with self.assertRaisesRegex(TushareQlibProviderBundleError, "missing_adjustment_factors"):
                TushareQlibProviderPublisher.publish(
                    config,
                    client=_StubClient(missing_factor=True),
                )
            self.assertEqual(marker.read_text(encoding="utf-8"), "old")
            self.assertTrue((config.staging_path / "tushare_provider_validation.json").exists())

    def test_duplicate_market_rows_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_text:
            tmp = Path(tmp_text)
            config = _base_config(tmp)
            with self.assertRaisesRegex(TushareQlibProviderBundleError, "duplicate_market_rows"):
                TushareQlibProviderPublisher.publish(
                    config,
                    client=_StubClient(duplicate_daily=True),
                )

    def test_duplicate_index_rows_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_text:
            tmp = Path(tmp_text)
            config = _base_config(
                tmp,
                benchmark_indexes={"SH000300": "000300.SH"},
            )
            with self.assertRaisesRegex(TushareQlibProviderBundleError, "duplicate_index_rows"):
                TushareQlibProviderPublisher.publish(
                    config,
                    client=_StubClient(duplicate_index=True),
                )

    def test_missing_index_rows_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_text:
            tmp = Path(tmp_text)
            config = _base_config(
                tmp,
                benchmark_indexes={"SH000300": "000300.SH"},
            )
            with self.assertRaisesRegex(TushareQlibProviderBundleError, "empty_benchmark_index_data"):
                TushareQlibProviderPublisher.publish(
                    config,
                    client=_StubClient(missing_index=True),
                )

    def test_non_calendar_rows_are_rejected(self) -> None:
        staged = TushareStagedMarketData(
            daily=pd.DataFrame([
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20250104",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10,
                    "vol": 100,
                    "amount": 100,
                }
            ]),
            adj_factor=pd.DataFrame([
                {"ts_code": "600000.SH", "trade_date": "20250104", "adj_factor": 1.0}
            ]),
            trade_calendar=pd.DataFrame([
                {"cal_date": "20250102", "is_open": 1}
            ]),
            stock_basic=pd.DataFrame(),
            staging_dir="unused",
            daily_files=tuple(),
            adj_factor_files=tuple(),
        )
        with tempfile.TemporaryDirectory() as tmp_text:
            prepared = TushareQlibProviderPublisher.prepare_staged_data(
                staged,
                _base_config(Path(tmp_text), start_date="2025-01-02", end_date="2025-01-04"),
            )
        self.assertEqual(prepared.validation_profile.health, "error")
        self.assertIn("calendar_alignment", prepared.validation_profile.errors)

    def test_empty_market_coverage_is_rejected(self) -> None:
        staged = TushareStagedMarketData(
            daily=pd.DataFrame(columns=["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"]),
            adj_factor=pd.DataFrame(columns=["ts_code", "trade_date", "adj_factor"]),
            trade_calendar=pd.DataFrame([
                {"cal_date": "20250102", "is_open": 1}
            ]),
            stock_basic=pd.DataFrame(),
            staging_dir="unused",
            daily_files=tuple(),
            adj_factor_files=tuple(),
        )
        with tempfile.TemporaryDirectory() as tmp_text:
            prepared = TushareQlibProviderPublisher.prepare_staged_data(
                staged,
                _base_config(Path(tmp_text), start_date="2025-01-02", end_date="2025-01-02"),
            )
        self.assertEqual(prepared.validation_profile.health, "error")
        self.assertIn("empty_market_data", prepared.validation_profile.errors)

    def test_invalid_ohlcv_rows_are_rejected(self) -> None:
        staged = TushareStagedMarketData(
            daily=pd.DataFrame([
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20250102",
                    "open": 10,
                    "high": 9,
                    "low": 8,
                    "close": 10,
                    "vol": 100,
                    "amount": 100,
                }
            ]),
            adj_factor=pd.DataFrame([
                {"ts_code": "600000.SH", "trade_date": "20250102", "adj_factor": 1.0}
            ]),
            trade_calendar=pd.DataFrame([
                {"cal_date": "20250102", "is_open": 1}
            ]),
            stock_basic=pd.DataFrame(),
            staging_dir="unused",
            daily_files=tuple(),
            adj_factor_files=tuple(),
        )
        with tempfile.TemporaryDirectory() as tmp_text:
            prepared = TushareQlibProviderPublisher.prepare_staged_data(
                staged,
                _base_config(Path(tmp_text), start_date="2025-01-02", end_date="2025-01-02"),
            )
        self.assertEqual(prepared.validation_profile.health, "error")
        self.assertIn("invalid_ohlcv", prepared.validation_profile.errors)

    def test_comparison_report_is_informational(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_text:
            tmp = Path(tmp_text)
            config = _base_config(tmp)
            result = TushareQlibProviderPublisher.publish(config, client=_StubClient())
            baseline = tmp / "baseline"
            shutil.copytree(result.output_dir, baseline)

            report = compare_provider_bundles(
                generated_provider_uri=result.output_dir,
                baseline_provider_uri=str(baseline),
            )
            self.assertEqual(report.overlap_instrument_count, 1)
            self.assertEqual(report.compared_close_points, 2)
            self.assertEqual(report.max_abs_close_delta, 0.0)

    def test_generated_bundle_can_initialize_qlib_when_available(self) -> None:
        qlib_check = subprocess.run(
            [sys.executable, "-c", "import qlib"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if qlib_check.returncode != 0:
            self.skipTest("qlib not importable in subprocess")

        with tempfile.TemporaryDirectory() as tmp_text:
            tmp = Path(tmp_text)
            config = _base_config(
                tmp,
                benchmark_indexes={"SH000300": "000300.SH"},
            )
            result = TushareQlibProviderPublisher.publish(config, client=_StubClient())
            code = textwrap.dedent(
                f"""
                import qlib
                from qlib.constant import REG_CN
                from qlib.data import D
                qlib.init(provider_uri={result.output_dir!r}, region=REG_CN)
                instruments = D.list_instruments(D.instruments("all"), start_time="2025-01-02", end_time="2025-01-03")
                assert "SH600000" in instruments
                assert "SH000300" not in instruments
                df = D.features(["SH600000"], ["$close"], start_time="2025-01-02", end_time="2025-01-03")
                assert not df.empty
                assert float(df.iloc[-1, 0]) == 20.0
                benchmark = D.features(["SH000300"], ["$close"], start_time="2025-01-02", end_time="2025-01-03")
                assert not benchmark.empty
                assert float(benchmark.iloc[-1, 0]) == 4010.0
                """
            )
            completed = subprocess.run(
                [sys.executable, "-c", code],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(
                completed.returncode,
                0,
                msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
            )


if __name__ == "__main__":
    unittest.main()
