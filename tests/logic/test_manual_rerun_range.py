"""手动补跑的抓取范围与交易日历闸预警。

（openspec 2026-08-22-manual-rerun-range）

两个真实场景逼出这个改动：

* 2026-08-17 / 08-20 / 08-21 连续三晚失败，因为一次收盘前的更宽范围抓取把
  fetch manifest 撑到 20151001 并记下未解决的洞，此后每次按缺省下限
  ``--start-date 20180101`` 跑都被 manifest 的范围守卫拒绝。01 给出的修法是
  「按完整范围重跑」，而运行中心**做不到**——范围写死。操作人只能去命令行。
* 周末补跑会撞上交易日历闸：它 no-op 并 **exit 0**，状态工件记成一次成功，
  而实际什么都没做。旁路是传 ``--end-date``，但页面同样传不了。

本模块最要紧的两条是**穷尽等价**守卫：`update_runner` 与编排器的唯一耦合是
CLI 进程边界（它不许 import ``src.*``），所以日历闸的两个判据只能在 UI 侧
**重述**。重述必然有漂移风险——这个仓库刚为「同一件事写两处」付过学费——所以
这里导入真判据，把输入域穷尽比一遍。
"""

from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import src.data_pipeline.daily_update as orchestrator  # noqa: E402
import web.operator_ui.update_runner as runner  # noqa: E402
from web.operator_ui.update_runner import (  # noqa: E402
    START_DATE,
    TOKEN_ENV_VAR,
    build_update_argv,
    calendar_gate_warning,
    date_input_problem,
    launch_daily_update,
    range_problem,
)

_PAGE = PROJECT_ROOT / "web" / "operator_ui" / "pages" / "run_center.py"
_ENV_OK = {TOKEN_ENV_VAR: "test-token"}

# 2026-08-22 是周六 —— 三晚故障那一周的周末，也就是补跑真正会发生的那天。
SATURDAY = date(2026, 8, 22)
SUNDAY = date(2026, 8, 23)
MONDAY = date(2026, 8, 24)


def _bundle(root: Path, *, calendars: bool = True, instruments: bool = True,
            features: bool = True) -> Path:
    provider = root / "my_cn_data_pit"
    if calendars:
        (provider / "calendars").mkdir(parents=True, exist_ok=True)
        (provider / "calendars" / "day.txt").write_text("20260821", encoding="utf-8")
    if instruments:
        (provider / "instruments").mkdir(parents=True, exist_ok=True)
        (provider / "instruments" / "all.txt").write_text("", encoding="utf-8")
    if features:
        (provider / "features").mkdir(parents=True, exist_ok=True)
    provider.mkdir(parents=True, exist_ok=True)
    return provider


# ---------------------------------------------------------------- 重述不许漂移

class TheRestatedGatePredicatesMatchTheOrchestrator(unittest.TestCase):
    """UI 侧的重述必须与编排器**逐点**一致。

    不是「抽几个用例看看」：输入域小到可以穷尽，那就穷尽。日期取满一整年
    （含闰年 2 月），bundle 骨架取三个路径在/不在的全部 2³ 组合。
    """

    def test_the_non_trading_day_predicate_agrees_for_a_whole_year(self) -> None:
        day = date(2028, 1, 1)          # 闰年，覆盖 2/29
        checked = 0
        while day < date(2029, 1, 1):
            if runner._is_non_trading_day(day) != orchestrator._run_date_is_non_trading(day):
                self.fail(f"{day} 上两边判定不一致 —— 重述漂移了")
            checked += 1
            day += timedelta(days=1)
        self.assertEqual(366, checked, "没走满一年 —— 本守卫已失效")

    def test_the_live_bundle_predicate_agrees_on_every_skeleton(self) -> None:
        checked = 0
        for calendars in (True, False):
            for instruments in (True, False):
                for features in (True, False):
                    with self.subTest(cal=calendars, inst=instruments, feat=features), \
                            tempfile.TemporaryDirectory() as tmp:
                        provider = _bundle(
                            Path(tmp), calendars=calendars,
                            instruments=instruments, features=features)
                        self.assertEqual(
                            orchestrator._live_bundle_present(provider),
                            runner._live_bundle_present(provider),
                            "重述漂移了",
                        )
                        checked += 1
        self.assertEqual(8, checked, "没走满 2³ —— 本守卫已失效")

    def test_the_ui_does_not_improve_on_the_gate(self) -> None:
        """UI 不许判得比闸更宽——比如「顺手」加上节假日。

        闸刻意只管周末（工作日节假日走正常流程，由 fetch 的新鲜度闸优雅
        no-op）。UI 要预警的是闸**会不会** no-op，不是「今天是不是节假日」；
        判得更宽就是在预警一件不会发生的事。
        """
        # 2026-10-01 国庆，周四：是节假日，但**不是**闸眼里的非交易日。
        national_day = date(2026, 10, 1)
        self.assertEqual(3, national_day.weekday())
        self.assertFalse(runner._is_non_trading_day(national_day))
        self.assertFalse(orchestrator._run_date_is_non_trading(national_day))


