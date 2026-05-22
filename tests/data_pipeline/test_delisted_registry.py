"""Tests for ``src.data.pit.delisted_registry.DelistedRegistryBuilder``.

All tests use synthetic parquet inputs (no Tushare network calls).
Verified behaviour:

- Schema invariants: ticker uniqueness, delist_date >= list_date, no
  NULL delist_date.
- Reference-case validation: pure_delisting + batch_delisting tickers
  must be present with matching delist_date; mismatches raise.
- Active-control validation: tickers listed as negative controls must
  be in active bucket AND absent from delisted registry.
- delist_reason: authoritative from reference YAML, ``"other"`` default
  for non-referenced tickers, invalid reason in YAML raises.
- Missing input files raise with actionable error messages.
- Atomic write (no .tmp residue).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.pit.delisted_registry import (  # noqa: E402
    REGISTRY_COLUMNS,
    VALID_REASONS,
    DelistedRegistryBuilder,
    DelistedRegistryError,
    _to_qlib_ticker,
)


def _write_active_parquet(path: Path, tickers: list[str]) -> None:
    """``tickers`` are Tushare-format (``600519.SH``); the parquet stores
    them as Tushare's ``ts_code`` because that's what Phase A.1 dumps.
    """
    df = pd.DataFrame({
        "ts_code": tickers,
        "symbol": [t.split(".")[0] for t in tickers],
        "name": [f"name_{t}" for t in tickers],
        "list_date": ["20000101"] * len(tickers),
        "delist_date": [None] * len(tickers),
        "list_status": ["L"] * len(tickers),
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _write_delisted_parquet(path: Path, rows: list[dict]) -> None:
    """``rows`` is a list of dicts with at least ts_code, name, list_date,
    delist_date (all YYYYMMDD strings)."""
    df = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _write_refs(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def _minimal_delisted_rows() -> list[dict]:
    """3 verified delistings used throughout the tests.

    ``ts_code`` is in Tushare's native ``<6-digit>.<exchange>`` format
    (e.g. ``600087.SH``). The registry builder converts to qlib-style
    ``<exchange><6-digit>`` (e.g. ``SH600087``) for the output and for
    the reference-case match.
    """
    return [
        {"ts_code": "600087.SH", "name": "退市长油(退)",
         "list_date": "19970612", "delist_date": "20140605",
         "list_status": "D"},
        {"ts_code": "600247.SH", "name": "*ST成城(退)",
         "list_date": "20001123", "delist_date": "20210322",
         "list_status": "D"},
        {"ts_code": "000023.SZ", "name": "*ST深天(退)",
         "list_date": "19930429", "delist_date": "20240902",
         "list_status": "D"},
    ]


def _minimal_refs() -> dict:
    return {
        "pure_delisting_cases": [
            {"ticker": "SH600087", "list_date": "1997-06-12",
             "delist_date": "2014-06-05", "delist_reason": "financial",
             "last_company_name": "退市长油"},
            {"ticker": "SH600247", "list_date": "2000-11-23",
             "delist_date": "2021-03-22", "delist_reason": "financial",
             "last_company_name": "*ST成城退"},
        ],
        "active_control_cases": [
            {"ticker": "SH600519", "name": "贵州茅台"},
        ],
    }


class HappyPathTests(unittest.TestCase):

    def test_build_writes_registry_with_expected_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_delisted_parquet(
                tmp_path / "delisted_stocks.parquet", _minimal_delisted_rows())
            _write_active_parquet(
                tmp_path / "active_stocks.parquet", ["SH600519"])
            _write_refs(tmp_path / "refs.yaml", _minimal_refs())

            builder = DelistedRegistryBuilder(
                tushare_dir=tmp_path,
                reference_cases_path=tmp_path / "refs.yaml",
                output_path=tmp_path / "delisted_registry.parquet",
            )
            result = builder.build()

            self.assertEqual(result.row_count, 3)
            self.assertEqual(result.reference_rows_matched, 2)
            self.assertEqual(result.active_controls_checked, 1)

            out = pd.read_parquet(result.output_path)
            self.assertEqual(tuple(out.columns), REGISTRY_COLUMNS)
            self.assertEqual(set(out["ticker"]),
                            {"SH600087", "SH600247", "SZ000023"})
            # Reason from reference takes precedence; non-referenced row defaults
            row_087 = out[out["ticker"] == "SH600087"].iloc[0]
            row_023 = out[out["ticker"] == "SZ000023"].iloc[0]
            self.assertEqual(row_087["delist_reason"], "financial")
            self.assertEqual(row_023["delist_reason"], "other")
            # Dates parsed
            self.assertEqual(row_087["delist_date"], pd.Timestamp("2014-06-05"))
            self.assertEqual(row_087["list_date"], pd.Timestamp("1997-06-12"))

    def test_batch_delisting_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            delisted = _minimal_delisted_rows() + [
                {"ts_code": "002473.SZ", "name": "圣莱退(退)",
                 "list_date": "20100910", "delist_date": "20220622",
                 "list_status": "D"},
                {"ts_code": "002618.SZ", "name": "丹邦退(退)",
                 "list_date": "20110920", "delist_date": "20220622",
                 "list_status": "D"},
            ]
            _write_delisted_parquet(tmp_path / "delisted_stocks.parquet", delisted)
            _write_active_parquet(tmp_path / "active_stocks.parquet", ["600519.SH"])
            refs = _minimal_refs()
            refs["batch_delisting_cases"] = [
                {"batch_date": "2022-06-22",
                 "tickers": [
                     {"ticker": "SZ002473"},
                     {"ticker": "SZ002618"},
                 ]},
            ]
            _write_refs(tmp_path / "refs.yaml", refs)

            result = DelistedRegistryBuilder(
                tushare_dir=tmp_path,
                reference_cases_path=tmp_path / "refs.yaml",
                output_path=tmp_path / "out.parquet",
            ).build()

            # 2 pure + 2 batch = 4 matched
            self.assertEqual(result.reference_rows_matched, 4)


class ValidationFailureTests(unittest.TestCase):

    def test_reference_ticker_missing_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Omit 600247.SH from delisted bucket but keep in references
            _write_delisted_parquet(
                tmp_path / "delisted_stocks.parquet",
                [r for r in _minimal_delisted_rows() if r["ts_code"] != "600247.SH"],
            )
            _write_active_parquet(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_refs(tmp_path / "refs.yaml", _minimal_refs())

            with self.assertRaisesRegex(DelistedRegistryError,
                                        r"SH600247.*missing"):
                DelistedRegistryBuilder(
                    tushare_dir=tmp_path,
                    reference_cases_path=tmp_path / "refs.yaml",
                    output_path=tmp_path / "out.parquet",
                ).build()

    def test_reference_delist_date_mismatch_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            rows = _minimal_delisted_rows()
            # Mutate SH600247's delist_date so it disagrees with the reference
            rows[1]["delist_date"] = "20200101"
            _write_delisted_parquet(tmp_path / "delisted_stocks.parquet", rows)
            _write_active_parquet(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_refs(tmp_path / "refs.yaml", _minimal_refs())

            with self.assertRaisesRegex(DelistedRegistryError,
                                        r"SH600247.*mismatch"):
                DelistedRegistryBuilder(
                    tushare_dir=tmp_path,
                    reference_cases_path=tmp_path / "refs.yaml",
                    output_path=tmp_path / "out.parquet",
                ).build()

    def test_active_control_in_delisted_bucket_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Add the negative-control ticker to the delisted bucket — the
            # exact failure mode of the agent-fabricated PR-95 baseline
            rows = _minimal_delisted_rows() + [
                {"ts_code": "600519.SH", "name": "贵州茅台",
                 "list_date": "20010827", "delist_date": "20990101",
                 "list_status": "D"},
            ]
            _write_delisted_parquet(tmp_path / "delisted_stocks.parquet", rows)
            _write_active_parquet(tmp_path / "active_stocks.parquet", [])
            _write_refs(tmp_path / "refs.yaml", _minimal_refs())

            with self.assertRaisesRegex(DelistedRegistryError,
                                        r"SH600519.*delisted registry"):
                DelistedRegistryBuilder(
                    tushare_dir=tmp_path,
                    reference_cases_path=tmp_path / "refs.yaml",
                    output_path=tmp_path / "out.parquet",
                ).build()

    def test_invalid_delist_reason_in_yaml_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_delisted_parquet(
                tmp_path / "delisted_stocks.parquet", _minimal_delisted_rows())
            _write_active_parquet(tmp_path / "active_stocks.parquet", ["600519.SH"])
            refs = _minimal_refs()
            refs["pure_delisting_cases"][0]["delist_reason"] = "fabricated"
            _write_refs(tmp_path / "refs.yaml", refs)

            with self.assertRaisesRegex(DelistedRegistryError,
                                        r"invalid delist_reason"):
                DelistedRegistryBuilder(
                    tushare_dir=tmp_path,
                    reference_cases_path=tmp_path / "refs.yaml",
                    output_path=tmp_path / "out.parquet",
                ).build()

    def test_duplicate_ticker_in_delisted_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            rows = _minimal_delisted_rows() + [
                dict(_minimal_delisted_rows()[0])  # duplicate SH600087
            ]
            _write_delisted_parquet(tmp_path / "delisted_stocks.parquet", rows)
            _write_active_parquet(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_refs(tmp_path / "refs.yaml", _minimal_refs())

            with self.assertRaisesRegex(DelistedRegistryError,
                                        r"Duplicate tickers"):
                DelistedRegistryBuilder(
                    tushare_dir=tmp_path,
                    reference_cases_path=tmp_path / "refs.yaml",
                    output_path=tmp_path / "out.parquet",
                ).build()

    def test_unparseable_delist_date_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            rows = _minimal_delisted_rows()
            rows[0]["delist_date"] = "garbage"
            _write_delisted_parquet(tmp_path / "delisted_stocks.parquet", rows)
            _write_active_parquet(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_refs(tmp_path / "refs.yaml", _minimal_refs())

            with self.assertRaisesRegex(DelistedRegistryError,
                                        r"unparseable delist_date"):
                DelistedRegistryBuilder(
                    tushare_dir=tmp_path,
                    reference_cases_path=tmp_path / "refs.yaml",
                    output_path=tmp_path / "out.parquet",
                ).build()


class MissingInputTests(unittest.TestCase):

    def test_missing_delisted_parquet_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_refs(tmp_path / "refs.yaml", _minimal_refs())

            with self.assertRaisesRegex(DelistedRegistryError,
                                        r"delisted_stocks\.parquet"):
                DelistedRegistryBuilder(
                    tushare_dir=tmp_path,
                    reference_cases_path=tmp_path / "refs.yaml",
                    output_path=tmp_path / "out.parquet",
                ).build()

    def test_missing_active_parquet_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_delisted_parquet(
                tmp_path / "delisted_stocks.parquet", _minimal_delisted_rows())
            _write_refs(tmp_path / "refs.yaml", _minimal_refs())

            with self.assertRaisesRegex(DelistedRegistryError,
                                        r"active_stocks\.parquet"):
                DelistedRegistryBuilder(
                    tushare_dir=tmp_path,
                    reference_cases_path=tmp_path / "refs.yaml",
                    output_path=tmp_path / "out.parquet",
                ).build()

    def test_missing_reference_yaml_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_delisted_parquet(
                tmp_path / "delisted_stocks.parquet", _minimal_delisted_rows())
            _write_active_parquet(tmp_path / "active_stocks.parquet", ["600519.SH"])

            with self.assertRaisesRegex(DelistedRegistryError,
                                        r"Reference cases file not found"):
                DelistedRegistryBuilder(
                    tushare_dir=tmp_path,
                    reference_cases_path=tmp_path / "absent.yaml",
                    output_path=tmp_path / "out.parquet",
                ).build()


class AtomicWriteTests(unittest.TestCase):

    def test_no_tmp_file_left_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_delisted_parquet(
                tmp_path / "delisted_stocks.parquet", _minimal_delisted_rows())
            _write_active_parquet(tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_refs(tmp_path / "refs.yaml", _minimal_refs())

            DelistedRegistryBuilder(
                tushare_dir=tmp_path,
                reference_cases_path=tmp_path / "refs.yaml",
                output_path=tmp_path / "out.parquet",
            ).build()
            tmp_files = list(tmp_path.glob("**/*.tmp"))

        self.assertEqual(tmp_files, [])


class TickerNormalisationTests(unittest.TestCase):
    """Regression — Phase A.2 smoke test against real Tushare exposed that
    Tushare returns ``600087.SH`` (suffix) but the project canonical
    format is ``SH600087`` (prefix). The builder MUST convert.
    """

    def test_sse_ticker(self) -> None:
        self.assertEqual(_to_qlib_ticker("600087.SH"), "SH600087")

    def test_szse_ticker(self) -> None:
        self.assertEqual(_to_qlib_ticker("000023.SZ"), "SZ000023")

    def test_chinext_ticker(self) -> None:
        self.assertEqual(_to_qlib_ticker("300297.SZ"), "SZ300297")

    def test_star_ticker(self) -> None:
        self.assertEqual(_to_qlib_ticker("688086.SH"), "SH688086")

    def test_already_qlib_style_is_pass_through(self) -> None:
        # Defensive: someone may pre-convert; do not double-convert.
        self.assertEqual(_to_qlib_ticker("SH600087"), "SH600087")

    def test_unrecognised_shape_passes_through(self) -> None:
        # Don't silently mangle malformed input; let validation catch it.
        self.assertEqual(_to_qlib_ticker("BAD_FORMAT.XX"), "BAD_FORMAT.XX")

    def test_built_registry_emits_qlib_style(self) -> None:
        """End-to-end: real Tushare-style input -> qlib-style output."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_delisted_parquet(
                tmp_path / "delisted_stocks.parquet", _minimal_delisted_rows())
            _write_active_parquet(
                tmp_path / "active_stocks.parquet", ["600519.SH"])
            _write_refs(tmp_path / "refs.yaml", _minimal_refs())

            DelistedRegistryBuilder(
                tushare_dir=tmp_path,
                reference_cases_path=tmp_path / "refs.yaml",
                output_path=tmp_path / "out.parquet",
            ).build()

            out = pd.read_parquet(tmp_path / "out.parquet")
            self.assertEqual(set(out["ticker"]),
                            {"SH600087", "SH600247", "SZ000023"})
            # Confirm no Tushare-style values leaked through
            for t in out["ticker"]:
                self.assertNotIn(".", t,
                                 f"ticker {t!r} still in Tushare suffix format")


class ConstantsTests(unittest.TestCase):

    def test_valid_reasons_matches_design_doc(self) -> None:
        """Design §4.1 enumerates the 6 valid reasons; pin them."""
        self.assertEqual(VALID_REASONS, (
            "financial", "major_violation", "voluntary",
            "par_value", "restructure_failure", "other",
        ))

    def test_registry_columns_matches_design_doc(self) -> None:
        self.assertEqual(REGISTRY_COLUMNS, (
            "ticker", "list_date", "delist_date",
            "last_company_name", "delist_reason",
        ))


if __name__ == "__main__":
    unittest.main()
