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
import re
import unittest
from pathlib import Path

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
