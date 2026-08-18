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
    _GOVERNED_FAMILY,
    _knob_matches,
    governed_family_mismatches,
)


def _func_body(source: str, name: str) -> str:
    """源码里某个顶层函数的**本体**（到下一个顶层 def 为止）。

    固定字节窗口会渗进下一个函数的 docstring —— 那正是本文件里一条断言
    误红的原因（相邻函数的注释里正好讲着「为什么不能 resolve」）。
    """
    start = source.index(f"def {name}(")
    nxt = source.find(chr(10) + "def ", start + 1)
    return source[start : nxt if nxt != -1 else len(source)]


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
        # 判据自己不再走一遍锚定/前缀匹配，而是委托 canonical_dir_key，
        # 后者是折叠键用的同一段代码 —— 一处锚定、一处规范化。
        verdict_at = src.index("def run_dir_is_inspectable")
        body = src[verdict_at : src.index("def anchored_run_dir")]
        self.assertIn("canonical_dir_key(", body)
        self.assertNotIn("PROJECT_ROOT / candidate", body)
        self.assertIn("anchored_run_dir(", _func_body(src, "canonical_dir_key"))

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
        # r18 起走 load_all_jobs（翻页翻到 total 为止），不再猜一个大 page_size。
        self.assertIn("load_all_jobs(", src)
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
        official_at = src.index("elif _effective_status == OFFICIAL_METRIC_STATUS")
        self.assertLess(none_at, official_at)

    def test_declared_purpose_can_only_worsen_the_displayed_status(self) -> None:
        # codex #444 r9: status=official 而 purpose=predictions_only 时，此前
        # 先照 status 打 ✓、再补一句中性说明 —— 正好把「声明只能让判定更差」
        # 这条规则反着执行了，非 canonical 的数字被呈现为可用于晋升。
        src = _PAGE_WF.read_text(encoding="utf-8")
        self.assertIn("_effective_status", src)
        # 降级必须在**渲染之前**算出。
        compute_at = src.index("_effective_status = _metric_status")
        render_at = src.index("elif _effective_status == OFFICIAL_METRIC_STATUS")
        self.assertLess(compute_at, render_at)
        # 告警展示的是**生效后**的状态，不是原始 status。
        self.assertIn("⚠ 指标状态:**{_effective_status}**", src)
        self.assertIn("更弱", src)

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
        self.assertIn("_run_id_to_dir.setdefault(_job.run_id,", src)
        self.assertIn("_target_dir = _run_id_to_dir.get(_requested_run_id)", src)
        self.assertIn("if key == _locate or run_options[key]", src)
        # 索引必须**同时**收 UI 作业与 CLI 记录，只收一边等于没修。
        idx_at = src.index("_run_id_to_dir: dict[str, str] = {")
        seed = src[idx_at : idx_at + 400]
        self.assertIn("wf_jobs", seed)

    def test_spec_delta_does_not_encode_anchor_only_governance(self) -> None:
        # codex #444 r3: 归档时 spec delta 里的错话会把治理错误重新引回来
        # —— 比 UI 上的一句话更持久。
        # change 已归档（#444 shipped），delta 已并进主 spec。钉**主 spec**
        # 而不是追归档路径：归档件是历史留档，主 spec 才是活契约。
        spec = (
            PROJECT_ROOT / "openspec" / "specs"
            / "v2-operator-ui-console" / "spec.md"
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
        self.assertIn("governed_family_coverage(_wf_config)", src)
        self.assertIn("_governed = not _mismatched", src)
        # 页面里不得再硬写一份字段清单。
        gov_at = src.index("_mismatched, _unrecorded = governed_family_coverage")
        block = src[gov_at : gov_at + 400]
        for field in ("instruments", "benchmark_code", "rebalance_cadence_days",
                      "rebalance_phase", "slippage_bps"):
            with self.subTest(field=field):
                self.assertNotIn(f'"{field}"', block)
        # 族外必须明说「不给治理判断」，而不是沉默或照旧断言。
        self.assertIn("不属于被治理的 csi800 认证族", src)
        # 而且要说清是**哪一项**不符 —— 只说「不属于」等于让人自己猜。
        self.assertIn("不符项", src)
        # 两条治理文案必须**嵌在** _governed 的 else 分支里。r12 我把「未记录
        # 键」披露写成了同级的第二个 if，anchor 的 elif 就挂到了它身上 —— 一份
        # 完整记录、只是不入族的报告会直接拿到「认证胜者」文案（codex r13）。
        self.assertIn("if not _governed:", src)
        gate_at = src.index("if not _governed:")
        fold_at = src.index('if _anchor == "fold_phase":')
        unrec_at = src.index("if _unrecorded:")
        self.assertLess(gate_at, fold_at)
        # anchor 链必须在「未记录」披露**之前**，且缩进比它深（嵌套的标志）。
        self.assertLess(fold_at, unrec_at)
        self.assertIn('        if _anchor == "fold_phase":', src)
        self.assertNotIn('    elif _anchor == "fold_phase":', src)

    def test_superseded_ids_are_not_aliased_into_the_newest_report(self) -> None:
        # codex #444 r4: r3 把**每个**被覆盖的 id 都塞进别名表，于是点旧行
        # 时 _requested_found=True，绕过告警、静默渲染最新那份报告 ——
        # 等于把 r2 加的告警又废掉了。别名只覆盖同一次调用的 UI/CLI 两个 id。
        # 判据已提到共享层，所以这里钉的是「页面消费的是被覆盖表而不是
        # 别名表」；折叠本身的行为由 CatalogFoldBehaviorTests 真跑一遍。
        src = _PAGE_WF.read_text(encoding="utf-8")
        self.assertIn("_superseded_runs = _folded.superseded_count", src)
        alias_at = src.index("_run_id_to_dir.setdefault(_job.run_id,")
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
        alias_at = src.index("_run_id_to_dir.setdefault(_job.run_id,")
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
        """预设的**解析后**配置（含它 extends 的基座）。

        判据比的是报告 config，那是解析后的形态：`topk` 只写在 config_walk
        基座里，只读原始 preset 会把每个预设都判成不符（codex #444 r10 加入
        topk 之后，raw 扫描从「恰好两个」变成「零个」）。
        """
        import yaml

        path = PROJECT_ROOT / "config" / "presets" / f"{name}.yaml"
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        preset = loaded if isinstance(loaded, dict) else {}
        base_rel = str(preset.get("extends") or "")
        merged: dict[str, object] = {}
        if base_rel:
            base_path = (path.parent / base_rel).resolve()
            if base_path.is_file():
                base = yaml.safe_load(base_path.read_text(encoding="utf-8"))
                if isinstance(base, dict):
                    merged.update(base)
        merged.update(preset)
        # 报告 config 里的路径是**展开后**的（引擎写盘前已解析
        # `${VAR:-default}`）。测试不展开的话，namechange_path 之类会以字面量
        # 形式去比，认证预设自己都判不符 —— 那是测试的失真，不是实现的。
        #
        # 但展开必须**与环境无关**（codex #444 r17）：开发机或 CI 若把
        # QUANT_DELISTED_REGISTRY 导成**空串**，展开结果就是空 —— 这个本该
        # 「认证基线」的夹具自己先出了族，用它做的每条断言都在测错的东西。
        # 实测一个空的 QUANT_DELISTED_REGISTRY 会让五处断言失败。
        # 所以把该 YAML 引用到的所有 ${VAR} 从环境里摘掉，让它落到 tracked
        # 的默认值上 —— 变量名从 YAML 本身扫出来，不是手写清单。
        import os
        import re
        from unittest import mock

        from src.core._yaml_loader import _expand_env_vars_in_tree

        referenced = {
            name
            for value in merged.values()
            if isinstance(value, str)
            for name in re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", value)
        }
        pinned = {k: v for k, v in os.environ.items() if k not in referenced}
        with mock.patch.dict(os.environ, pinned, clear=True):
            expanded = {
                key: (
                    _expand_env_vars_in_tree(value, source_path=path)
                    if isinstance(value, str)
                    else value
                )
                for key, value in merged.items()
            }
        # 再用 dataclass 默认值打底 —— 报告 config 是**完整**的（引擎 dump 整个
        # dataclass），而裸 preset 只有它显式写的那几个键。不打底的话，预设没写
        # 的键全部落进「未记录」而被跳过，于是像 `csi800` 这种只写两三个键的
        # 预设也会「入族」（codex #444 r12 让未记录键不算不符之后的连带效应）。
        from web.operator_ui.pages._walk_forward_helpers import (
            _reported_config_defaults,
        )

        simulated = dict(_reported_config_defaults())
        simulated.update({k: v for k, v in expanded.items() if k in simulated})
        return simulated

    def _governed(self, cfg: dict[str, object]) -> bool:
        return not governed_family_mismatches(cfg)

    def test_cost_convention_is_part_of_the_identity(self) -> None:
        # codex #444 r6: 少了 slippage_bps，csi800_cadence5_base（5 bps 灵敏度
        # 臂）四个旧谓词全中，会被标成「认证胜者」。
        self.assertIn("slippage_bps", _GOVERNED_FAMILY)
        self.assertNotIn("rebalance_anchor", _GOVERNED_FAMILY)  # 族跨两个锚

    def test_identity_matches_the_governance_semantic_keys(self) -> None:
        """入族键集 == 治理钉的 SEMANTIC_KEYS 减去族内区分维度。

        手挑键名漏过三次（r6 slippage / r7 约束 / r10 topk +
        attribution_sleeve_grouping），每次都放一个跑偏的运行顶着「认证胜者」
        被读。所以这里直接对着治理钉断言：将来那边加一个语义字段，这里会红。
        """
        import re

        governance = (
            PROJECT_ROOT / "tests" / "governance"
            / "test_csi800_n5_production_serving.py"
        ).read_text(encoding="utf-8")
        block = re.search(r"SEMANTIC_KEYS = \((.*?)\)", governance, re.S)
        assert block is not None, "治理钉里找不到 SEMANTIC_KEYS"
        semantic = set(re.findall(r'"([a-z_]+)"', block.group(1)))
        # 身份是「认证 preset 链 ∪ 服务参数」的并集，比治理语义键更宽；
        # 但治理钉的每一个语义键（除族内维度）都必须在里面。
        self.assertTrue(
            (semantic - {"rebalance_anchor"}) <= set(_GOVERNED_FAMILY),
            f"治理语义键有遗漏: "
            f"{sorted((semantic - {'rebalance_anchor'}) - set(_GOVERNED_FAMILY))}",
        )

    def test_serving_params_are_the_source_of_their_values(self) -> None:
        # 值也不抄：服务参数里出现的键，身份必须与它逐值相等。
        import yaml

        serving = yaml.safe_load(
            (PROJECT_ROOT / "config" / "serving"
             / "csi800_n5_production.yaml").read_text(encoding="utf-8")
        )
        for key, want in serving.items():
            if key in _GOVERNED_FAMILY:
                with self.subTest(key=key):
                    self.assertEqual(_GOVERNED_FAMILY[key], want)

    def test_experiment_semantics_are_gated_too(self) -> None:
        # codex #444 r11: 只比服务语义时，把 ensemble_window 从 3 改成 1、或换
        # 个模型、换训练窗，仍然零不符项 —— 一个实质不同的实验顶着认证胜者
        # 的文案被读。这些键钉在 preset 链里，必须一并进判据。
        for key, want in (("ensemble_window", 3), ("model_type", "LGBModel"),
                          ("train_months", 24)):
            with self.subTest(key=key):
                self.assertIn(key, _GOVERNED_FAMILY)
                self.assertEqual(_GOVERNED_FAMILY[key], want)

    def test_identity_stays_within_the_report_config_contract(self) -> None:
        # 身份只能包含报告 config 真的会记录的字段：provider_uri / region 是
        # 运行环境参数，报告里没有 —— 把它们算进去，认证运行自己都会因
        # 「缺这两个键」被判出族，标签全灭（本机实测）。
        from web.operator_ui.pages._walk_forward_helpers import (
            _reported_config_defaults,
        )

        reported = set(_reported_config_defaults())
        self.assertTrue(set(_GOVERNED_FAMILY) <= set(reported))
        self.assertNotIn("provider_uri", _GOVERNED_FAMILY)
        self.assertNotIn("region", _GOVERNED_FAMILY)
        # 契约字段用 ast 从源码读，不 import —— import 会把 qlib/gym 拖进这个
        # 号称「纯」的 helper（实测 1.19s / 2042 模块）。
        src = (
            PROJECT_ROOT / "web" / "operator_ui" / "pages"
            / "_walk_forward_helpers.py"
        ).read_text(encoding="utf-8")
        self.assertIn("ast.parse", src)
        self.assertNotIn("from src.core.walk_forward.config import", src)

    def test_unreadable_authority_fails_loud_not_open(self) -> None:
        # 退化成空要求会让**每个**运行都被判入族 —— 恰恰在权威读不到的时候
        # 乱发认证族标签。方向必须相反：宁可整页报错。
        from unittest import mock

        from web.operator_ui.pages import _walk_forward_helpers as H

        for bad in ("", "- a" + chr(10) + "- b" + chr(10), "42" + chr(10)):
            with self.subTest(payload=bad[:6]):
                with mock.patch.object(
                    Path, "read_text", return_value=bad
                ), self.assertRaises(H.GovernedFamilyUnavailableError):
                    H._load_governed_family()

    def test_topk_and_sleeve_grouping_gate_the_labels(self) -> None:
        # codex #444 r10: 换掉 topk 就是另一个组合，换掉 sleeve 归组就是另一套
        # 归因口径 —— 两者都不该继续顶着认证族文案。
        cfg = self._preset("csi800_cadence5_conservative")
        self.assertEqual(governed_family_mismatches(cfg), [])
        for key, bad in (("topk", 30), ("attribution_sleeve_grouping", False)):
            with self.subTest(key=key):
                broken = dict(cfg)
                broken[key] = bad
                self.assertEqual(governed_family_mismatches(broken), [key])

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
        # 全预设扫一遍（各自与其 extends 基座合并后）：任何新预设若意外入族，
        # 这里会响。
        import yaml

        governed = []
        for path in sorted((PROJECT_ROOT / "config" / "presets").glob("*.yaml")):
            try:
                loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError:
                continue
            if not isinstance(loaded, dict):
                continue
            try:
                resolved = self._preset(path.stem)
            except Exception:
                # 有的战役预设引用了未设默认值的环境变量（如
                # ${PV_PROMO_POOL_DIR}），本机解析不出来 —— 解析不出来的
                # 配置本就无从判定入族，跳过而不是让扫描崩掉。
                continue
            if not self._governed(resolved):
                continue
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

    def test_constraint_switches_are_compared_by_value(self) -> None:
        # r7 起这两把钥匙直接来自生产服务参数（不再由 EVAL_PROFILES 的
        # campaign_constraints 语义开关手工映射）—— 少了任一把，一个关掉风控
        # 约束的运行就会顶着认证族文案被读。
        self.assertEqual(_GOVERNED_FAMILY["risk_constraints_enabled"], True)
        self.assertEqual(
            _GOVERNED_FAMILY["risk_constraints_calibration"], "campaign_v1"
        )

    def test_booleans_do_not_collapse_into_numbers(self) -> None:
        # bool 是 int 的子类：不特判的话 True 会等于 1.0，
        # risk_constraints_enabled=1 这种写法会静默通过。
        self.assertTrue(_knob_matches(True, True))
        self.assertFalse(_knob_matches(1, True))
        self.assertFalse(_knob_matches("true", True))
        self.assertFalse(_knob_matches(None, True))

    def test_dataclass_defaults_are_part_of_the_identity(self) -> None:
        # codex #444 r12: 只取 YAML 的话，它没写的字段（运行时落 dataclass
        # 默认值）是盲区 —— label_horizon_days 从 1 改成 5 是实质不同的实验，
        # 却零不符项。默认值必须一起进身份。
        for key, want in (
            ("label_horizon_days", 1),
            ("risk_constraints_mode", "raise"),
            ("metrics_purpose", "official"),
            ("seed", 42),
        ):
            with self.subTest(key=key):
                self.assertIn(key, _GOVERNED_FAMILY)
                self.assertEqual(_GOVERNED_FAMILY[key], want)

    def test_none_defaults_do_not_read_as_mismatches(self) -> None:
        # str(None or "") 是 ""，str(None) 是 "None" —— 落到字符串比会把
        # None==None 判成不符，认证运行自己因此出族（实测四个键）。
        self.assertTrue(_knob_matches(None, None))
        self.assertFalse(_knob_matches("x", None))
        self.assertFalse(_knob_matches(0, None))

    def test_unrecorded_keys_are_disclosed_not_counted_as_mismatch(self) -> None:
        # 报告没记的键（该字段落地之前的运行）不算不符 —— 否则每一份历史报告
        # 都出族，功能等于删掉。但也不能静默：页面必须说出覆盖了几个、缺几个。
        from web.operator_ui.pages._walk_forward_helpers import (
            governed_family_coverage,
        )

        cfg = self._preset("csi800_cadence5_conservative")
        # topk 的契约默认值与认证值相同 → 缺键不算不符（历史报告不因「后加的
        # 字段」被整批踢出），但仍要出现在未记录清单里。
        trimmed = {k: v for k, v in cfg.items() if k != "topk"}
        bad, unrecorded = governed_family_coverage(trimmed)
        self.assertNotIn("topk", bad)
        self.assertIn("topk", unrecorded)
        # 有键但值不同,仍然算不符。
        wrong = dict(cfg)
        wrong["topk"] = 30
        self.assertIn("topk", governed_family_coverage(wrong)[0])
        src = _PAGE_WF.read_text(encoding="utf-8")
        self.assertIn("_unrecorded", src)
        self.assertIn("本报告未记录", src)

    def test_identity_does_not_move_with_the_viewer_environment(self) -> None:
        # codex #444 r13: 权威里的数据源路径写成 ${QUANT_*:-...}。若按**看页面
        # 那个进程**的环境再展开一次，同一份报告会因为 UI 起在别的机器上而
        # 突然「换族」、丢掉治理标签。
        import importlib
        import os
        from unittest import mock

        from web.operator_ui.pages import _walk_forward_helpers as H

        env_keys = H._environment_dependent_keys()
        self.assertTrue(env_keys, "环境相关键判定失效了")
        # codex #444 r14: 这些键**留在**身份里，只是改比「配没配」—— 整条踢出
        # 会放过 delisted_registry_path=""（那会关掉 PIT provider、退回 legacy
        # WARN 掩码，是另一套语义）。
        for key in env_keys:
            with self.subTest(key=key):
                self.assertIn(key, H._PRESENCE_ONLY_KEYS)

        # 用 **tracked** 的预设链构造报告形态。`output/` 整棵树都不入库
        # （.gitignore:20），拿盘上的真实报告做夹具会让整条用例在 CI 里
        # skip —— 我刚修的回归就会完全没有覆盖（codex #444 r16）。
        cfg = self._preset("csi800_cadence5_conservative")
        before = H.governed_family_coverage(cfg)
        try:
            with mock.patch.dict(
                os.environ,
                {
                    "QUANT_NAMECHANGE_PATH": "E:/elsewhere/names.parquet",
                    "QUANT_DELISTED_REGISTRY": "E:/elsewhere/reg.parquet",
                    "QUANT_PROVIDER_URI": "E:/elsewhere/bundle",
                },
            ):
                reloaded = importlib.reload(H)
                self.assertEqual(before, reloaded.governed_family_coverage(cfg))
        finally:
            # 还原模块状态,免得污染后续用例（本文件曾被这类 reload 咬过）。
            importlib.reload(H)

    def test_empty_pit_registry_is_not_the_certified_family(self) -> None:
        # codex #444 r14: 空 delisted_registry_path 是合法配置，但它关掉 PIT
        # provider、退回 legacy WARN 掩码 —— 掩码/归因语义不同，不能顶着认证
        # 胜者的文案。路径**字面量**不比（随机器变），「配没配」必须比。
        from web.operator_ui.pages import _walk_forward_helpers as H

        # 用 **tracked** 的预设链构造报告形态。`output/` 整棵树都不入库
        # （.gitignore:20），拿盘上的真实报告做夹具会让整条用例在 CI 里
        # skip —— 我刚修的回归就会完全没有覆盖（codex #444 r16）。
        cfg = self._preset("csi800_cadence5_conservative")
        self.assertEqual(H.governed_family_coverage(cfg)[0], [])
        for blank in ("", "   "):
            with self.subTest(value=repr(blank)):
                broken = dict(cfg)
                broken["delisted_registry_path"] = blank
                self.assertIn(
                    "delisted_registry_path",
                    H.governed_family_coverage(broken)[0],
                )
        # 但换成**另一条**已配置的路径不算不符 —— 那只是机器差异。
        moved = dict(cfg)
        moved["delisted_registry_path"] = "E:/elsewhere/registry.parquet"
        self.assertEqual(H.governed_family_coverage(moved)[0], [])

    def test_absent_keys_are_judged_by_their_runtime_default(self) -> None:
        # codex #444 r15: 缺键不等于「无从判断」—— 那次运行当时就跑在契约默认
        # 值上。早于 delisted_registry_path 的报告缺这个键，意味着它按默认 ''
        # 跑：PIT provider 关闭、legacy WARN 掩码，与认证运行语义不同，是真正
        # 的不符，不能只记一句「未记录」就放行。
        from web.operator_ui.pages import _walk_forward_helpers as H

        # 用 **tracked** 的预设链构造报告形态。`output/` 整棵树都不入库
        # （.gitignore:20），拿盘上的真实报告做夹具会让整条用例在 CI 里
        # skip —— 我刚修的回归就会完全没有覆盖（codex #444 r16）。
        cfg = self._preset("csi800_cadence5_conservative")
        defaults = H._reported_config_defaults()
        # 默认值与认证值**不同**的键：缺了就是不符。
        for key in ("delisted_registry_path", "namechange_path"):
            with self.subTest(key=key, kind="differs"):
                self.assertFalse(H._is_configured(defaults.get(key)))
                trimmed = {k: v for k, v in cfg.items() if k != key}
                bad, unrecorded = H.governed_family_coverage(trimmed)
                self.assertIn(key, bad)
                self.assertIn(key, unrecorded)
        # 默认值与认证值**相同**的键：缺了不算不符，只披露。
        for key in ("risk_constraints_mode", "metrics_purpose"):
            with self.subTest(key=key, kind="same"):
                trimmed = {k: v for k, v in cfg.items() if k != key}
                bad, unrecorded = H.governed_family_coverage(trimmed)
                self.assertNotIn(key, bad)
                self.assertIn(key, unrecorded)

    def test_environment_dependence_is_derived_not_hand_listed(self) -> None:
        # 判据是「权威值本身长成 ${...} 模板」—— 手挑键名正是这条规则存在的
        # 原因（同类已经漏过四次）。
        from web.operator_ui.pages._walk_forward_helpers import (
            _is_environment_dependent,
        )

        self.assertTrue(_is_environment_dependent("${QUANT_X:-/d/x}"))
        self.assertTrue(_is_environment_dependent("${QUANT_X}"))
        self.assertFalse(_is_environment_dependent("D:/qlib_data/x.parquet"))
        self.assertFalse(_is_environment_dependent(50))
        self.assertFalse(_is_environment_dependent(None))

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
        # 根键只在 canonical_dir_key 里取一次（判据委托给它）。
        canon = _func_body(src, "canonical_dir_key")
        self.assertIn("_allowed_root_keys()", canon)
        self.assertNotIn(".resolve()", canon)

    def test_root_keys_are_not_recomputed_per_row(self) -> None:
        # 根只有两个且不随行变化；逐行调用 allowed_output_roots() 等于把
        # 2×N 次 resolve 摊到整轮过滤上（改完行侧后仍剩 409 ms 就是这里）。
        from web.operator_ui import job_io

        calls = 0
        real = job_io.allowed_output_roots

        def counting(*, resolve: bool = True) -> tuple[Path, ...]:
            nonlocal calls
            calls += 1
            return real(resolve=resolve)

        job_io._ROOT_KEYS_CACHE = None
        with mock.patch.object(job_io, "allowed_output_roots", counting):
            for _ in range(50):
                job_io.run_dir_is_inspectable("output/runs/x")
        # 一轮里取词法+解析两种拼写 = 2 次；关键是**与行数无关**。
        self.assertLessEqual(calls, 2)

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

    def test_a_symlinked_root_does_not_discard_every_row(self) -> None:
        # codex #444 r8: 候选侧是纯词法，而 allowed_output_roots() 会
        # resolve() 根 —— 若 output 本身是符号链接/联接，解析后的根变成挂载
        # 目标，词法候选一条都对不上，整份目录记录被判不可检视，而产物守卫
        # 却接受同一条路径。本机用 mklink /J 复现过（symlink 需特权，
        # junction 不需要）。
        import subprocess
        import tempfile

        from web.operator_ui import job_io
        from web.operator_ui._path_guard import guard_output_path

        with tempfile.TemporaryDirectory() as tmp:
            # **必须 resolve**：GitHub 的 Windows runner 把 TEMP 设成 8.3 短名
            # (`C:\Users\RUNNER~1\...`)，于是 link.resolve() 展开成长名，而用
            # 短名 base 拼出的 real/runs/a 就成了**第三种拼写** —— 两个候选活在
            # 不同命名空间里，断言测的就不是它想测的东西了（CI 实测红）。
            base = Path(tmp).resolve()
            real = base / "real_volume"
            (real / "runs").mkdir(parents=True)
            link = base / "output_link"
            try:
                link.symlink_to(real, target_is_directory=True)
            except (OSError, NotImplementedError):
                if sys.platform != "win32":
                    self.skipTest("本机无法建立符号链接")
                made = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(real)],
                    capture_output=True, text=True,
                )
                if made.returncode != 0 or not link.exists():
                    self.skipTest("本机无法建立目录联接")
            if link.resolve() == Path(os.path.normpath(str(link))):
                self.skipTest("该文件系统上根的解析形与词法形相同")
            job_io._ROOT_KEYS_CACHE = None
            try:
                with mock.patch(
                    "web.operator_ui._path_guard._ALLOWED_ROOTS", (link,)
                ):
                    # 两种拼写都必须与产物守卫给出同一个答案。
                    for label, candidate in (
                        ("链接拼写", link / "runs" / "a"),
                        ("解析拼写", real / "runs" / "a"),
                    ):
                        with self.subTest(spelling=label):
                            guard_output_path(candidate)  # 守卫接受 → 不抛
                            self.assertTrue(
                                job_io.run_dir_is_inspectable(str(candidate))
                            )
                    # 边界之外仍然拒绝。
                    self.assertFalse(
                        job_io.run_dir_is_inspectable(str(base / "elsewhere"))
                    )
                    # codex #444 r9: 准入放宽之后，折叠键若仍是纯词法，两种
                    # 拼写就成了两条运行 —— 同一份产物出现两个选择器条目，
                    # 被覆盖的历史行又能静默渲染当前报告。必须折成一条。
                    self.assertEqual(
                        job_io.canonical_dir_key(str(link / "runs" / "a")),
                        job_io.canonical_dir_key(str(real / "runs" / "a")),
                    )
                    folded = job_io.fold_catalog_by_dir([
                        JobSummary(
                            run_id="newest", type="walk_forward",
                            status="completed", source="cli",
                            run_dir=str(link / "runs" / "a"),
                        ),
                        JobSummary(
                            run_id="older", type="walk_forward",
                            status="completed", source="cli",
                            run_dir=str(real / "runs" / "a"),
                        ),
                    ])
                    self.assertEqual(
                        [r.run_id for r in folded.newest], ["newest"]
                    )
                    self.assertIn("older", folded.superseded_dir_of_run)
            finally:
                job_io._ROOT_KEYS_CACHE = None

    def test_a_third_spelling_is_set_aside_by_design(self) -> None:
        """别名拼写被搁置是**设计**，不是 bug —— 钉住它，别让它被误当缺陷修掉。

        判据是纯词法（无逐行 I/O：3527 行 771ms → 23ms 那条 SHALL）。它看得穿
        「根的拼写」与「根的解析形」这两种，但看不穿**第三种**拼写（另一个
        联接 / 8.3 短名 / 另一条符号链接指向同一目录）。那种行会被判不可检视
        并计入搁置数 —— **假阴性**，安全方向，且页面有报数。反过来（把边界外
        的行放进来）才是不可接受的。

        CI 上那次红就是这个形态被夹具无意撞上的：runner 的 TEMP 是 8.3 短名。
        """
        import subprocess
        import tempfile

        from web.operator_ui import job_io
        from web.operator_ui._path_guard import guard_output_path

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            real = base / "real_volume"
            (real / "runs").mkdir(parents=True)
            link, alias = base / "output_link", base / "third_spelling"
            for path in (link, alias):
                try:
                    path.symlink_to(real, target_is_directory=True)
                except (OSError, NotImplementedError):
                    if sys.platform != "win32":
                        self.skipTest("本机无法建立符号链接")
                    made = subprocess.run(
                        ["cmd", "/c", "mklink", "/J", str(path), str(real)],
                        capture_output=True, text=True,
                    )
                    if made.returncode != 0 or not path.exists():
                        self.skipTest("本机无法建立目录联接")
            if link.resolve() == Path(os.path.normpath(str(link))):
                self.skipTest("该文件系统上根的解析形与词法形相同")
            job_io._ROOT_KEYS_CACHE = None
            try:
                with mock.patch(
                    "web.operator_ui._path_guard._ALLOWED_ROOTS", (link,)
                ):
                    third = alias / "runs" / "a"
                    # 守卫（会 resolve）接受它……
                    guard_output_path(third)
                    # ……而词法判据搁置它。两者**不一致是有意的**。
                    self.assertFalse(job_io.run_dir_is_inspectable(str(third)))
            finally:
                job_io._ROOT_KEYS_CACHE = None

    def test_root_definition_lives_in_one_place(self) -> None:
        # 词法根若在 job_io 里另抄一份，两处会分叉 —— 这正是本 PR 反复
        # 修的那一类。定义只留在 _path_guard，用 resolve=False 取词法形。
        src = (PROJECT_ROOT / "web" / "operator_ui" / "job_io.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("allowed_output_roots(resolve=", src)
        self.assertNotIn('PROJECT_ROOT / "output"', src)

    def test_verdict_and_fold_key_share_one_implementation(self) -> None:
        # r1 是锚点分叉、r9 是规范化分叉 —— 同一类修了两次。判据现在直接
        # 复用 canonical_dir_key，不再自己走一遍前缀匹配。
        src = (PROJECT_ROOT / "web" / "operator_ui" / "job_io.py").read_text(
            encoding="utf-8"
        )
        whole = src[
            src.index("def run_dir_is_inspectable") : src.index(
                "def anchored_run_dir"
            )
        ]
        code = whole[whole.index('"""', whole.index('"""') + 3) + 3 :]
        self.assertIn("canonical_dir_key(run_dir) is not None", code)
        self.assertNotIn("startswith(root", code)

    def test_containment_is_not_a_bare_prefix_match(self) -> None:
        # output_extra 以 output 开头，但不在读边界内。
        self.assertFalse(run_dir_is_inspectable("output_extra/runs/a"))
        self.assertTrue(run_dir_is_inspectable("output"))


class LoadAllJobsTests(unittest.TestCase):
    """详情页必须看到目录的**每一行**（codex #444 r18）。

    此前三处调用各自写死 `page_size=100_000` —— 那是**猜**一个够大的数字，
    猜错了没有任何提示：超出的行静默消失，而作业页能翻到它们，于是点进去
    是「运行未找到」，正是本 change 要消灭的死链。
    """

    def test_matches_the_filtered_total(self) -> None:
        from web.operator_ui.job_io import load_all_jobs

        for kind in ("walk_forward", "pipeline"):
            with self.subTest(kind=kind):
                rows = load_all_jobs(type_filter=kind, source_filter="cli")
                _, total, _ = list_all_jobs(
                    type_filter=kind, source_filter="cli", page=1, page_size=1,
                )
                self.assertEqual(len(rows), total)

    def test_really_walks_past_the_first_page(self) -> None:
        # 本机目录只有一百多行，一页就装下了 —— 不把步长压到 1，这条用例
        # 根本走不到翻页分支，等于空转。
        from web.operator_ui import job_io

        _, total, _ = list_all_jobs(page=1, page_size=1)
        if total < 3:
            self.skipTest("本机目录行数太少，翻页分支无从触发")
        calls: list[int] = []
        real = job_io.list_all_jobs

        def counting(**kw: object) -> tuple[list[JobSummary], int, int]:
            calls.append(int(kw.get("page", 1)))  # type: ignore[arg-type]
            return real(**kw)  # type: ignore[arg-type]

        with mock.patch.object(job_io, "_PAGE_STRIDE", 1), mock.patch.object(
            job_io, "list_all_jobs", counting
        ):
            rows = job_io.load_all_jobs()
        self.assertEqual(len(rows), total)
        self.assertGreater(len(calls), 1, "没有真的翻页")
        self.assertEqual(calls, list(range(1, len(calls) + 1)))

    def test_short_read_fails_loud_instead_of_truncating(self) -> None:
        # 拿不齐就抛 —— 静默截断正是 100_000 那个写法的病根。
        from web.operator_ui import job_io

        def truncating(**kw: object) -> tuple[list[JobSummary], int, int]:
            return ([], 5, 0)

        with mock.patch.object(job_io, "list_all_jobs", truncating):
            with self.assertRaises(RuntimeError) as caught:
                job_io.load_all_jobs()
        self.assertIn("0/5", str(caught.exception))

    def test_no_page_reaches_for_a_guessed_ceiling(self) -> None:
        # 钉的是**调用形式**，不是字面量的任何出现 —— 注释里讲这段历史是
        # 允许的，钉太宽会逼着后来人把解释删掉。
        for page in (_PAGE_JOBS, _PAGE_WF, _PAGE_RESULTS):
            with self.subTest(page=page.name):
                src = page.read_text(encoding="utf-8")
                self.assertNotIn("page_size=100_000", src)
                self.assertIn("load_all_jobs(", src)


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
