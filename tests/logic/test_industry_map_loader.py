"""Tests for ``src.data.industry_map_loader``."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.industry_map_loader import (  # noqa: E402
    IndustryMapLoaderError,
    coerce_industry_map,
    load_industry_map,
)


def _write_csv(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class LoadIndustryMapTests(unittest.TestCase):
    def test_happy_path_returns_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "sw_l2.csv"
            _write_csv(p, [
                "instrument,industry_code",
                "SH600519,白酒",
                "SZ000858,白酒",
                "SH601398,银行",
            ])
            mapping = load_industry_map(p)
        self.assertEqual(mapping, {
            "SH600519": "白酒",
            "SZ000858": "白酒",
            "SH601398": "银行",
        })

    def test_extra_trailing_columns_tolerated(self) -> None:
        """A v2 publisher might add extra columns; v1 loader must ignore
        them rather than fail. The two base columns are the contract."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "sw_l2.csv"
            _write_csv(p, [
                "instrument,industry_code,industry_name_en",
                "SH600519,白酒,Liquor",
                "SH601398,银行,Bank",
            ])
            mapping = load_industry_map(p)
        self.assertEqual(mapping, {"SH600519": "白酒", "SH601398": "银行"})

    def test_missing_file_raises(self) -> None:
        with self.assertRaisesRegex(IndustryMapLoaderError, "does not exist"):
            load_industry_map("/no/such/path.csv")

    def test_missing_required_column_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.csv"
            _write_csv(p, ["foo,bar", "x,y"])
            with self.assertRaisesRegex(
                IndustryMapLoaderError, "missing a required column"
            ):
                load_industry_map(p)

    def test_completely_empty_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "empty.csv"
            p.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(
                IndustryMapLoaderError, "completely empty"
            ):
                load_industry_map(p)

    def test_header_only_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "head.csv"
            _write_csv(p, ["instrument,industry_code"])
            with self.assertRaisesRegex(
                IndustryMapLoaderError, "zero data rows"
            ):
                load_industry_map(p)

    def test_duplicate_instrument_raises(self) -> None:
        """Two industries claiming the same stock would silently
        overwrite in a plain dict, hiding the bad publish. Loud raise."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "dup.csv"
            _write_csv(p, [
                "instrument,industry_code",
                "SH600519,白酒",
                "SH600519,银行",  # duplicate key, contradictory industry
            ])
            with self.assertRaisesRegex(
                IndustryMapLoaderError, "Duplicate instrument"
            ):
                load_industry_map(p)

    def test_empty_cell_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "blank.csv"
            _write_csv(p, [
                "instrument,industry_code",
                "SH600519,",  # empty industry
            ])
            with self.assertRaisesRegex(
                IndustryMapLoaderError, "empty instrument.*or industry"
            ):
                load_industry_map(p)

    def test_blank_data_lines_tolerated(self) -> None:
        """A stray blank line in the middle of the data is harmless and
        should not abort the load."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "ok.csv"
            p.write_text(
                "instrument,industry_code\nSH600519,白酒\n\nSH601398,银行\n",
                encoding="utf-8",
            )
            mapping = load_industry_map(p)
        self.assertEqual(mapping, {"SH600519": "白酒", "SH601398": "银行"})


class CoerceIndustryMapTests(unittest.TestCase):
    def test_returns_plain_dict(self) -> None:
        # Pass a Mapping-but-not-dict (we just use a dict here; the
        # contract is "ends as plain dict[str, str]").
        result = coerce_industry_map({"SH600000": "银行"})
        self.assertIsInstance(result, dict)
        self.assertEqual(result, {"SH600000": "银行"})

    def test_coerces_non_string_keys_and_values(self) -> None:
        result = coerce_industry_map({600000: 123})  # type: ignore[dict-item]
        self.assertEqual(result, {"600000": "123"})


if __name__ == "__main__":
    unittest.main()
