"""fetch 进度行的解析与展示位（运行中心页「走到哪了」）。

信息本来就在共享日志里，这层只把最后一条抬出来。测试盯两件事：解析要对
**真实**日志行成立，展示要**只**发生在 running 分支 —— 否则显示的是上一次
运行的残留，把它当成当前进度就是撒谎。
"""

from __future__ import annotations

import sys
import unittest
from datetime import timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.operator_ui.update_progress import (  # noqa: E402
    FetchProgress,
    last_fetch_progress,
)

_TZ = timezone(timedelta(hours=8))

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
                written=3263, skipped=46201, at="21:02:58",
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


class NoAttributionClaimTests(unittest.TestCase):
    """本层**不声称**这条属于哪一次运行 —— 而且必须说出来（codex #450 r2）。

    日志行只带 HH:MM:SS、不含日期，计划任务启动的运行也不写带日期的起始横幅，
    所以「昨天 21:00」与「今天 21:00」在数据里不可区分。三种启发式都被连着
    证伪过（mtime 门 / 挂钟回退 / 时刻比大小），根因是结构性的。这里钉住的
    是：**不要再往回加推断**，改为如实披露。
    """

    def test_a_later_starting_rerun_is_not_silently_attributed(self) -> None:
        # codex 的场景：旧进度 10:30，新运行 15:00 起，当前非进度行 15:01。
        # 任何「猜归属」的实现都会在这里把旧的当成新的。本层的契约是**只**
        # 返回尾部最后一条，由调用方披露不确定性 —— 所以这里返回的就是那条
        # 旧的，且**页面必须说它不可证明**（见 PagePlacementTests）。
        text = (
            "10:30:00 [f] INFO —   daily year=2026 progress: 2400/5883 "
            "tickers (written=3263, skipped=46201)" + chr(10)
            + "15:01:00 [d] INFO — Stage 0 repair: nothing to do"
        )
        got = last_fetch_progress(text)
        assert got is not None
        self.assertEqual((got.done, got.at), (2400, "10:30:00"))

    def test_the_module_does_not_grow_an_attribution_guess_back(self) -> None:
        # 防回潮：三种被证伪的启发式都不得再出现在这一层。
        src = (
            PROJECT_ROOT / "web" / "operator_ui" / "update_progress.py"
        ).read_text(encoding="utf-8")
        for forbidden in ("def progress_for_run", "log_mtime", "started_at"):
            with self.subTest(forbidden=forbidden):
                # docstring 里复述这段历史是允许的，禁的是**代码**。
                code = src[src.index('"""', src.index('"""') + 3) + 3 :]
                self.assertNotIn(forbidden, code)

    def test_the_timestamp_is_carried_so_the_caller_can_disclose_it(self) -> None:
        got = last_fetch_progress(_REAL_TAIL)
        assert got is not None
        self.assertEqual(got.at, "21:02:58")
        self.assertIn("21:02:58", got.describe())


class PagePlacementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.src = _PAGE.read_text(encoding="utf-8")

    def test_progress_is_rendered_only_inside_the_running_branch(self) -> None:
        # 非 running 时最后一条进度行是**上一次**运行的残留。放在 running
        # 分支之外，就是把旧进度当成当前进度。
        running_at = self.src.index('elif _status.kind == "running":')
        # 锚串随读取器返回类型变化更新（它现在带上归属）。**断言未变**：
        # 进度仍必须只在 running 分支内渲染 —— 非 running 时那条是上一次
        # 运行的残留。旧锚 `_progress = _read_progress()` 现在会先匹配到
        # 后面的 `_baseline_progress = _read_progress()`，定位到分支之外。
        progress_at = self.src.index("_attributed = _read_progress()")
        next_branch = self.src.index("elif _status.ok:")
        self.assertLess(running_at, progress_at)
        self.assertLess(progress_at, next_branch)

    def test_absent_progress_says_so_rather_than_rendering_nothing(self) -> None:
        self.assertIn("日志尾部没有 fetch 进度行", self.src)

    def test_the_caption_admits_it_cannot_prove_attribution(self) -> None:
        # 撤回归属承诺之后，**必须**把不确定性说出来，并同时摆出两个时刻让
        # 操作人自己判断 —— 否则读者会默认它就是本次运行的进度。
        self.assertIn("无法证明这条属于本次运行", self.src)
        self.assertIn("本次运行始于", self.src)
        self.assertIn("_progress.describe()", self.src)

    def test_progress_is_part_of_the_polling_rerun_condition(self) -> None:
        # codex #450 r1: 片段计时只重跑片段，而追加的 fetch 行**不改变**状态
        # 签名（kind/started_at/分类都不动）。只比签名的话，页面会一直冻在
        # 「还没有进度行」直到运行结束。
        self.assertIn("_baseline_progress = _read_progress()", self.src)
        fragment_at = self.src.index("def _watch_update_completion()")
        body = self.src[fragment_at : fragment_at + 1400]
        self.assertIn("_read_progress() != _baseline_progress", body)
        # 基线必须在片段注册**之前**定格 —— 两侧都在片段里重算是 #442 r2
        # 已经证伪过的错法。
        baseline_at = self.src.index("_baseline_progress = _read_progress()")
        self.assertLess(baseline_at, fragment_at)

    def test_one_progress_reader_shared_by_page_and_fragment(self) -> None:
        # 两处各写一份正是它们会分叉的方式（这一页上已经栽过三次）。
        self.assertEqual(self.src.count("def _read_progress()"), 1)
        self.assertGreaterEqual(self.src.count("_read_progress()"), 3)

    def test_the_caption_does_not_claim_overall_progress(self) -> None:
        # 明确否认「整轮进度」这层含义；也不得渲染成进度条。
        self.assertIn("不是整轮进度", self.src)
        for forbidden in ("st.progress", "progress_bar"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.src)


if __name__ == "__main__":
    unittest.main()
