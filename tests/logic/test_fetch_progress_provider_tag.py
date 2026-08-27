"""进度行的 provider 标记：写侧盖章、读侧据此归属。

#465 建立的归属判据是「窗口完整 + 边界独占」。它在生产上几乎总答「不知道」：
真实日志按尾部读（窗口天然截断），而兄弟 bundle 共用同一份 daily_update.log、
单飞锁又是 per-provider 的，所以别人的边界随时可能落在窗口里。
`update_progress._current_segment` 的 docstring 当时就写下了出路——给进度行
本身打 provider 标记。这个 change 就是那件事。

测试盯三件：
1. **写侧与读侧用同一个身份**（分头规范化 = 两份会漂的推导）；
2. **老日志照常解析**，且不带标记的行**不被当成任何人的**；
3. 标记路径真的放松了判据（截断窗口 + 别人的边界穿插，仍能确定归属），
   而放松没有放过一条本该拒绝的。
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.tushare.fetcher import (  # noqa: E402
    TushareFetcher,
    TushareFetcherConfig,
)
from src.data_pipeline.daily_update import (  # noqa: E402
    DailyUpdateConfig,
    build_plan,
    run_boundary_line,
)
from web.operator_ui.update_progress import (  # noqa: E402
    last_fetch_progress,
    last_fetch_progress_for_run,
)

_TZ = timezone(timedelta(hours=8))
_BOUNDARY_PREFIX = "[src.data_pipeline.daily_update] INFO — "
_FETCH_PREFIX = "[src.data.tushare.fetcher] INFO — "


def _key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _boundary(clock: str, at: datetime, provider: Path) -> str:
    return (
        f"{clock} {_BOUNDARY_PREFIX}{run_boundary_line(at, provider)}\n")


def _progress(
    clock: str, done: int, *, provider: str = "", endpoint: str = "daily",
) -> str:
    tag = f" provider={provider}" if provider else ""
    return (
        f"{clock} {_FETCH_PREFIX}  {endpoint} year=2026 progress: "
        f"{done}/5883 tickers (written={done}, skipped=0){tag}\n"
    )


class WriterStampTests(unittest.TestCase):
    """写侧盖的章，形态与缺省。"""

    def _fetcher(self, tag: str) -> TushareFetcher:
        # 构造真 fetcher 需要一个 client（会读环境里的 token）。这里测的是
        # 一个只读 config 的纯格式化方法，绕开客户端而不是给生产代码开一个
        # 只为测试存在的入口。
        fetcher = TushareFetcher.__new__(TushareFetcher)
        fetcher._config = TushareFetcherConfig(
            output_dir=Path("out"), provider_tag=tag)
        return fetcher

    def test_a_configured_tag_becomes_a_trailing_suffix(self) -> None:
        fetcher = self._fetcher("d:\\qlib_data\\my_cn_data_pit")

        self.assertEqual(
            fetcher._progress_provider_suffix(),
            " provider=d:\\qlib_data\\my_cn_data_pit",
        )

    def test_no_tag_stamps_nothing_at_all(self) -> None:
        # 不是 `provider=` 空值:读侧必须能分辨「这次没报身份」与「报了一个
        # 空身份」——前者退回边界归属,后者会诱使读侧把空串当成可比对的身份。
        self.assertEqual(self._fetcher("")._progress_provider_suffix(), "")

    def test_a_tag_with_a_newline_is_refused(self) -> None:
        # 带换行的标记会被行式日志切成两半:前半截的身份被截断成一个**不同**
        # 却完全合法的身份串。宁可退回边界归属,也不产出能被误读的行。
        self.assertEqual(
            self._fetcher("d:\\a\nprovider=d:\\b")._progress_provider_suffix(),
            "",
        )
        self.assertEqual(
            self._fetcher("d:\\a\rx")._progress_provider_suffix(), "")


class WriterWiringTests(unittest.TestCase):
    """盖章函数本身对，不代表进度行**用了**它。

    变异实测：把格式串参数里的 ``self._progress_provider_suffix()`` 换成
    ``""``，只测那个函数的用例照样全绿——进度行从此不带身份，而读侧只会
    安静地退回「归属报不出来」，看起来像环境问题而不是回归。
    """

    def test_the_progress_line_stamps_through_the_suffix_helper(self) -> None:
        source = (
            PROJECT_ROOT / "src" / "data" / "tushare" / "fetcher.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            '                        "  %s year=%d progress: %d/%d tickers "\n'
            '                        "(written=%d, skipped=%d)%s",\n',
            source,
        )
        self.assertIn(
            "                        self._progress_provider_suffix(),\n",
            source,
        )

    def test_the_cli_forwards_the_flag_into_the_config(self) -> None:
        # 标志解析对了、没接进 config 的话，每一条进度行都不带身份，而
        # 「归属报不出来」正是这个 change 要修的那个症状。
        source = (
            PROJECT_ROOT / "scripts" / "data_pipeline" / "01_fetch_tushare.py"
        ).read_text(encoding="utf-8")

        self.assertIn('"--provider-tag", default=""', source)
        self.assertIn("            provider_tag=args.provider_tag,\n", source)


class WriterReaderShareOneIdentityTests(unittest.TestCase):
    """编排器传给 fetch 的标记 == 它写进边界行的身份。

    分头规范化就是两份会漂的推导，而读侧对身份的校验是**完整回环**——差一个
    字节就退化成「别人的行」，看起来只是「归属报不出来」，不像个 bug。
    """

    def _plan_tag(self, provider: Path) -> str:
        config = DailyUpdateConfig(
            provider_dir=provider,
            tushare_dir=provider.parent / "tushare_raw",
            delisted_registry=provider.parent / "delisted.csv",
            reference_cases=provider.parent / "cases.csv",
        )
        plan = build_plan(config)
        argv = plan.fetch
        return argv[argv.index("--provider-tag") + 1]

    def test_the_fetch_tag_matches_the_boundary_identity(self) -> None:
        provider = PROJECT_ROOT / "output" / "a_bundle"
        at = datetime(2026, 8, 27, 20, 30, tzinfo=_TZ)

        tag = self._plan_tag(provider)
        boundary = run_boundary_line(at, provider)

        self.assertIn(f"provider={tag}", boundary)
        self.assertEqual(tag, _key(provider))

    def test_the_tag_is_a_full_round_trip_of_itself(self) -> None:
        # 读侧要求 `normcase(resolve(tag)) == tag`。写侧产不出这种形态的话,
        # 每一条真实标记都验不过,而症状只是「归属还是报不出来」。
        tag = self._plan_tag(PROJECT_ROOT / "output" / "b_bundle")

        self.assertEqual(os.path.normcase(str(Path(tag).resolve())), tag)


class BackwardCompatibleParseTests(unittest.TestCase):
    """老日志（标记落地之前写的）必须照常解析。"""

    def test_an_unstamped_line_still_parses(self) -> None:
        got = last_fetch_progress(_progress("20:50:30", 400))

        assert got is not None
        self.assertEqual(got.done, 400)
        self.assertEqual(got.provider, "")

    def test_a_stamped_line_carries_its_provider(self) -> None:
        got = last_fetch_progress(
            _progress("20:50:30", 400, provider="d:\\data\\a"))

        assert got is not None
        self.assertEqual(got.done, 400)
        self.assertEqual(got.provider, "d:\\data\\a")

    def test_an_endpoint_containing_the_marker_is_not_read_as_a_tag(
        self,
    ) -> None:
        # 标记锚行尾。不锚的话,一个恰好含 `provider=` 的名字会被读走。
        got = last_fetch_progress(
            _progress("20:50:30", 400, endpoint="provider=x"))

        assert got is not None
        self.assertEqual(got.provider, "")

    def test_filtering_by_provider_skips_unstamped_lines(self) -> None:
        # 不带标记的行**不属于任何人**。把它当成自己的,就是把标记落地之前
        # 那个被证伪的猜测又请了回来。
        log = (
            _progress("20:50:30", 400, provider="d:\\data\\a")
            + _progress("20:51:30", 900)
        )

        got = last_fetch_progress(log, provider_key="d:\\data\\a")

        assert got is not None
        self.assertEqual(got.done, 400)

    def test_filtering_by_provider_skips_other_providers(self) -> None:
        log = (
            _progress("20:50:30", 400, provider="d:\\data\\a")
            + _progress("20:51:30", 900, provider="d:\\data\\b")
        )

        got = last_fetch_progress(log, provider_key="d:\\data\\a")

        assert got is not None
        self.assertEqual(got.done, 400)


class TaggedAttributionTests(unittest.TestCase):
    """标记路径：判据放松到「我们最后一条边界 + 按标记过滤」。"""

    def setUp(self) -> None:
        self.mine = PROJECT_ROOT / "output" / "mine_bundle"
        self.theirs = PROJECT_ROOT / "output" / "their_bundle"
        self.mine_key = _key(self.mine)
        self.theirs_key = _key(self.theirs)
        self.at = datetime(2026, 8, 27, 20, 0, tzinfo=_TZ)

    def test_a_truncated_window_still_attributes_when_lines_are_stamped(
        self,
    ) -> None:
        # 这是标记的**全部价值**:真实日志按尾部读,窗口天然截断,而独占判据
        # 只在「我看到了全部」时成立——于是生产上几乎总答「不知道」。
        log = (
            _boundary("20:00:00", self.at, self.mine)
            + _progress("20:50:30", 400, provider=self.mine_key)
        )

        got = last_fetch_progress_for_run(
            log, provider_dir=self.mine, window_complete=False)

        self.assertTrue(got.attributed)
        assert got.progress is not None
        self.assertEqual(got.progress.done, 400)
        self.assertEqual(got.unattributed_reason, "")

    def test_an_interleaved_foreign_boundary_no_longer_blocks_attribution(
        self,
    ) -> None:
        # 独占判据在这里会答 foreign_boundary。行自己带身份之后,别人的边界
        # 穿插无所谓——过滤得掉。
        log = (
            _boundary("20:00:00", self.at, self.mine)
            + _progress("20:50:30", 400, provider=self.mine_key)
            + _boundary(
                "20:55:00",
                datetime(2026, 8, 27, 20, 55, tzinfo=_TZ), self.theirs)
            + _progress("20:56:00", 1200, provider=self.theirs_key)
            + _progress("20:57:00", 800, provider=self.mine_key)
        )

        got = last_fetch_progress_for_run(
            log, provider_dir=self.mine, window_complete=True)

        self.assertTrue(got.attributed)
        assert got.progress is not None
        self.assertEqual(got.progress.done, 800)

    def test_only_lines_after_our_own_boundary_count(self) -> None:
        # 标记解决「哪个 provider」,不解决「哪一次运行」——历次运行的行都躺
        # 在同一份日志里。所以仍要从**我们自己最后一条边界**之后取。
        log = (
            _boundary("19:00:00",
                      datetime(2026, 8, 27, 19, 0, tzinfo=_TZ), self.mine)
            + _progress("19:30:00", 5000, provider=self.mine_key)
            + _boundary("20:00:00", self.at, self.mine)
            + _progress("20:10:00", 200, provider=self.mine_key)
        )

        got = last_fetch_progress_for_run(
            log, provider_dir=self.mine, window_complete=True)

        self.assertTrue(got.attributed)
        assert got.progress is not None
        self.assertEqual(got.progress.done, 200)

    def test_our_boundary_with_no_stamped_line_falls_back(self) -> None:
        # 我们的边界在,但这一段的进度行不带标记(我们这侧还没升级/手工跑的
        # fetch)。标记路径不成立,退回边界独占——而窗口截断时它答「不知道」,
        # 也就是标记落地之前的行为,没有退步。
        log = (
            _boundary("20:00:00", self.at, self.mine)
            + _progress("20:50:30", 400)
        )

        got = last_fetch_progress_for_run(
            log, provider_dir=self.mine, window_complete=False)

        self.assertFalse(got.attributed)
        self.assertEqual(got.unattributed_reason, "window_truncated")
        # 进度本身仍如实呈出(全窗口取最后一条),只是不声称归属。
        assert got.progress is not None
        self.assertEqual(got.progress.done, 400)

    def test_a_foreign_stamp_alone_never_attributes_to_us(self) -> None:
        # 别人在报身份不代表我们在报。
        log = (
            _boundary("20:00:00", self.at, self.mine)
            + _progress("20:50:30", 400, provider=self.theirs_key)
        )

        got = last_fetch_progress_for_run(
            log, provider_dir=self.mine, window_complete=False)

        self.assertFalse(got.attributed)

    def test_no_boundary_of_ours_is_not_attributed_even_when_stamped(
        self,
    ) -> None:
        # 标记路径的定位器仍是**我们自己的**边界。没有它,就不知道这条属于
        # 哪一次运行。
        log = _progress("20:50:30", 400, provider=self.mine_key)

        got = last_fetch_progress_for_run(
            log, provider_dir=self.mine, window_complete=True)

        self.assertFalse(got.attributed)
        self.assertEqual(got.unattributed_reason, "no_boundary")

    def test_a_corrupt_boundary_is_reported_as_corrupt_not_truncated(
        self,
    ) -> None:
        # 损坏的边界就是损坏的边界。报成「窗口截断」会让操作人去调大读取
        # 窗口,而问题在别处。
        log = (
            f"20:00:00 {_BOUNDARY_PREFIX}[daily_update] run started "
            f"NOT-A-TIMESTAMP provider={self.mine_key}\n"
            + _progress("20:50:30", 400, provider=self.mine_key)
        )

        got = last_fetch_progress_for_run(
            log, provider_dir=self.mine, window_complete=False)

        self.assertFalse(got.attributed)
        self.assertEqual(got.unattributed_reason, "corrupt_boundary")

    def test_a_tag_that_is_not_its_own_canonical_form_is_refused(self) -> None:
        # 写侧只产 `normcase(resolve())` 的精确形态。宽容化(strip / 大小写
        # 不敏感 / 只比 basename)会让写侧产不出的拼写被洗成我们的身份,然后
        # 以「已确定」的口气归属一条别人的进度。这里逐种试:每一种都是某个
        # 「顺手宽容一下」的改动会放过的形态(变异实测:`.strip().lower()`
        # 只被尾部空格与大小写差异咬住,尾随分隔符咬不住)。
        for spelling in (
            self.mine_key + os.sep,
            self.mine_key + " ",
            " " + self.mine_key,
            self.mine_key.upper(),
            Path(self.mine_key).name,
        ):
            with self.subTest(provider=spelling):
                if spelling == self.mine_key:
                    continue  # 大小写在本机已被 normcase 折平时跳过
                log = (
                    _boundary("20:00:00", self.at, self.mine)
                    + _progress("20:50:30", 400, provider=spelling)
                )

                got = last_fetch_progress_for_run(
                    log, provider_dir=self.mine, window_complete=False)

                self.assertFalse(got.attributed)

    def test_a_foreign_boundary_arriving_last_does_not_capture_our_lines(
        self,
    ) -> None:
        # 标记路径的定位器必须是**我们自己的**边界。把它放松成「最后一条
        # 边界」的话,别人后起跑时会把我们**更早**的进度圈进它那一段:定位
        # 点跑到别人的边界之后,我们那条行落在它之前,于是取不到——页面从
        # 「400/5883」变成「无法归属」,而运行好好的(变异实测能逃逸)。
        log = (
            _boundary("20:00:00", self.at, self.mine)
            + _progress("20:50:30", 400, provider=self.mine_key)
            + _boundary(
                "20:55:00",
                datetime(2026, 8, 27, 20, 55, tzinfo=_TZ), self.theirs)
            + _progress("20:56:00", 1200, provider=self.theirs_key)
        )

        got = last_fetch_progress_for_run(
            log, provider_dir=self.mine, window_complete=True)

        self.assertTrue(got.attributed)
        assert got.progress is not None
        self.assertEqual(got.progress.done, 400)

    def test_the_unstamped_path_still_refuses_a_foreign_boundary(self) -> None:
        # 独占判据(标记落地之前的那条)不能因为标记路径存在就退化。窗口里
        # 有别人的边界、而我们的行不带标记时,那次运行有没有结束这份日志答
        # 不了——如实说不知道。
        log = (
            _boundary("20:00:00", self.at, self.mine)
            + _boundary(
                "20:05:00",
                datetime(2026, 8, 27, 20, 5, tzinfo=_TZ), self.theirs)
            + _progress("20:50:30", 400)
        )

        got = last_fetch_progress_for_run(
            log, provider_dir=self.mine, window_complete=True)

        self.assertFalse(got.attributed)
        self.assertEqual(got.unattributed_reason, "foreign_boundary")


if __name__ == "__main__":
    unittest.main()