# ---------------------------------------------------------------- argv

class TheDefaultArgvStillMirrorsTheScheduler(unittest.TestCase):
    """「手动通道镜像调度器」这条不变式,缺省路径上原样成立。

    它是 codex 在 #440 时代加固过的（那条 FULL-LIST 相等的守卫至今未改一字，
    就在 `tests/logic/test_update_runner.py`）。本改动让 argv **可以**偏离，
    但偏离只能来自操作人的显式输入，而且页面显示的就是产出的 argv 本身——
    所以偏离是看得见的，不是被夹带的。
    """

    P = (Path("/data/prov"), Path("/data/tu"), Path("/data/reg.parquet"))

    def test_no_range_is_byte_identical_to_the_old_call(self) -> None:
        self.assertEqual(
            build_update_argv(*self.P),
            build_update_argv(*self.P, start_date=None, end_date=None))
        self.assertEqual(build_update_argv(*self.P)[-2:], ["--start-date", START_DATE])
        self.assertNotIn("--end-date", build_update_argv(*self.P))

    def test_an_explicit_range_changes_the_range_and_nothing_else(self) -> None:
        base = build_update_argv(*self.P)
        widened = build_update_argv(*self.P, start_date="20151001", end_date="20260821")
        expected = [
            "20151001" if part == START_DATE else part for part in base
        ] + ["--end-date", "20260821"]
        self.assertEqual(expected, widened, "范围之外还夹带了别的参数")

    def test_an_empty_start_falls_back_to_the_scheduler_floor(self) -> None:
        # 操作人清空输入框时,argv 仍必须带 `--start-date`（调度器总是显式传它）。
        self.assertEqual(
            build_update_argv(*self.P), build_update_argv(*self.P, start_date=""))

    def test_an_end_date_alone_keeps_the_default_floor(self) -> None:
        argv = build_update_argv(*self.P, end_date="20260822")
        self.assertEqual(["--end-date", "20260822"], argv[-2:])
        self.assertIn(START_DATE, argv)


# ---------------------------------------------------------------- 日期校验

