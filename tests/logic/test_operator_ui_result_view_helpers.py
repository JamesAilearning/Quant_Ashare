"""Regression tests for pure Results page display helpers."""

from __future__ import annotations

import unittest

import pandas as pd


class ResultViewHelperTests(unittest.TestCase):
    def test_filter_nav_frame_by_range_filters_display_rows_only(self) -> None:
        from web.operator_ui.result_view_helpers import filter_nav_frame_by_range

        frame = pd.DataFrame({
            "date": ["2026-01-01", "2026-04-01", "2026-05-01"],
            "strategy_nav": [1.0, 1.1, 1.2],
        })

        filtered = filter_nav_frame_by_range(frame, "1M")

        self.assertEqual(list(filtered["date"]), ["2026-04-01", "2026-05-01"])
        self.assertEqual(list(frame["date"]), ["2026-01-01", "2026-04-01", "2026-05-01"])

    def test_nav_y_range_always_includes_one(self) -> None:
        from web.operator_ui.result_view_helpers import nav_y_range

        frame = pd.DataFrame({"strategy_nav": [1.2, 1.3]})

        y_range = nav_y_range(frame)

        self.assertIsNotNone(y_range)
        assert y_range is not None
        self.assertLessEqual(y_range[0], 1.0)
        self.assertGreaterEqual(y_range[1], 1.3)

    def test_filter_log_text_searches_and_filters_severity(self) -> None:
        from web.operator_ui.result_view_helpers import filter_log_text

        text = "INFO started\nWARNING slow fetch\nERROR tushare failed\nplain context"

        filtered = filter_log_text(text, search="fetch", levels=("WARNING",))

        self.assertEqual(filtered, "WARNING slow fetch")

    def test_filter_log_text_keeps_untagged_lines_when_all_levels_selected(self) -> None:
        from web.operator_ui.result_view_helpers import LOG_LEVEL_OPTIONS, filter_log_text

        text = "INFO started\nplain context"

        filtered = filter_log_text(text, levels=LOG_LEVEL_OPTIONS)

        self.assertEqual(filtered, text)

    def test_filter_log_text_empty_severity_selection_returns_no_matches(self) -> None:
        from web.operator_ui.result_view_helpers import filter_log_text

        text = "INFO started\nERROR failed\nplain context"

        filtered = filter_log_text(text, levels=())

        self.assertEqual(filtered, "")


if __name__ == "__main__":
    unittest.main()
