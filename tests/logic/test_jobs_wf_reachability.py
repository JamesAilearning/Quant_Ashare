"""作业↔滚动验证的可达性与状态词汇（UI drift 审计 P1）。

审计实测出两条：作业页列出的 CLI 滚动验证行点「查看详情」落到
「暂无滚动验证记录」（详情页只认 UI 作业目录）；CLI 侧状态词汇是
``ok``/``partial``，而页面筛选说「已完成」，于是筛选会吞掉自己刚
列出来的行。第三条是过程中查出的更深问题：运行目录索引的默认路径
按 CWD 解析，测试从仓库根跑会把记录写进操作人的真实索引，产物却落在
随后被删的临时目录里。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.operator_ui.job_io import (  # noqa: E402
    JobSummary,
    _normalise_cli_entry,
    anchored_run_dir,
    fold_catalog_by_dir,
    list_all_jobs,
    run_dir_is_inspectable,
)
from web.operator_ui.pages._walk_forward_helpers import (  # noqa: E402
    _CAMPAIGN_CONSTRAINT_FIELDS,
    _GOVERNED_FAMILY,
    _knob_matches,
    governed_family_mismatches,
)

_PAGE_JOBS = PROJECT_ROOT / "web" / "operator_ui" / "pages" / "jobs.py"
_PAGE_WF = PROJECT_ROOT / "web" / "operator_ui" / "pages" / "walk_forward.py"
_PAGE_RESULTS = PROJECT_ROOT / "web" / "operator_ui" / "pages" / "results.py"


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


class CatalogFoldBehaviorTests(unittest.TestCase):
    """折叠算法的**行为**覆盖。

    r1/r2/r4 三轮改的都是这个算法，此前只有字符串钉守着；提到共享层之后
    它是纯函数，可以真跑一遍。三个维度各至少一例：锚定、首条即最新、
    被覆盖者的去向。
    """

    @staticmethod
    def _row(run_id: str, run_dir: str) -> JobSummary:
        return JobSummary(
            run_id=run_id, type="pipeline", status="completed", source="cli",
            run_dir=run_dir,
        )

    def test_relative_dirs_anchor_at_the_repo_not_the_cwd(self) -> None:
        # 与可检视判据同源：判据锚在仓库根，页面若锚在 CWD，在仓库根之外
        # 启动 UI 时「判定可达」的运行会反被路径守卫拒绝（codex #444 r1）。
        folded = fold_catalog_by_dir([self._row("a", "output/runs/x")])
        self.assertEqual(folded.dir_of_run["a"], PROJECT_ROOT / "output/runs/x")
        # 判据与折叠对同一条相对行必须给出一致的答案。
        self.assertTrue(run_dir_is_inspectable("output/runs/x"))

    def test_absolute_dirs_pass_through_untouched(self) -> None:
        target = PROJECT_ROOT / "output" / "runs" / "abs"
        folded = fold_catalog_by_dir([self._row("a", str(target))])
        self.assertEqual(folded.dir_of_run["a"], target)

    def test_first_row_per_dir_wins_and_the_rest_are_superseded(self) -> None:
        # 目录记录按完成时间倒序，首条即最新；同目录更早的那些产物已被覆盖。
        rows = [
            self._row("newest", "output/runs/same"),
            self._row("older", "output/runs/same"),
            self._row("oldest", "output/runs/same"),
            self._row("other", "output/runs/elsewhere"),
        ]
        folded = fold_catalog_by_dir(rows)
        self.assertEqual([r.run_id for r in folded.newest], ["newest", "other"])
        self.assertEqual(folded.superseded_count, 2)
        self.assertEqual(
            set(folded.superseded_dir_of_run), {"older", "oldest"}
        )

    def test_superseded_ids_never_appear_in_the_alias_side(self) -> None:
        # codex #444 r4 的核心：被覆盖的 id 若也能定位到目录，点它就会静默
        # 渲染出别人的报告。两张表必须不相交。
        rows = [
            self._row("newest", "output/runs/same"),
            self._row("older", "output/runs/same"),
        ]
        folded = fold_catalog_by_dir(rows)
        self.assertEqual(
            set(folded.dir_of_run) & set(folded.superseded_dir_of_run), set()
        )
        self.assertNotIn("older", folded.dir_of_run)
        # 但它仍要能说出「谁覆盖了它」，否则告警无从指路。
        self.assertEqual(
            folded.superseded_dir_of_run["older"], folded.dir_of_run["newest"]
        )

    def test_case_differences_collapse_on_windows_semantics(self) -> None:
        # 同一个目录写成两种大小写，若不 normcase 就会被当成两个目录，
        # 折叠漏掉一半。
        rows = [
            self._row("a", "output/runs/CaseDir"),
            self._row("b", "output/runs/casedir"),
        ]
        folded = fold_catalog_by_dir(rows)
        if os.path.normcase("A") == os.path.normcase("a"):
            self.assertEqual(len(folded.newest), 1)
            self.assertEqual(folded.superseded_count, 1)
        else:
            self.assertEqual(len(folded.newest), 2)

    def test_rows_without_a_dir_are_dropped_not_crashed(self) -> None:
        folded = fold_catalog_by_dir(
            [self._row("blank", ""), self._row("real", "output/runs/y")]
        )
        self.assertEqual([r.run_id for r in folded.newest], ["real"])
        self.assertNotIn("blank", folded.dir_of_run)
        self.assertNotIn("blank", folded.superseded_dir_of_run)

    def test_parent_segments_collapse_so_equivalent_paths_fold(self) -> None:
        # codex #444 r6: output/runs/a 与 output/x/../runs/a 指向同一份产物、
        # 两者都判可达；折叠键若保留字面 ``..`` 就会被当成两次不同的运行，
        # 被覆盖的历史行于是静默渲染出当前那份报告。
        self.assertEqual(
            anchored_run_dir("output/runs/a"),
            anchored_run_dir("output/x/../runs/a"),
        )
        self.assertTrue(run_dir_is_inspectable("output/x/../runs/a"))
        folded = fold_catalog_by_dir([
            self._row("newest", "output/runs/a"),
            self._row("lexical", "output/x/../runs/a"),
        ])
        self.assertEqual([r.run_id for r in folded.newest], ["newest"])
        self.assertEqual(folded.superseded_count, 1)
        self.assertEqual(
            folded.superseded_dir_of_run["lexical"], folded.dir_of_run["newest"]
        )

    def test_the_inspectability_verdict_uses_the_shared_anchor(self) -> None:
        # 「共用同一段代码」这句话得是真的：判据自己再抄一份锚定，正是 r1
        # 那个 bug 会回来的方式。
        src = (PROJECT_ROOT / "web" / "operator_ui" / "job_io.py").read_text(
            encoding="utf-8"
        )
        verdict_at = src.index("def run_dir_is_inspectable")
        body = src[verdict_at : src.index("def anchored_run_dir")]
        self.assertIn("anchored_run_dir(", body)
        self.assertNotIn("PROJECT_ROOT / candidate", body)

    def test_empty_input_is_an_empty_fold(self) -> None:
        folded = fold_catalog_by_dir([])
        self.assertEqual(folded.newest, ())
        self.assertEqual(folded.superseded_count, 0)

    def test_real_catalog_folds_identically_for_both_run_types(self) -> None:
        # 真目录上跑一遍：本机 92 条滚动验证行折叠成 20 个目录 / 72 条被覆盖。
        # 断言的是**不变式**而不是这两个数字（目录内容会变）。
        for kind in ("walk_forward", "pipeline"):
            rows, _, _ = list_all_jobs(
                type_filter=kind, source_filter="cli", page=1, page_size=100_000,
            )
            folded = fold_catalog_by_dir(rows)
            with self.subTest(kind=kind):
                dirs = {os.path.normcase(str(p)) for p in folded.dir_of_run.values()}
                self.assertEqual(len(dirs), len(folded.newest))
                self.assertEqual(
                    len(folded.newest) + folded.superseded_count,
                    sum(1 for r in rows if r.run_dir),
                )
                self.assertEqual(
                    set(folded.dir_of_run) & set(folded.superseded_dir_of_run), set()
                )


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

    def test_disclosure_precedes_empty_state_exits(self) -> None:
        # codex #444 r4: 目录全是越界记录时（正是本改动针对的重污染场景），
        # 页面会说「暂无作业」然后 st.stop()，反而一个字都不提搁置了多少
        # —— 最需要披露的场景恰好不披露。披露必须在两处空态之前。
        src = _PAGE_JOBS.read_text(encoding="utf-8")
        disclosure_at = src.index("_set_aside = count_cli_rows_outside_output_tree()")
        first_empty_at = src.index("if total == 0 and not _active:")
        filtered_empty_at = src.index(chr(10) + "if total == 0:")
        self.assertLess(disclosure_at, first_empty_at)
        self.assertLess(disclosure_at, filtered_empty_at)

    def test_disclosure_does_not_swallow_the_pagination_controls(self) -> None:
        # codex #444 r1: 最初把披露段插在 pg_indicator 与 pg_next 之间，
        # 缩进把 `with pg_next:` 一起吞进了 `if _set_aside:` —— 没有搁置行
        # 时「下一页」按钮直接消失。披露块内不得含任何分页控件。
        src = _PAGE_JOBS.read_text(encoding="utf-8")
        disclosure_at = src.index("_set_aside = count_cli_rows_outside_output_tree()")
        block = src[disclosure_at : disclosure_at + 800]
        self.assertNotIn("pg_next", block)
        self.assertNotIn("pg_prev", block)

    def test_both_detail_pages_share_one_fold_implementation(self) -> None:
        # codex #444 r1/r2/r4 三轮改的是**同一个**算法（锚定 / 首条即最新 /
        # 被覆盖者只计数不别名），r5 又要求结果页做同一件事。两页各写一份
        # 必然分叉 —— 所以实现只留一份，两页都调它。
        for page in (_PAGE_WF, _PAGE_RESULTS):
            src = page.read_text(encoding="utf-8")
            with self.subTest(page=page.name):
                self.assertIn("fold_catalog_by_dir", src)
                # 页面里不得再自己写一遍折叠。
                self.assertNotIn("if not Path(_resolved).is_absolute():", src)

    def test_unmatched_requested_run_is_not_silently_swapped(self) -> None:
        # codex #444 r2: 同一 preset 反复跑会把报告写回同一个 output_dir，
        # 目录键因此把多条 catalog 行折叠成一条。点旧行时匹配不上，页面
        # 原本静默落到 index 0 —— 操作人会以为看的是自己点的那次。
        # 实测本机 92 条折叠成 20 个目录、8 个目录被反复覆盖。
        src = _PAGE_WF.read_text(encoding="utf-8")
        self.assertIn("_requested_found", src)
        self.assertIn("不在可打开清单中", src)
        self.assertIn("_superseded_runs", src)
        # 告警必须在 selectbox **之前**渲染，否则操作人先看到选中项、
        # 再看到告警，第一印象已经错了。
        warn_at = src.index("不在可打开清单中")
        select_at = src.index("selected = st.selectbox(")
        self.assertLess(warn_at, select_at)

    def test_every_known_run_id_can_locate_its_directory(self) -> None:
        # codex #444 r3: UI 启动的滚动验证会**同时**留下一条 UI 作业和一条
        # CLI 目录记录，指向同一个 output_dir（JobManager 把结果目录写进
        # config["output_dir"]，引擎再按它编目）。选择器每目录只放一条，
        # 若只比「选择器上恰好展示的那个 id」，另一个 id 的跳转永远匹配不上。
        src = _PAGE_WF.read_text(encoding="utf-8")
        self.assertIn("_run_id_to_dir", src)
        self.assertIn("_run_id_to_dir.setdefault(_job.run_id, _resolved)", src)
        self.assertIn("_target_dir = _run_id_to_dir.get(_requested_run_id)", src)
        self.assertIn("if key == _locate or run_options[key]", src)
        # 索引必须**同时**收 UI 作业与 CLI 记录，只收一边等于没修。
        idx_at = src.index("_run_id_to_dir: dict[str, str] = {")
        seed = src[idx_at : idx_at + 400]
        self.assertIn("wf_jobs", seed)

    def test_spec_delta_does_not_encode_anchor_only_governance(self) -> None:
        # codex #444 r3: 归档时 spec delta 里的错话会把治理错误重新引回来
        # —— 比 UI 上的一句话更持久。
        spec = (
            PROJECT_ROOT / "openspec" / "changes" / "2026-08-17-ui-drift-p1"
            / "specs" / "v2-operator-ui-console" / "spec.md"
        ).read_text(encoding="utf-8")
        self.assertIn("SHALL NOT be presented as deciding what is production", spec)
        self.assertIn("certified winner runs on\n`fold_phase`", spec)
        self.assertIn("separately gated re-check", spec)

    def test_governance_captions_only_apply_to_the_governed_family(self) -> None:
        # codex #444 r4: 只凭 anchor 推治理身份，会把 stage7_daily_h5 /
        # csi300 参照运行标成「认证胜者」—— 而本 change 的 delta 自己就写着
        # 「anchor 单独 SHALL NOT 被当作生产判据」。文案必须先确认整族身份。
        # r6 起判据不再写在页面里（那样它会和晋升族语义各自漂），而是取自
        # _GOVERNED_FAMILY —— 字段清单本身由 GovernedFamilyPredicateTests
        # 对着 EVAL_PROFILES 真跑核对。这里只钉「页面确实消费它」。
        src = _PAGE_WF.read_text(encoding="utf-8")
        self.assertIn("governed_family_mismatches(_wf_config)", src)
        self.assertIn("_governed = not _mismatched", src)
        # 页面里不得再硬写一份字段清单。
        gov_at = src.index("_mismatched = governed_family_mismatches")
        block = src[gov_at : gov_at + 400]
        for field in ("instruments", "benchmark_code", "rebalance_cadence_days",
                      "rebalance_phase", "slippage_bps"):
            with self.subTest(field=field):
                self.assertNotIn(f'"{field}"', block)
        # 族外必须明说「不给治理判断」，而不是沉默或照旧断言。
        self.assertIn("不属于被治理的 csi800 认证族", src)
        # 而且要说清是**哪一项**不符 —— 只说「不属于」等于让人自己猜。
        self.assertIn("不符项", src)
        # 两条治理文案都必须在 _governed 之后（elif 链），不能独立触发。
        self.assertIn("if not _governed:", src)

    def test_superseded_ids_are_not_aliased_into_the_newest_report(self) -> None:
        # codex #444 r4: r3 把**每个**被覆盖的 id 都塞进别名表，于是点旧行
        # 时 _requested_found=True，绕过告警、静默渲染最新那份报告 ——
        # 等于把 r2 加的告警又废掉了。别名只覆盖同一次调用的 UI/CLI 两个 id。
        # 判据已提到共享层，所以这里钉的是「页面消费的是被覆盖表而不是
        # 别名表」；折叠本身的行为由 CatalogFoldBehaviorTests 真跑一遍。
        src = _PAGE_WF.read_text(encoding="utf-8")
        self.assertIn("_superseded_runs = _folded.superseded_count", src)
        alias_at = src.index("_run_id_to_dir.setdefault(_job.run_id, _resolved)")
        # 别名只在遍历 newest 的循环体内写入 —— newest 里不含被覆盖的行。
        loop_at = src.index("for _job in _folded.newest:")
        self.assertLess(loop_at, alias_at)
        self.assertNotIn("superseded_dir_of_run", src[loop_at:alias_at])

    def test_results_page_admits_cli_pipeline_runs(self) -> None:
        # codex #444 r5: 作业页把 CLI 流水线行路由到结果页，而结果页的选择器
        # 只由 JobManager.list_jobs() 构成 —— 那些行点进去是「运行未找到」，
        # 正是本 change 的 delta 所禁止的死链。
        src = _PAGE_RESULTS.read_text(encoding="utf-8")
        self.assertIn('type_filter="pipeline", source_filter="cli"', src)
        self.assertIn("_run_id_alias", src)
        # 别名解析必须发生在 run-not-found 之前，否则 CLI 镜像 id 仍会撞墙。
        alias_at = src.index('_alias = _run_id_alias.get(requested_run_id, "")')
        not_found_at = src.index("_render_run_not_found(requested_run_id)")
        self.assertLess(alias_at, not_found_at)

    def test_results_page_does_not_flash_away_its_superseded_warning(self) -> None:
        # 解析后的 id 若拿去和**原始请求**比，别名/被覆盖两条路一进来就会
        # 改写 query_params → 触发重跑 → 告警只闪一下就没了。
        src = _PAGE_RESULTS.read_text(encoding="utf-8")
        self.assertIn("if selected_job_id and selected_job_id != _selected_run_id:", src)
        self.assertNotIn(
            "if selected_job_id and selected_job_id != requested_run_id:", src
        )

    def test_results_page_separates_overwritten_from_missing(self) -> None:
        # 「产物被覆盖」说成「运行未找到」会让操作人以为记录被删了。
        src = _PAGE_RESULTS.read_text(encoding="utf-8")
        self.assertIn("_superseded_owner", src)
        self.assertIn("的产物已被覆盖", src)
        # 被覆盖表必须在 newest 建完之后才填 —— 纯 CLI 目录的占位者正是那轮
        # 才写进 _dir_owner 的，先填会把它们漏成「运行未找到」。
        newest_at = src.index("for _row in _folded.newest:")
        sup_at = src.index("for _run_id, _dir in _folded.superseded_dir_of_run.items():")
        self.assertLess(newest_at, sup_at)

    def test_superseded_ids_route_to_their_directory_owner(self) -> None:
        # codex #444 r6: 被覆盖的 id 故意不进 _run_id_to_dir（那是静默别名
        # 的路），但也不能就这么丢了 —— _target_dir 为空、_default_index 落到
        # 0，页面渲染的是**全局第一条**（很可能是另一个目录的运行），告警还
        # 说不出是谁覆盖了它。要定位到它自己那个目录，同时保留告警。
        src = _PAGE_WF.read_text(encoding="utf-8")
        self.assertIn("_superseded_dir", src)
        self.assertIn("_overwritten_at = _superseded_dir.get(_requested_run_id)", src)
        self.assertIn("_locate = _target_dir or _overwritten_at", src)
        # 定位成功也**不算**找到 —— 否则退回成 r2 修掉的「静默换人」。
        self.assertIn("_requested_found = _target_dir is not None", src)
        # 告警必须指名现在住在那个目录里的是谁。
        self.assertIn("_occupant = run_options.get(_overwritten_at", src)
        # 且被覆盖的 id 依然不得进入静默别名表。
        alias_at = src.index("_run_id_to_dir.setdefault(_job.run_id, _resolved)")
        sup_at = src.index("_superseded_dir: dict[str, str] = {")
        self.assertLess(alias_at, sup_at)
        self.assertNotIn("_run_id_to_dir.setdefault", src[sup_at : sup_at + 400])


class GovernedFamilyPredicateTests(unittest.TestCase):
    """治理族判据的**行为**覆盖（codex #444 r4/r6）。

    判据住在 streamlit-free 的 ``_walk_forward_helpers``，所以能直接真跑 ——
    为了一个纯谓词去导入页面（连带 streamlit）正是 #442 r6 抓到的错法。
    """

    @staticmethod
    def _preset(name: str) -> dict[str, object]:
        import yaml

        path = PROJECT_ROOT / "config" / "presets" / f"{name}.yaml"
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}

    def _governed(self, cfg: dict[str, object]) -> bool:
        return not governed_family_mismatches(cfg)

    def test_predicate_is_derived_from_the_promotion_profile(self) -> None:
        # 不复述字面量：晋升族语义钉在 EVAL_PROFILES，抄一份到 UI 只会各自漂。
        from scripts.eval_profiles import EVAL_PROFILES

        profile = EVAL_PROFILES["csi800_n5"]
        for key, want in _GOVERNED_FAMILY.items():
            with self.subTest(key=key):
                self.assertEqual(want, profile[key])

    def test_cost_convention_is_part_of_the_identity(self) -> None:
        # codex #444 r6: 少了 slippage_bps，csi800_cadence5_base（5 bps 灵敏度
        # 臂）四个旧谓词全中，会被标成「认证胜者」。
        self.assertIn("slippage_bps", _GOVERNED_FAMILY)
        self.assertNotIn("rebalance_anchor", _GOVERNED_FAMILY)  # 族跨两个锚

    def test_the_certified_pair_is_governed_and_the_base_arm_is_not(self) -> None:
        self.assertTrue(self._governed(self._preset("csi800_cadence5_conservative")))
        self.assertTrue(
            self._governed(self._preset("csi800_cadence5_conservative_isoweek"))
        )
        base = self._preset("csi800_cadence5_base")
        self.assertFalse(self._governed(base))
        # 且它落选的原因就是成本口径，不是别的巧合。
        self.assertEqual(base["slippage_bps"], 5.0)
        self.assertEqual(base["instruments"], "csi800")
        self.assertEqual(base["rebalance_cadence_days"], 5)

    def test_exactly_the_certified_pair_passes_across_all_presets(self) -> None:
        # 全预设扫一遍：任何新预设若意外入族，这里会响。
        import yaml

        governed = []
        for path in sorted((PROJECT_ROOT / "config" / "presets").glob("*.yaml")):
            try:
                cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError:
                continue
            if isinstance(cfg, dict) and self._governed(cfg):
                governed.append(path.stem)
        self.assertEqual(
            sorted(governed),
            [
                "csi800_cadence5_conservative",
                "csi800_cadence5_conservative_isoweek",
            ],
        )

    def test_constraint_semantics_are_part_of_the_identity(self) -> None:
        # codex #444 r7: profile 的 risk_constraint_scope / campaign_constraints
        # 也是晋升门的一部分。少比它们，一个把约束关掉、或把作用域换回
        # canonical 默认 all_days 的运行照样顶着「认证胜者」文案被读。
        self.assertIn("risk_constraint_scope", _GOVERNED_FAMILY)
        cfg = self._preset("csi800_cadence5_conservative")
        for key, bad in (
            ("risk_constraint_scope", "all_days"),
            ("risk_constraints_enabled", False),
            ("risk_constraints_calibration", "something_else"),
        ):
            with self.subTest(key=key):
                broken = dict(cfg)
                broken[key] = bad
                self.assertEqual(governed_family_mismatches(broken), [key])

    def test_campaign_switch_maps_onto_the_report_config_keys(self) -> None:
        # profile 里 campaign_constraints 是个语义开关，报告 config 里没有同名
        # 键 —— 映射写错等于这一维根本没比。
        from scripts.eval_profiles import EVAL_PROFILES

        self.assertTrue(EVAL_PROFILES["csi800_n5"]["campaign_constraints"])
        self.assertEqual(
            _CAMPAIGN_CONSTRAINT_FIELDS,
            {"risk_constraints_enabled": True,
             "risk_constraints_calibration": "campaign_v1"},
        )
        # 而这两把钥匙确实是认证预设里写着的值。
        cfg = self._preset("csi800_cadence5_conservative")
        for key, want in _CAMPAIGN_CONSTRAINT_FIELDS.items():
            with self.subTest(key=key):
                self.assertEqual(cfg[key], want)

    def test_booleans_do_not_collapse_into_numbers(self) -> None:
        # bool 是 int 的子类：不特判的话 True 会等于 1.0，
        # risk_constraints_enabled=1 这种写法会静默通过。
        self.assertTrue(_knob_matches(True, True))
        self.assertFalse(_knob_matches(1, True))
        self.assertFalse(_knob_matches("true", True))
        self.assertFalse(_knob_matches(None, True))

    def test_numeric_knobs_compare_across_int_and_float_spellings(self) -> None:
        # YAML 里 5 与 5.0 都出现过；按字符串比会把等价配置判成不符。
        self.assertTrue(_knob_matches(5, 5.0))
        self.assertTrue(_knob_matches(20.0, 20))
        self.assertFalse(_knob_matches(5.0, 20.0))
        self.assertFalse(_knob_matches(None, 5))
        self.assertFalse(_knob_matches("abc", 5))
        self.assertTrue(_knob_matches("csi800", "csi800"))
        self.assertFalse(_knob_matches(None, "csi800"))


class InspectabilityIsPureTests(unittest.TestCase):
    """判据必须真的无逐行 I/O（codex #444 r7 与自审并行抓到同一条）。"""

    def test_no_resolve_call_on_the_row_side(self) -> None:
        src = (PROJECT_ROOT / "web" / "operator_ui" / "job_io.py").read_text(
            encoding="utf-8"
        )
        whole = src[
            src.index("def run_dir_is_inspectable") : src.index(
                "def anchored_run_dir"
            )
        ]
        # 只看可执行部分 —— docstring 里正要讲「为什么不能 resolve」。
        code = whole[whole.index('"""', whole.index('"""') + 3) + 3 :]
        # 逐行 resolve() 会走符号链接、真碰盘：本机 3527 行每次渲染 771 ms。
        self.assertNotIn(".resolve()", code)
        self.assertIn("_allowed_root_keys()", code)

    def test_root_keys_are_not_recomputed_per_row(self) -> None:
        # 根只有两个且不随行变化；逐行调用 allowed_output_roots() 等于把
        # 2×N 次 resolve 摊到整轮过滤上（改完行侧后仍剩 409 ms 就是这里）。
        from web.operator_ui import job_io

        calls = 0
        real = job_io.allowed_output_roots

        def counting() -> tuple[Path, ...]:
            nonlocal calls
            calls += 1
            return real()

        job_io._ROOT_KEYS_CACHE = None
        with mock.patch.object(job_io, "allowed_output_roots", counting):
            for _ in range(50):
                job_io.run_dir_is_inspectable("output/runs/x")
        self.assertLessEqual(calls, 1)

    def test_cache_follows_a_patched_boundary(self) -> None:
        # 缓存过期会让判据放行本该拒绝的路径 —— 比慢严重得多。
        import tempfile

        from web.operator_ui import job_io

        self.assertTrue(run_dir_is_inspectable("output/runs/x"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch(
                "web.operator_ui._path_guard._ALLOWED_ROOTS", (root,)
            ):
                self.assertFalse(job_io.run_dir_is_inspectable("output/runs/x"))
                self.assertTrue(job_io.run_dir_is_inspectable(str(root / "a")))
        self.assertTrue(run_dir_is_inspectable("output/runs/x"))

    def test_containment_is_not_a_bare_prefix_match(self) -> None:
        # output_extra 以 output 开头，但不在读边界内。
        self.assertFalse(run_dir_is_inspectable("output_extra/runs/a"))
        self.assertTrue(run_dir_is_inspectable("output"))


class DeadEndRowsTests(unittest.TestCase):
    def test_types_without_a_detail_view_disable_the_action(self) -> None:
        # 本机 117 行里 7 行是 tushare_provider：记录在、产物在，点进去却说
        # 「运行未找到，可能已被删除」—— 那是假消息。
        src = _PAGE_JOBS.read_text(encoding="utf-8")
        self.assertIn("_has_detail_view", src)
        self.assertIn('selected.type in {"pipeline", "walk_forward"}', src)
        self.assertIn("disabled=not _has_detail_view", src)
        # 禁用了还得说清为什么。
        self.assertIn("没有详情视图", src)

    def test_walk_forward_page_always_states_code_provenance(self) -> None:
        # codex #444 r7: git_commit 为 null（续跑混合来源，引擎显式这么标）
        # 或整键缺失时，此前整条代码身份都不渲染 —— 最不可溯源的报告反而
        # 一个字都不说，还照打认证族文案。
        src = _PAGE_WF.read_text(encoding="utf-8")
        self.assertIn('elif "git_commit" in wf_report:', src)
        self.assertIn("无法归属到单个提交", src)
        self.assertIn("未记录", src)


class GovernanceCaptionTests(unittest.TestCase):
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
