"""Tests for the shared PIT ticker converters (src/data/pit/_common.py).

``qlib_to_ts_code`` was extracted from ``daily_recommend._qlib_code_to_ts_code``
(C2-d PR2) so the inference ST filter and the backtest ST mask share one
definition; these cases pin the conversion the inference path relied on plus
the round-trip against ``to_qlib_ticker``.
"""

from __future__ import annotations

import unittest

from src.data.pit._common import qlib_to_ts_code, to_qlib_ticker


class QlibToTsCodeTests(unittest.TestCase):
    def test_qlib_to_ts_code_exchanges(self) -> None:
        # The exact conversions the daily-recommend name lookup relied on.
        self.assertEqual(qlib_to_ts_code("SH600000"), "600000.SH")
        self.assertEqual(qlib_to_ts_code("SZ000001"), "000001.SZ")
        self.assertEqual(qlib_to_ts_code("BJ832317"), "832317.BJ")

    def test_already_ts_form_passthrough(self) -> None:
        # A value already containing '.' is returned unchanged.
        self.assertEqual(qlib_to_ts_code("600000.SH"), "600000.SH")

    def test_unrecognised_shape_passthrough(self) -> None:
        # No 2-letter exchange prefix -> returned unchanged (defer to caller).
        self.assertEqual(qlib_to_ts_code("123456"), "123456")
        self.assertEqual(qlib_to_ts_code(""), "")

    def test_round_trip_with_to_qlib_ticker(self) -> None:
        for qlib_code in ("SH600000", "SZ000001", "BJ832317"):
            self.assertEqual(
                to_qlib_ticker(qlib_to_ts_code(qlib_code)), qlib_code,
            )
        for ts_code in ("600000.SH", "000001.SZ", "832317.BJ"):
            self.assertEqual(
                qlib_to_ts_code(to_qlib_ticker(ts_code)), ts_code,
            )


if __name__ == "__main__":
    unittest.main()
