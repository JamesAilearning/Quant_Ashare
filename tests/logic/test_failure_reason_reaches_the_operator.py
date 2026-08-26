"""失败原因必须一路走到操作人眼前，而不是停在返回值里。

（openspec 2026-08-22-stage-failure-reason）

生产者那一半由 ``tests/data_pipeline/test_daily_update_failure_reason.py`` 守；
这里守读侧：``detail`` 已经带着阶段自己那句 ERROR 了，工作台却把它整个丢掉，
只渲染「退出码含义 + 失败阶段」——两处都是。于是三晚故障里操作人看到的仍是
三句一模一样的话。

同时守那张退出码表：它有**三份**（UI 常量 / 运维手册 / 编排器模块 docstring），
过去零一致性测试，且三份都把 11 写成了一个**具体原因**（「查 token / 网络」），
而 11 的实际条件是「01 以 0/3 以外任何码退出」——一整类。
"""

from __future__ import annotations

import ast
import logging
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

import src.data_pipeline.daily_update as du
from web.operator_ui.pages._today_workbench_helpers import failed_update_summary
from web.operator_ui.update_status import EXIT_CODE_MEANINGS, UpdateRunStatus

_ROOT = Path(__file__).resolve().parents[2]
_PAGE = _ROOT / "web" / "operator_ui" / "pages" / "today_workbench.py"
_RUNBOOK = _ROOT / "docs" / "runbook_daily_update_scheduling.md"


def _status(detail: str) -> UpdateRunStatus:
    return UpdateRunStatus(
        kind="finished", path=Path("x"), state="finished",
        exit_code=11, failed_stage="fetch", detail=detail)


class TheReasonIsRenderedNotDiscarded(unittest.TestCase):

    REAL = ("fetch failed hard (exit 1) — refusing narrower-scope merge for "
            "endpoint 'daily': ... Re-run the full range to extend it, or pass "
            "--reset-manifest")

    def test_the_reason_survives_into_the_operator_facing_line(self) -> None:
        line = failed_update_summary(_status(self.REAL))
        self.assertIn("--reset-manifest", line, "唯一能让操作人动手的那半没了")
        self.assertIn("fetch", line, "还得说清死在哪一环")

    def test_a_missing_reason_is_said_out_loud_not_left_blank(self) -> None:
        """留白读起来像「没有更多可说」，真相是「这次运行没写下原因」。

        两者对操作人的下一步完全不同：前者让人停手，后者让人去翻日志。
        """
        line = failed_update_summary(_status(""))
        self.assertIn("未写下原因", line)
        self.assertIn("日志", line)

    def test_whitespace_only_reason_counts_as_missing(self) -> None:
        self.assertEqual(
            failed_update_summary(_status("   \n  ")), failed_update_summary(_status("")))


class BothConsumersReadTheSameLine(unittest.TestCase):
    """失败卡片与今日待办队列必须走同一个函数。

    它们此前是两段手写的同义字符串。分头演化时，「为什么」那一段——唯一可行动
    的部分——迟早会在其中一处被漏掉，而漏掉的那一处不会有任何人察觉。
    """

    _TREE = ast.parse(_PAGE.read_text(encoding="utf-8"))

    def _calls_named(self, name: str) -> list[ast.Call]:
        return [
            node for node in ast.walk(self._TREE)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == name
        ]

    def test_the_page_calls_the_shared_helper_twice(self) -> None:
        self.assertEqual(
            2, len(self._calls_named("failed_update_summary")),
            "失败卡片与队列喂料，各一处；少一处就是那一处又在手写")

    def test_the_page_hand_writes_no_failure_line_of_its_own(self) -> None:
        # 第二道独立守卫：AST 只看得见「调了」，看不见「旁边还留着一份手写的」。
        source = _PAGE.read_text(encoding="utf-8")
        self.assertNotIn(
            "失败阶段：", source,
            "页面里还留着手写的失败行——它会和 failed_update_summary 分头漂移")

    def test_the_failure_card_is_the_one_that_got_the_helper(self) -> None:
        # 防止「两处调用」落在同一个分支里而失败卡片其实没接上。
        cards = [
            node for node in ast.walk(self._TREE)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_render_card"
            and any("更新失败" in ast.unparse(arg) for arg in node.args)
        ]
        self.assertEqual(1, len(cards), "找不到失败卡片——本守卫已失效")
        self.assertIn("failed_update_summary", ast.unparse(cards[0]))


