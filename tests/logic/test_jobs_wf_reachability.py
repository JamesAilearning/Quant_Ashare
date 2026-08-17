"""作业↔滚动验证的可达性与状态词汇（UI drift 审计 P1）。

审计实测出两条：作业页列出的 CLI 滚动验证行点「查看详情」落到
「暂无滚动验证记录」（详情页只认 UI 作业目录）；CLI 侧状态词汇是
``ok``/``partial``，而页面筛选说「已完成」，于是筛选会吞掉自己刚
列出来的行。第三条是过程中查出的更深问题：运行目录索引的默认路径
按 CWD 解析，测试从仓库根跑会把记录写进操作人的真实索引，产物却落在
随后被删的临时目录里。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.operator_ui.job_io import (  # noqa: E402
    JobSummary,
    _normalise_cli_entry,
    run_dir_is_inspectable,
)

_PAGE_JOBS = PROJECT_ROOT / "web" / "operator_ui" / "pages" / "jobs.py"
_PAGE_WF = PROJECT_ROOT / "web" / "operator_ui" / "pages" / "walk_forward.py"


class StatusVocabularyTests(unittest.TestCase):
    """CLI 写 ok / partial；UI 词汇是 completed / partial。"""

    def test_cli_ok_is_normalised_to_the_ui_word(self) -> None:
        summary = _normalise_cli_entry(
            {"run_id": "r1", "engine": "walk_forward", "status": "ok",
             "output_dir": "output/walk_forward/x"}
        )
        self.assertEqual(summary.status, "completed")

    def test_partial_is_preserved_not_folded(self) -> None:
        # partial 已经是下拉选项/标签/图标/白名单里的词——归一成 completed
        # 会把「部分折缺 IC」这条信息抹掉。
        summary = _normalise_cli_entry(
            {"run_id": "r2", "engine": "walk_forward", "status": "partial",
             "output_dir": "output/walk_forward/x"}
        )
        self.assertEqual(summary.status, "partial")

    def test_unknown_status_words_pass_through_untouched(self) -> None:
        # 只翻译已知同义词；发明映射会把没见过的状态悄悄改写。
        summary = _normalise_cli_entry(
            {"run_id": "r3", "engine": "pipeline", "status": "weird",
             "output_dir": "output/runs/x"}
        )
        self.assertEqual(summary.status, "weird")


class RunDirInspectabilityTests(unittest.TestCase):
    """产物必须在 output 树内，否则详情页读不到（也是本页的读边界）。"""

    def test_paths_under_the_output_tree_are_inspectable(self) -> None:
        self.assertTrue(run_dir_is_inspectable("output/walk_forward/run1"))
        self.assertTrue(
            run_dir_is_inspectable(str(PROJECT_ROOT / "output" / "runs" / "r"))
        )

    def test_temp_dirs_are_not_inspectable(self) -> None:
        # 这正是本机 3404 条测试残留的形态。
        self.assertFalse(
            run_dir_is_inspectable(r"C:\Users\x\AppData\Local\Temp\tmpabcd")
        )
        self.assertFalse(run_dir_is_inspectable("/tmp/tmpabcd"))

    def test_blank_is_not_inspectable(self) -> None:
        self.assertFalse(run_dir_is_inspectable(""))
        self.assertFalse(run_dir_is_inspectable("   "))

    def test_escapes_out_of_the_tree_are_refused(self) -> None:
        self.assertFalse(run_dir_is_inspectable("output/../../elsewhere"))

    def test_relative_rows_anchor_to_the_repo_not_the_cwd(self) -> None:
        # 索引里 1257 条是相对路径，按进程 CWD 解析会随启动目录变答案。
        import os

        original = os.getcwd()
        try:
            os.chdir(PROJECT_ROOT.parent)
            self.assertTrue(run_dir_is_inspectable("output/walk_forward/run1"))
        finally:
            os.chdir(original)


class JobSummaryCarriesRunDirTests(unittest.TestCase):
    def test_cli_entry_carries_output_dir_as_run_dir(self) -> None:
        summary = _normalise_cli_entry(
            {"run_id": "r", "engine": "walk_forward", "status": "ok",
             "output_dir": "output/walk_forward/abc"}
        )
        self.assertEqual(summary.run_dir, "output/walk_forward/abc")
        self.assertIn("run_dir", summary.to_dict())

    def test_run_dir_defaults_empty_so_old_callers_are_unaffected(self) -> None:
        self.assertEqual(JobSummary(run_id="x", type="p", status="ok").run_dir, "")


class PageSourcePinsTests(unittest.TestCase):
    def test_walk_forward_page_accepts_cli_runs(self) -> None:
        src = _PAGE_WF.read_text(encoding="utf-8")
        # 详情页必须也从统一清单取 CLI 行，否则作业页的跳转还是死路。
        self.assertIn("list_all_jobs", src)
        self.assertIn('source_filter="cli"', src)
        self.assertIn('type_filter="walk_forward"', src)

    def test_walk_forward_page_shows_run_identity_and_anchor(self) -> None:
        src = _PAGE_WF.read_text(encoding="utf-8")
        self.assertIn("rebalance_anchor", src)
        self.assertIn("fold_phase", src)
        self.assertIn("运行身份", src)

    def test_walk_forward_page_never_defaults_missing_metric_status(self) -> None:
        # 缺失是主路径（本机 21 个真实运行里 16 个没有该键，含全部 csi800
        # 战役运行）——缺失若落进 official 分支，#406 整套防线在 UI 上作废。
        src = _PAGE_WF.read_text(encoding="utf-8")
        self.assertIn("metric_status", src)
        self.assertIn("OFFICIAL_METRIC_STATUS", src)
        self.assertIn("_metric_status is None", src)
        self.assertIn("未标注", src)
        # 判定分支必须先处理缺失，再谈 official —— 顺序反了就会把 None
        # 归进 else 的告警或 official 的放行。
        none_at = src.index("_metric_status is None")
        official_at = src.index("elif _metric_status == OFFICIAL_METRIC_STATUS")
        self.assertLess(none_at, official_at)

    def test_jobs_page_discloses_rows_it_set_aside(self) -> None:
        src = _PAGE_JOBS.read_text(encoding="utf-8")
        self.assertIn("count_cli_rows_outside_output_tree", src)
        self.assertIn("未列出", src)

    def test_disclosure_does_not_swallow_the_pagination_controls(self) -> None:
        # codex #444 r1: 最初把披露段插在 pg_indicator 与 pg_next 之间，
        # 缩进把 `with pg_next:` 一起吞进了 `if _set_aside:` —— 没有搁置行
        # 时「下一页」按钮直接消失。披露必须整体在分页块之后。
        src = _PAGE_JOBS.read_text(encoding="utf-8")
        next_at = src.index("with pg_next:")
        disclosure_at = src.index("_set_aside = count_cli_rows_outside_output_tree()")
        self.assertLess(
            next_at, disclosure_at, "分页控件必须在披露段之前且不受其条件控制"
        )
        # 且披露块内不得出现任何分页控件。
        block = src[disclosure_at : disclosure_at + 800]
        self.assertNotIn("pg_next", block)
        self.assertNotIn("pg_prev", block)

    def test_cli_options_are_anchored_like_the_inspectability_check(self) -> None:
        # codex #444 r1: 判据把相对 output_dir 锚在仓库根，而页面下游的
        # `Path(selected)` → guard_output_path 走进程 CWD。存原始相对串会
        # 让「判定可达」的运行反被守卫拒绝（在仓库根之外启动 UI 时）。
        src = _PAGE_WF.read_text(encoding="utf-8")
        self.assertIn("PROJECT_ROOT", src)
        self.assertIn("if not Path(_resolved).is_absolute():", src)
        self.assertIn("str(PROJECT_ROOT / _resolved)", src)

    def test_anchor_caption_matches_the_governance_pin(self) -> None:
        # codex #444 r1: 起初把 iso_week 说成「认证胜者」——写反了。治理钉
        # 明写 winner=fold_phase / isoweek 复核=iso_week；页面若反过来说，
        # 合法的认证证据会被当成参照运行。这里直接对着治理钉断言，而不是
        # 断言某句措辞，这样将来钉子挪了测试也会跟着响。
        import yaml

        presets = PROJECT_ROOT / "config" / "presets"
        winner = yaml.safe_load(
            (presets / "csi800_cadence5_conservative.yaml").read_text(
                encoding="utf-8"
            )
        )
        isoweek = yaml.safe_load(
            (presets / "csi800_cadence5_conservative_isoweek.yaml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(winner["rebalance_anchor"], "fold_phase")
        self.assertEqual(isoweek["rebalance_anchor"], "iso_week")

        src = _PAGE_WF.read_text(encoding="utf-8")
        fold_at = src.index('if _anchor == "fold_phase":')
        fold_block = src[fold_at : fold_at + 400]
        # fold_phase 段必须称它为认证胜者，且不得称 iso_week 为认证胜者。
        self.assertIn("认证胜者", fold_block)
        self.assertNotIn("生产认证的胜者是 `iso_week`", src)
        iso_at = src.index('elif _anchor == "iso_week":')
        iso_block = src[iso_at : iso_at + 400]
        self.assertIn("生产服务锚", iso_block)


if __name__ == "__main__":
    unittest.main()
