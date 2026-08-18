"""fetch 进度行的解析与展示位（运行中心页「走到哪了」）。

信息本来就在共享日志里，这层只把最后一条抬出来。测试盯两件事：解析要对
**真实**日志行成立，展示要**只**发生在 running 分支 —— 否则显示的是上一次
运行的残留，把它当成当前进度就是撒谎。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.operator_ui.update_progress import (  # noqa: E402
    FetchProgress,
    last_fetch_progress,
)

_PAGE = PROJECT_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"

#: 逐字取自生产日志（D:/qlib_data/logs/daily_update.log，2026-08-17 那次）。
#: 手写一条“像的”样例是这类解析测试最常见的空转来源。
_REAL_TAIL = (
    "20:49:38 [src.data.tushare.fetcher] WARNING —   No-data placeholder "
    "D:\\qlib_data\tushare_raw\\daily\2022\\920982.BJ.parquet unexpectedly holds 94 row(s)\n"
    "20:50:30 [src.data.tushare.fetcher] INFO —   daily year=2024 progress: "
    "400/5883 tickers (written=941, skipped=34757)\n"
    "21:02:58 [src.data.tushare.fetcher] INFO —   daily year=2026 progress: "
    "2400/5883 tickers (written=3263, skipped=46201)\n"
)


class ParseTests(unittest.TestCase):
    def test_parses_a_real_production_line(self) -> None:
        got = last_fetch_progress(_REAL_TAIL)
        self.assertEqual(
            got,
            FetchProgress(
                endpoint="daily", year=2026, done=2400, total=5883,
                written=3263, skipped=46201,
            ),
        )

    def test_takes_the_last_line_not_the_first(self) -> None:
        # 日志是追加的，前面躺着历次运行的进度行。
        got = last_fetch_progress(_REAL_TAIL)
        assert got is not None
        self.assertEqual((got.year, got.done), (2026, 2400))

    def test_no_progress_line_is_none_not_a_guess(self) -> None:
        for text in ("", "   ", "20:49:35 WARNING — No-data placeholder x.parquet"):
            with self.subTest(text=text[:16]):
                self.assertIsNone(last_fetch_progress(text))

    def test_zero_total_is_dropped(self) -> None:
        # 0/0 说不出任何进度，渲染出来只会误导。
        line = ("21:00:00 INFO —   daily year=2026 progress: 0/0 tickers "
                "(written=0, skipped=0)")
        self.assertIsNone(last_fetch_progress(line))

    def test_description_states_its_own_scope(self) -> None:
        # 分母是「该端点该年」的票数，不是整轮进度 —— 描述必须自带范围，
        # 否则读的人会当成整体百分比。
        got = last_fetch_progress(_REAL_TAIL)
        assert got is not None
        text = got.describe()
        for token in ("daily", "2026", "2400/5883"):
            with self.subTest(token=token):
                self.assertIn(token, text)


class PagePlacementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.src = _PAGE.read_text(encoding="utf-8")

    def test_progress_is_rendered_only_inside_the_running_branch(self) -> None:
        # 非 running 时最后一条进度行是**上一次**运行的残留。放在 running
        # 分支之外，就是把旧进度当成当前进度。
        running_at = self.src.index('elif _status.kind == "running":')
        progress_at = self.src.index("_progress = last_fetch_progress(")
        next_branch = self.src.index("elif _status.ok:")
        self.assertLess(running_at, progress_at)
        self.assertLess(progress_at, next_branch)

    def test_absent_progress_says_so_rather_than_rendering_nothing(self) -> None:
        self.assertIn("还没有 fetch 进度行", self.src)

    def test_the_caption_does_not_claim_overall_progress(self) -> None:
        # 明确否认「整轮进度」这层含义；也不得渲染成进度条。
        self.assertIn("不是整轮进度", self.src)
        for forbidden in ("st.progress", "progress_bar"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.src)


if __name__ == "__main__":
    unittest.main()