class AFailedValidationCheckIsLoggedAsAnError(unittest.TestCase):
    """校验器把失败的检查按 ERROR 记——级别在这里不只是显示问题。

    `daily_update` 捕获阶段失败原因时只收 ERROR 记录。失败的检查若记成 INFO，
    一次普通的契约失败在状态工件里就只剩 `validation failed (exit N)`，操作人
    还是得去翻日志——本改动等于在这个阶段上白做（codex #462）。

    这里调**真的** `PITValidator._log_summary`，不用假 runner 自己发 ERROR：
    要验的正是真实校验路径的日志行为。
    """

    @staticmethod
    def _levels_for(passed: bool) -> list[int]:
        from src.data.pit.pit_validator import (
            CheckResult,
            PITValidationReport,
            PITValidator,
        )
        report = PITValidationReport(
            checks=[CheckResult(
                name="calendar spine", code="A", passed=passed,
                errors=[] if passed else ["3 sessions missing from day.txt"],
                warnings=["reference case absent"])],
            provider_dir=Path("X"),
        )
        seen: list[int] = []
        probe = logging.Handler()
        probe.emit = lambda record: seen.append(record.levelno)  # type: ignore[method-assign]
        logger = logging.getLogger("src")
        logger.addHandler(probe)
        try:
            PITValidator._log_summary(report)
        finally:
            logger.removeHandler(probe)
        return seen

    def test_a_failed_check_reaches_error_level(self) -> None:
        self.assertIn(
            logging.ERROR, self._levels_for(passed=False),
            "失败的检查记成了 INFO —— 阶段失败原因的捕获收不到它")

    def test_a_passing_check_stays_at_info(self) -> None:
        # 全过时不许把摘要吵成 ERROR：那会让捕获收进一堆无关内容。
        self.assertNotIn(logging.ERROR, self._levels_for(passed=True))

    def test_the_captured_reason_survives_into_the_artifact(self) -> None:
        """端到端：校验失败的那句话要走到 `detail` 里。"""
        import src.data_pipeline.daily_update as du
        from src.data.pit.pit_validator import (
            CheckResult,
            PITValidationReport,
            PITValidator,
        )
        report = PITValidationReport(
            checks=[CheckResult(name="calendar spine", code="A", passed=False,
                                errors=["3 sessions missing from day.txt"])],
            provider_dir=Path("X"),
        )
        with du._capture_stage_errors() as captured:
            PITValidator._log_summary(report)
        detail = du._stage_detail("validation failed (exit 2)", captured)
        self.assertIn("3 sessions missing", detail)


class TheNoReasonMarkIsStatedOnceOnEachSide(unittest.TestCase):
    """写入侧与读侧刻意不互相 import，所以标记串在两处各声明一次。

    与 `STATUS_SCHEMA_VERSION` 同样的处理：重复是必要的，零一致性测试不是——
    两处分头改一个字，读侧就再也认不出兜底串，工作台又会把「只有退出码」当成
    原因渲染出来。
    """

    def test_both_sides_declare_the_same_mark(self) -> None:
        import src.data_pipeline.daily_update as du
        from web.operator_ui.update_status import NO_REASON_MARK
        self.assertEqual(du._NO_REASON_MARK, NO_REASON_MARK)

    def test_the_mark_is_not_empty(self) -> None:
        # 空串会让 `endswith` 恒真，读侧从此把每一条原因都说成「没有原因」。
        from web.operator_ui.update_status import NO_REASON_MARK
        self.assertTrue(NO_REASON_MARK.strip())


class AFallbackSummaryIsNotDressedUpAsAReason(unittest.TestCase):
    """阶段一条 ERROR 都没记时，读侧不许说「原因：<退出码摘要>」。

    那是把「我们只有一个退出码」伪装成一条解释——比不说更糟：操作人会以为
    这就是原因，而不去翻日志（codex #462）。
    """

    def _silent_failure_line(self) -> str:
        """从一个**什么都不记**的失败阶段走完整条链路，而不是伪造一个空 detail。"""
        import src.data_pipeline.daily_update as du

        def silent(argv: list[str]) -> int:
            return 1

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = du.DailyUpdateConfig(
                tushare_dir=root / "raw", provider_dir=root / "provider",
                delisted_registry=root / "raw" / "reg.parquet",
                reference_cases=root / "cases.yaml", now=date(2026, 6, 10),
            )
            (cfg.tushare_dir).mkdir(parents=True, exist_ok=True)
            pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["平安银行"],
                          "snapshot_date": ["20260610"]}).to_parquet(
                cfg.tushare_dir / "active_stocks.parquet")
            for sub in ("calendars", "instruments", "features"):
                (cfg.provider_dir / sub).mkdir(parents=True, exist_ok=True)
            (cfg.provider_dir / "calendars" / "day.txt").write_text("L", encoding="utf-8")
            (cfg.provider_dir / "instruments" / "all.txt").write_text("", encoding="utf-8")
            runners = {s: (lambda argv: 0) for s in (
                "fetch", "registry", "bins", "membership", "universe",
                "benchmark", "validate")}
            runners["fetch"] = silent
            _, _, detail = du._execute_daily_update(cfg, runners)  # type: ignore[arg-type]
        return failed_update_summary(_status(detail))

    def test_a_silent_failure_says_no_reason_was_recorded(self) -> None:
        line = self._silent_failure_line()
        self.assertNotIn("原因：", line, "把退出码摘要当成原因端了出来")
        self.assertIn("未在日志中留下原因", line)
        self.assertIn("日志", line)

    def test_the_exit_code_summary_is_still_shown(self) -> None:
        # 不说「原因」不等于把摘要也吞掉：它仍是操作人手里唯一的线索。
        self.assertIn("fetch failed hard", self._silent_failure_line())


