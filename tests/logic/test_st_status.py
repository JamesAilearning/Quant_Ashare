"""Tests for the shared A-share ST predicate (src/data/st_status.py).

Marker coverage is driven by the markers that actually appear in
``all_namechanges.parquet`` / ``active_stocks.parquet`` (see the module
docstring): ST / *ST / SST / S*ST and resumption-day NST are ST; bare S
(share-reform), N (new listing), XD/DR (ex-div/rights) and Latin company
names are not.
"""

from __future__ import annotations

import unittest

from src.data.st_status import current_st_codes, is_st_name


class IsStNameTests(unittest.TestCase):
    def test_st_family_matched(self) -> None:
        for nm in ["ST康美", "*ST金亚", "SST佳通", "S*ST佳通", "NST毅达"]:
            self.assertTrue(is_st_name(nm), nm)

    def test_non_st_not_matched(self) -> None:
        for nm in [
            "平安银行",      # plain name
            "S佳通",         # bare S = share-reform pending, NOT ST
            "N浙传媒",       # new-listing marker
            "XD金牛",        # ex-dividend marker
            "DR隆基",        # ex-rights marker
            "TCL科技",       # Latin company name
            "GQY视讯",       # Latin company name
            "STAR环球",      # Latin 'STAR' — trailing-letter guard excludes it
            "*金亚",         # truncated tushare name (ST dropped) -> PR2's reason
                            # cross-check, NOT this name-only predicate
            "",              # empty
        ]:
            self.assertFalse(is_st_name(nm), nm)

    def test_none_is_false(self) -> None:
        self.assertFalse(is_st_name(None))

    def test_leading_whitespace_stripped(self) -> None:
        self.assertTrue(is_st_name("  *ST金亚 "))

    def test_fullwidth_marker_normalised(self) -> None:
        # Full-width ＊ / Ｓ / Ｔ (U+FF0A/FF33/FF34) -> matched (defensive).
        self.assertTrue(is_st_name("＊ST金亚"))    # full-width star
        self.assertTrue(is_st_name("ＳＴ海王"))    # full-width S+T
        self.assertTrue(is_st_name("Ｓ＊ＳＴ佳通"))  # full-width S*ST

    def test_fullwidth_company_letter_not_false_positive(self) -> None:
        # Full-width 'Ａ' (U+FF21) in a real name must NOT be normalised to a
        # marker — 万科Ａ is not ST.
        self.assertFalse(is_st_name("万科Ａ"))


class CurrentStCodesTests(unittest.TestCase):
    def test_filters_to_st_only(self) -> None:
        names = {
            "SZ000004": "*ST国华",
            "SH600000": "浦发银行",
            "SZ000078": "ST海王",
            "SH600519": "贵州茅台",
            "SH600182": "S*ST佳通",
        }
        self.assertEqual(
            current_st_codes(names), {"SZ000004", "SZ000078", "SH600182"},
        )

    def test_empty_map(self) -> None:
        self.assertEqual(current_st_codes({}), set())


if __name__ == "__main__":
    unittest.main()
