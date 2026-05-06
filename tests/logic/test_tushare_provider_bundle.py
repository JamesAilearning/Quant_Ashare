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
    DEFAULT_MANIFEST_NAME,
    TushareMarketDataFetcher,
    TushareQlibProviderBundleConfig,
    TushareQlibProviderBundleError,
    TushareQlibProviderPublisher,
    TushareStagedMarketData,
    compare_provider_bundles,
)


class _StubClient:
    def __init__(self, *, missing_factor: bool = False, duplicate_daily: bool = False):
        self.missing_factor = missing_factor
        self.duplicate_daily = duplicate_daily
        self.calls: list[tuple[str, dict]] = []

    def call(self, api_name: str, **params):
        self.calls.append((api_name, dict(params)))
        if api_name == "trade_cal":
            return pd.DataFrame([
                {"cal_date": "20250102", "is_open": 1},
                {"cal_date": "20250103", "is_open": 1},
            ])
        if api_name == "stock_basic":
            status = params.get("list_status")
            if status == "L":
                return pd.DataFrame([
                    {"ts_code": "600000.SH", "symbol": "600000", "name": "PF Bank"},
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
                    }
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
                    }
                ],
            }[trade_date]
            if self.duplicate_daily and trade_date == "20250102":
                rows = rows + [dict(rows[0])]
            return pd.DataFrame(rows)
        if api_name == "adj_factor":
            if self.missing_factor and params["trade_date"] == "20250103":
                return pd.DataFrame(columns=["ts_code", "trade_date", "adj_factor"])
            factors = {
                "20250102": [{"ts_code": "600000.SH", "trade_date": "20250102", "adj_factor": 2.0}],
                "20250103": [{"ts_code": "600000.SH", "trade_date": "20250103", "adj_factor": 4.0}],
            }[params["trade_date"]]
            return pd.DataFrame(factors)
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


class ProviderPublishTests(unittest.TestCase):
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
        try:
            import qlib  # noqa: F401
        except ImportError:
            self.skipTest("qlib not importable")

        with tempfile.TemporaryDirectory() as tmp_text:
            tmp = Path(tmp_text)
            config = _base_config(tmp)
            result = TushareQlibProviderPublisher.publish(config, client=_StubClient())
            code = textwrap.dedent(
                f"""
                import qlib
                from qlib.constant import REG_CN
                from qlib.data import D
                qlib.init(provider_uri={result.output_dir!r}, region=REG_CN)
                instruments = D.list_instruments(D.instruments("all"), start_time="2025-01-02", end_time="2025-01-03")
                assert "SH600000" in instruments
                df = D.features(["SH600000"], ["$close"], start_time="2025-01-02", end_time="2025-01-03")
                assert not df.empty
                assert float(df.iloc[-1, 0]) == 20.0
                """
            )
            completed = subprocess.run(
                [sys.executable, "-c", code],
                cwd=str(PROJECT_ROOT),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
            )
            self.assertEqual(
                completed.returncode,
                0,
                msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
            )


if __name__ == "__main__":
    unittest.main()
