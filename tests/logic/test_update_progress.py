"""fetch 进度行的解析与展示位（运行中心页「走到哪了」）。

信息本来就在共享日志里，这层只把最后一条抬出来。测试盯两件事：解析要对
**真实**日志行成立，展示要**只**发生在 running 分支 —— 否则显示的是上一次
运行的残留，把它当成当前进度就是撒谎。
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.operator_ui.update_progress import (  # noqa: E402
    FetchProgress,
    last_fetch_progress,
    progress_for_run,
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


class RunAttributionTests(unittest.TestCase):
    """进度必须能**归属到本次运行**，否则不显示（codex #450 r1）。

    日志是追加的，每行只带 HH:MM:SS、不带日期，而且计划任务启动的运行**不写**
    任何起始横幅（`[run_center]` 标记只有 UI 启动才写）。所以「取最后一条」
    不等于「本次的」—— 一次刚起步、还没打出第一条进度行的运行，尾部那条属于
    上一次。
    """

    _OLD = ("21:02:58 [f] INFO —   daily year=2026 progress: 2400/5883 "
            "tickers (written=3263, skipped=46201)")
    _STARTED = datetime(2026, 8, 18, 20, 43, 3, tzinfo=_TZ)

    def test_log_untouched_since_start_yields_nothing(self) -> None:
        # 本次运行开始后日志一个字都没写 → 尾部那条必然是上一次的。
        stale_mtime = datetime(2026, 8, 17, 21, 2, 58, tzinfo=_TZ)
        self.assertIsNone(progress_for_run(
            self._OLD, log_mtime=stale_mtime,
            started_at=self._STARTED.isoformat(),
        ))

    def test_old_progress_followed_by_a_new_run_boundary(self) -> None:
        # codex 点名要的回归：旧进度行 + 新运行的行（挂钟回退）→ 不得采信。
        text = self._OLD + chr(10) + "20:44:10 [d] INFO — Stage 0 repair: none"
        mtime = datetime(2026, 8, 18, 20, 44, 10, tzinfo=_TZ)
        self.assertIsNone(progress_for_run(
            text, log_mtime=mtime, started_at=self._STARTED.isoformat(),
        ))
        # 同一判据在纯解析层也成立（不依赖 mtime）。
        self.assertIsNone(last_fetch_progress(text))

    def test_this_runs_own_progress_is_returned(self) -> None:
        text = (self._OLD + chr(10)
                + "20:44:10 [d] INFO — Stage 0 repair: none" + chr(10)
                + "20:55:00 [f] INFO —   daily year=2026 progress: 600/5883 "
                  "tickers (written=120, skipped=3)")
        mtime = datetime(2026, 8, 18, 20, 55, 0, tzinfo=_TZ)
        got = progress_for_run(
            text, log_mtime=mtime, started_at=self._STARTED.isoformat(),
        )
        assert got is not None
        self.assertEqual((got.done, got.total, got.at), (600, 5883, "20:55:00"))

    def test_unattributable_inputs_yield_nothing_not_a_guess(self) -> None:
        mtime = datetime(2026, 8, 18, 20, 55, 0, tzinfo=_TZ)
        cases = {
            "started_at 不可解析": dict(log_mtime=mtime, started_at="不是时间"),
            "started_at 为空": dict(log_mtime=mtime, started_at=""),
            "mtime 取不到": dict(log_mtime=None,
                                started_at=self._STARTED.isoformat()),
            "tz 一有一无": dict(
                log_mtime=datetime(2026, 8, 18, 20, 55),
                started_at=self._STARTED.isoformat()),
        }
        for label, kwargs in cases.items():
            with self.subTest(case=label):
                self.assertIsNone(progress_for_run(self._OLD, **kwargs))


class PagePlacementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.src = _PAGE.read_text(encoding="utf-8")

    def test_progress_is_rendered_only_inside_the_running_branch(self) -> None:
        # 非 running 时最后一条进度行是**上一次**运行的残留。放在 running
        # 分支之外，就是把旧进度当成当前进度。
        running_at = self.src.index('elif _status.kind == "running":')
        progress_at = self.src.index("_progress = _read_progress()")
        next_branch = self.src.index("elif _status.ok:")
        self.assertLess(running_at, progress_at)
        self.assertLess(progress_at, next_branch)

    def test_absent_progress_says_so_rather_than_rendering_nothing(self) -> None:
        self.assertIn("还没有**本次运行**的 fetch 进度行", self.src)
        # 缺失时必须说清「不拿旧的顶」，否则读者会以为只是还没开始。
        self.assertIn("归属不了就不显示", self.src)

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