class AMalformedDateIsRefusedBeforeTheTwoHourRun(unittest.TestCase):
    """编排器与 01 对日期格式**零校验**——畸形值一路流到 tushare 那头才炸。"""

    def test_a_blank_endpoint_is_unspecified_not_invalid(self) -> None:
        self.assertIsNone(date_input_problem("", label="结束日期"))
        self.assertIsNone(date_input_problem("   ", label="结束日期"))
        self.assertIsNone(range_problem("", ""))

    def test_wrong_width_or_non_digits_are_refused(self) -> None:
        for bad in ("2026082", "202608221", "2026-08-22", "20260822 ext", "abcdefgh"):
            with self.subTest(值=bad):
                self.assertIsNotNone(date_input_problem(bad, label="开始日期"))

    def test_eight_digits_that_are_not_a_date_are_refused(self) -> None:
        # 这一类过得了「8 位数字」那一关,只有真的构造日期才拦得住。
        for bad in ("20260231", "20261301", "20260000", "20260230"):
            with self.subTest(值=bad):
                problem = date_input_problem(bad, label="开始日期")
                self.assertIsNotNone(problem)
                self.assertIn("真实日期", str(problem))

    def test_a_leap_day_is_a_real_date(self) -> None:
        self.assertIsNone(date_input_problem("20280229", label="开始日期"))
        self.assertIsNotNone(date_input_problem("20260229", label="开始日期"))

    def test_a_reversed_range_is_refused(self) -> None:
        problem = range_problem("20260821", "20260101")
        self.assertIsNotNone(problem)
        self.assertIn("晚于", str(problem))

    def test_equal_endpoints_are_a_valid_single_day(self) -> None:
        self.assertIsNone(range_problem("20260822", "20260822"))

    def test_the_real_widening_that_fixed_three_nights_is_valid(self) -> None:
        self.assertIsNone(range_problem("20151001", "20260821"))


class TheLauncherRefusesABadRangeWithoutSpawning(unittest.TestCase):
    """页面已经拦过一次,但被审计的边界是启动器,它可以被页面之外的调用方使用。"""

    def _launch(self, **kw: object) -> object:
        with tempfile.TemporaryDirectory() as tmp:
            provider = _bundle(Path(tmp))
            with mock.patch("subprocess.Popen") as popen:
                result = launch_daily_update(
                    provider, Path(tmp) / "tushare_raw", Path(tmp) / "reg.parquet",
                    env=_ENV_OK, **kw)  # type: ignore[arg-type]
            return result, popen

    def test_a_malformed_start_never_reaches_popen(self) -> None:
        result, popen = self._launch(start_date="2026-08-22")  # type: ignore[misc]
        self.assertEqual("bad_range", result.kind)  # type: ignore[attr-defined]
        popen.assert_not_called()

    def test_a_reversed_range_never_reaches_popen(self) -> None:
        result, popen = self._launch(  # type: ignore[misc]
            start_date="20260821", end_date="20260101")
        self.assertEqual("bad_range", result.kind)  # type: ignore[attr-defined]
        popen.assert_not_called()

    def test_a_valid_range_reaches_the_child_argv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = _bundle(Path(tmp))
            with mock.patch("subprocess.Popen") as popen:
                popen.return_value = SimpleNamespace(pid=4321)
                result = launch_daily_update(
                    provider, Path(tmp) / "tushare_raw", Path(tmp) / "reg.parquet",
                    env=_ENV_OK, start_date="20151001", end_date="20260821")
            self.assertEqual("launched", result.kind)
            argv = popen.call_args[0][0]
        self.assertIn("20151001", argv)
        self.assertEqual(["--end-date", "20260821"], argv[-2:])


# ---------------------------------------------------------------- 日历闸预警

