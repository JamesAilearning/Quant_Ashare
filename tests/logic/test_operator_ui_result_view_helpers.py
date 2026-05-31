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

    def test_nav_y_range_includes_one_when_curve_hugs_baseline(self) -> None:
        """Within the ``[0.7, 1.5]`` window the axis still anchors on the
        1.0 baseline (UI review P2-5)."""
        from web.operator_ui.result_view_helpers import nav_y_range

        frame = pd.DataFrame({"strategy_nav": [1.2, 1.3]})

        y_range = nav_y_range(frame)

        self.assertIsNotNone(y_range)
        assert y_range is not None
        self.assertLessEqual(y_range[0], 1.0)
        self.assertGreaterEqual(y_range[1], 1.3)

    def test_nav_y_range_does_not_anchor_one_for_high_nav(self) -> None:
        """When the strategy compounds well above the baseline (3×), the
        axis MUST fit the data instead of squashing it against 1.0 — the
        separate ``add_hline(y=1.0)`` reference covers break-even
        (UI review P2-5)."""
        from web.operator_ui.result_view_helpers import nav_y_range

        frame = pd.DataFrame({"strategy_nav": [2.8, 3.0, 3.2]})

        y_range = nav_y_range(frame)

        self.assertIsNotNone(y_range)
        assert y_range is not None
        # Lower bound hugs the data (~2.8), NOT pulled down to 1.0.
        self.assertGreater(y_range[0], 2.0)
        self.assertGreaterEqual(y_range[1], 3.2)

    def test_nav_y_range_does_not_anchor_one_for_low_nav(self) -> None:
        """Symmetric case — a strategy that drew down well below 0.7 also
        fits the data rather than stretching up to 1.0."""
        from web.operator_ui.result_view_helpers import nav_y_range

        frame = pd.DataFrame({"strategy_nav": [0.40, 0.45, 0.50]})

        y_range = nav_y_range(frame)

        self.assertIsNotNone(y_range)
        assert y_range is not None
        # Upper bound hugs the data (~0.5), NOT stretched up to 1.0.
        self.assertLess(y_range[1], 0.8)
        self.assertLessEqual(y_range[0], 0.40)

    def test_nav_y_range_none_for_empty_or_nonnumeric(self) -> None:
        from web.operator_ui.result_view_helpers import nav_y_range

        self.assertIsNone(nav_y_range(pd.DataFrame()))
        self.assertIsNone(nav_y_range(pd.DataFrame({"other": [1, 2]})))

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