class TheThreeExitCodeTablesAgree(unittest.TestCase):
    """一条码 = 一个**阶段**，不是一个原因；而且三份表必须列同一组码。"""

    @staticmethod
    def _constants() -> set[int]:
        # 从模块属性推导，不手抄：新增一个 EXIT_* 常量就会让三份表全部欠账。
        return {
            v for k, v in vars(du).items()
            if k.startswith("EXIT_") and isinstance(v, int) and not isinstance(v, bool)
        }

    @staticmethod
    def _docstring_table() -> set[int]:
        doc = du.__doc__ or ""
        return {int(m) for m in re.findall(r"^ {4}(\d+) {1,2}\S", doc, re.MULTILINE)}

    @staticmethod
    def _runbook_table() -> set[int]:
        text = _RUNBOOK.read_text(encoding="utf-8")
        section = text.split("## Monitoring — exit codes", 1)
        assert len(section) == 2, "运维手册的退出码小节改名了——本守卫已失效"
        body = section[1].split("\n## ", 1)[0]
        return {int(m) for m in re.findall(r"^\|\s*\**(\d+)\**\s*\|", body, re.MULTILINE)}

    def test_all_three_tables_list_the_same_codes(self) -> None:
        constants = self._constants()
        self.assertGreaterEqual(len(constants), 8, "没找到退出码常量——本守卫已失效")
        for name, got in (
            ("UI EXIT_CODE_MEANINGS", set(EXIT_CODE_MEANINGS)),
            ("编排器模块 docstring", self._docstring_table()),
            ("运维手册", self._runbook_table()),
        ):
            with self.subTest(表=name):
                self.assertEqual(
                    constants, got,
                    f"{name} 与 EXIT_* 常量对不上；缺的码操作人查不到含义")

    def test_exit_11_no_longer_names_one_cause_as_the_cause(self) -> None:
        """回归钉（不是通用判据）：11 曾写作「查 token / 网络」。

        2026-08-17/20/21 三晚的 11 全都是 fetch manifest 拒绝缩窄合并，token 与
        网络自始至终正常。那句文案把排障直接引向了错的地方。
        """
        meaning = EXIT_CODE_MEANINGS[du.EXIT_FETCH_HARD]
        self.assertNotIn("token", meaning.lower())
        self.assertIn("详情", meaning, "得告诉操作人真正的原因去哪儿看")

    def test_the_crash_code_names_the_exception_and_the_detail(self) -> None:
        """回归钉（#465 codex P2）：crash 记录入账 1，表里却没有 1。

        crash 路径把进程码 1 正式写进了状态与台账（磁盘满/权限崩产线可达），
        而 UI 表只列 0/2/10-17——刚定义的失败被渲成「未知退出码」。1 不是
        哪一环的专属码，含义必须说「异常」并把人指向详情（异常本身在那）。
        """
        meaning = EXIT_CODE_MEANINGS[du.EXIT_UNHANDLED_EXCEPTION]
        self.assertIn("异常", meaning)
        self.assertIn("详情", meaning, "得告诉操作人异常本身去哪儿看")

    def test_the_runbook_points_at_the_detail_field(self) -> None:
        body = _RUNBOOK.read_text(encoding="utf-8")
        row = [
            line for line in body.splitlines()
            if re.match(r"^\|\s*\**11\**\s*\|", line)
        ]
        self.assertEqual(1, len(row))
        self.assertIn("detail", row[0], "手册没指向承载原因的那个字段")


if __name__ == "__main__":
    unittest.main()