class TheWarningFiresExactlyWhenTheGateWouldNoOp(unittest.TestCase):
    """no-op 是三个条件的**合取**,只复现头一个就会说错话。"""

    def test_a_weekend_launch_without_an_end_date_is_warned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            warning = calendar_gate_warning(_bundle(Path(tmp)), today=SATURDAY)
        self.assertIsNotNone(warning)
        self.assertIn("exit 0", str(warning))
        self.assertIn("成功", str(warning), "没说清工件会记成一次成功")
        self.assertIn("结束日期", str(warning), "没给出旁路")

    def test_sunday_too(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNotNone(calendar_gate_warning(_bundle(Path(tmp)), today=SUNDAY))

    def test_an_end_date_bypasses_the_gate_so_no_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(calendar_gate_warning(
                _bundle(Path(tmp)), today=SATURDAY, end_date="20260822"))

    def test_a_trading_day_is_not_warned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(calendar_gate_warning(_bundle(Path(tmp)), today=MONDAY))

    def test_no_live_bundle_means_the_gate_lets_it_through(self) -> None:
        """没有可用 bundle 时闸**放行**,跑完整管线去 bootstrap。

        只看周末就会在这里说错话——预警一件不会发生的事。
        """
        with tempfile.TemporaryDirectory() as tmp:
            partial = _bundle(Path(tmp), features=False)   # 部分拷贝
            self.assertIsNone(calendar_gate_warning(partial, today=SATURDAY))

    def test_the_warning_does_not_claim_the_run_is_blocked(self) -> None:
        # 这是预警不是拦截:no-op 无害,操作人可能就是要它。
        with tempfile.TemporaryDirectory() as tmp:
            warning = str(calendar_gate_warning(_bundle(Path(tmp)), today=SATURDAY))
        for forbidden in ("已拒绝", "无法启动", "禁止"):
            self.assertNotIn(forbidden, warning)


# ---------------------------------------------------------------- 页面接线

class ThePageShowsWhatItWillActuallyRun(unittest.TestCase):
    _TREE = ast.parse(_PAGE.read_text(encoding="utf-8"))
    _SOURCE = _PAGE.read_text(encoding="utf-8")

    def _calls(self, name: str) -> list[ast.Call]:
        return [
            node for node in ast.walk(self._TREE)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == name
        ]

    def test_the_preview_is_derived_from_the_real_argv(self) -> None:
        """预览必须是 `build_update_argv` 产出的那个 argv 本身。

        改动前页面手抄了一份 flag 列表——与真正执行的参数是两份手写的同义
        内容,分头漂移时「显示的」与「要跑的」就不是一回事,而这个位置恰恰是
        「显示的必须就是要跑的」。
        """
        self.assertEqual(1, len(self._calls("build_update_argv")),
                         "页面应恰好从真 argv 派生一次预览")
        self.assertNotIn(
            "`--provider-dir {", self._SOURCE,
            "页面里还留着手抄的 argv 预览 —— 它会和真参数分头漂移")

    def test_the_page_hands_the_range_to_the_launcher(self) -> None:
        calls = self._calls("launch_daily_update")
        self.assertEqual(1, len(calls))
        passed = {kw.arg for kw in calls[0].keywords if kw.arg}
        self.assertLessEqual({"start_date", "end_date"}, passed,
                             "操作人填的范围没传给启动器")

    def test_the_preview_and_the_launch_read_the_same_inputs(self) -> None:
        # 预览用一组值、启动用另一组,是这类页面最容易出的错:看着对,跑的不是它。
        def range_kwargs(name: str) -> dict[str, str]:
            call = self._calls(name)[0]
            return {
                kw.arg: ast.unparse(kw.value)
                for kw in call.keywords if kw.arg in ("start_date", "end_date")
            }
        self.assertEqual(range_kwargs("build_update_argv"),
                         range_kwargs("launch_daily_update"))

    def test_the_button_is_disabled_on_an_invalid_range(self) -> None:
        launch_button = [
            node for node in ast.walk(self._TREE)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == "button"
            and any("启动数据更新" in ast.unparse(a) for a in node.args)
        ]
        self.assertEqual(1, len(launch_button), "找不到启动按钮 —— 本守卫已失效")
        disabled = [kw for kw in launch_button[0].keywords if kw.arg == "disabled"]
        self.assertEqual(1, len(disabled))
        self.assertIn("_range_error", ast.unparse(disabled[0].value))

    def test_the_gate_warning_does_not_disable_the_button(self) -> None:
        # no-op 无害:预警要显示,但不该替操作人做决定。
        launch_button = [
            node for node in ast.walk(self._TREE)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == "button"
            and any("启动数据更新" in ast.unparse(a) for a in node.args)
        ][0]
        disabled = [kw for kw in launch_button.keywords if kw.arg == "disabled"][0]
        self.assertNotIn("_gate_warning", ast.unparse(disabled.value))

    def test_the_page_computes_no_calendar_logic_of_its_own(self) -> None:
        for forbidden in ("weekday()", "isoweekday(", "calendars"):
            with self.subTest(禁用=forbidden):
                self.assertNotIn(forbidden, self._SOURCE)


if __name__ == "__main__":
    unittest.main()
