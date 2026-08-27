"""篮子与它下游那几处约束的**同一性**，以及三个来源页的接线。

篮子的上界、可比类型、最小数量都是**别处**定下的规则的副本。副本不钉住
就会分叉：放宽了对比页的 ``max_selections`` 而篮子没跟上，操作人攒到第 6
个再跳转，会撞上 URL 守卫的静默拒绝——一个什么也不说的空页。
"""

from __future__ import annotations

import ast
import re
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from web.operator_ui._param_guard import sanitize
from web.operator_ui.compare_basket import (
    COMPARABLE_TYPES,
    MAX_BASKET_SIZE,
    MIN_COMPARE_SIZE,
    basket_query_value,
)
from web.operator_ui.compare_basket_widget import COMPARISON_PAGE

_COMPARISON_PAGE = Path("web/operator_ui/pages/research_run_comparison.py")
_COMPARISON_HELPERS = Path(
    "web/operator_ui/pages/_research_run_comparison_helpers.py")
_JOBS_PAGE = Path("web/operator_ui/pages/jobs.py")
_RESULTS_PAGE = Path("web/operator_ui/pages/results.py")
_RESULTS_RENDER = Path("web/operator_ui/pages/_results_render.py")
_WALK_FORWARD_PAGE = Path("web/operator_ui/pages/walk_forward.py")


@dataclass(frozen=True)
class _Row:
    run_id: str
    type: str = "pipeline"
    run_dir: str = "output/runs/x"


class BasketMatchesItsDownstreamConstraintsTests(unittest.TestCase):
    def test_a_full_basket_passes_the_url_whitelist(self) -> None:
        # 钉的是**真跑守卫**,不是抄一个数字:`_param_guard` 的上界改了,
        # 这里立刻红。
        full = basket_query_value(
            tuple(f"run-{i}" for i in range(MAX_BASKET_SIZE)))

        self.assertEqual(sanitize("run_ids", full, default=""), full)

    def test_one_over_the_basket_ceiling_is_rejected_by_the_url_guard(
        self,
    ) -> None:
        over = basket_query_value(
            tuple(f"run-{i}" for i in range(MAX_BASKET_SIZE + 1)))

        # 守卫拒绝时回落到 default —— 也就是篮子会被静默丢掉。这正是上界
        # 必须与它对齐的原因。
        self.assertEqual(sanitize("run_ids", over, default=""), "")

    def test_the_comparison_page_selection_bounds_match(self) -> None:
        source = _COMPARISON_PAGE.read_text(encoding="utf-8")

        self.assertIn(f"max_selections={MAX_BASKET_SIZE}", source)
        self.assertIn(
            f"if not {MIN_COMPARE_SIZE} <= len(selected_ids) "
            f"<= {MAX_BASKET_SIZE}:",
            source,
        )

    def test_comparable_types_match_the_catalog_filter(self) -> None:
        # `selectable_catalog` 的 allowed_types 决定哪些运行进得了对比页的
        # 目录。抄错的话按钮会对一个永远进不去的运行显示可用。
        source = _COMPARISON_HELPERS.read_text(encoding="utf-8")
        match = re.search(r"allowed_types = \{([^}]*)\}", source)
        assert match is not None, "selectable_catalog 的 allowed_types 不见了"
        declared = {
            literal.strip().strip('"\'')
            for literal in match.group(1).split(",")
            if literal.strip()
        }

        self.assertEqual(declared, set(COMPARABLE_TYPES))

    def test_the_comparison_page_link_target_exists(self) -> None:
        # `st.page_link` 的路径写错不会报错,只会渲染成一个死链。
        self.assertTrue(
            Path("web/operator_ui") .joinpath(COMPARISON_PAGE).exists(),
            f"{COMPARISON_PAGE} 不存在",
        )

    def test_the_comparison_page_reads_the_run_ids_param(self) -> None:
        source = _COMPARISON_PAGE.read_text(encoding="utf-8")

        self.assertIn('st.query_params.get("run_ids"', source)


class SourcePageWiringTests(unittest.TestCase):
    """三个来源页都要接，且都走**同一个**入口。

    目录加载与参数传递集中在 ``render_compare_basket_controls`` 里：让每页
    自己拼，等于把「传全量目录行还是只传当前所有者」这个坑挖三遍——其中两遍
    已经在评审里被抓到过一次。
    """

    def test_the_shared_entry_point_forwards_the_full_catalog(self) -> None:
        """**真跑**共享入口，看它把什么转发下去。

        源码串证明不了「转发的是哪个值」——变异只要在 catalog 之后补一行
        ``all_rows = catalog.rows``，每一条串守卫都照样命中，而语义已经反转
        （实测逃逸）。只传当前所有者的话，「被同目录的更新运行接管」会退化成
        「目录里根本没有这条」，分因就没了，操作人只剩一句「不可用」。
        """
        import web.operator_ui.compare_basket_widget as widget

        owner = _Row("owner-1")
        superseded = _Row("old-1")
        catalog = SimpleNamespace(
            rows=(owner,), run_id_alias={"cli-1": "owner-1"})
        seen: dict[str, Any] = {}

        def _fake_load() -> list[object]:
            return [owner, superseded]

        def _capture(*args: Any, **kwargs: Any) -> None:
            seen.setdefault(kwargs.get("key_prefix", "?"), []).append(kwargs)

        with mock.patch.object(widget, "load_all_jobs_read_only", _fake_load), \
                mock.patch.object(
                    widget, "selectable_catalog", lambda rows: catalog), \
                mock.patch.object(
                    widget, "render_add_to_basket_button", _capture), \
                mock.patch.object(widget, "render_basket_panel", _capture):
            view = widget.render_add_to_basket("owner-1", key_prefix="t")
            widget.render_basket(view, key_prefix="t")

        forwarded = seen["t"]
        self.assertEqual(len(forwarded), 2, "按钮与面板都要收到目录")
        for call in forwarded:
            self.assertEqual(
                list(call["all_rows"]), [owner, superseded],
                "转发的必须是**全量**目录行，不是 catalog.rows",
            )
            self.assertEqual(list(call["selectable_ids"]), ["owner-1"])
            self.assertEqual(call["run_id_alias"], catalog.run_id_alias)

    def test_already_in_basket_is_judged_on_the_current_resolution(
        self,
    ) -> None:
        """「已在篮子里」要在**同一个解析状态**上比。

        篮子存的是**加入当时**解析出来的 id。目录归属后来变了（例如同一份
        产物的 UI/CLI 镜像证明变成成立，所有者从 ``cli-1`` 翻成 ``ui-9``），
        同一次运行的新解析 id 与旧存的 id 就不是同一个串。直接比
        ``resolved in basket`` 会判「不在」⇒ 按钮可用 ⇒ **同一次运行被加进
        去两次** ⇒ 复核时两者坍塌成同一所有者 ⇒ 链接被重复检查挡住，而操作
        人看到的是两行不同的 id，无从知道它们是同一次运行。

        真跑按钮渲染，断言它认得出「这就是篮子里那一个」。
        """
        import web.operator_ui.compare_basket_widget as widget

        owner = _Row("ui-9")
        seen: dict[str, object] = {}

        def _fake_button(_label: str, **kwargs: Any) -> bool:
            seen["disabled"] = kwargs.get("disabled")
            return False

        session = {"research_compare_basket": ["cli-1"]}
        with mock.patch.object(widget, "st", mock.Mock(
                session_state=session, button=_fake_button)):
            widget.render_add_to_basket_button(
                "ui-9",
                selectable_ids=["ui-9"],
                run_id_alias={"cli-1": "ui-9"},
                all_rows=[owner],
                key_prefix="t",
            )

        self.assertTrue(
            seen["disabled"],
            "篮子里存的 cli-1 现在解析成 ui-9，按钮该判为「已在篮子里」",
        )

    def test_the_standalone_panel_reads_nothing_when_the_basket_is_empty(
        self,
    ) -> None:
        # 作业页无选中行时走这条路径。空篮子还去读一遍全量目录，等于给每次
        # 无选中行的渲染加一次无谓的读盘。
        import web.operator_ui.compare_basket_widget as widget

        calls: list[int] = []

        with mock.patch.object(widget, "st", mock.Mock(session_state={})), \
                mock.patch.object(
                    widget, "load_all_jobs_read_only",
                    lambda: calls.append(1) or []):
            widget.render_standalone_basket(key_prefix="t")

        self.assertEqual(calls, [])

    def test_the_shared_entry_point_reads_the_catalog_once(self) -> None:
        # 每页自己读一次是这个坑被挖三遍的原因;读两次也说明有人又把加载
        # 复制了一份。
        import web.operator_ui.compare_basket_widget as widget

        calls = []

        def _fake_load() -> list[object]:
            calls.append(1)
            return []

        with mock.patch.object(widget, "load_all_jobs_read_only", _fake_load), \
                mock.patch.object(
                    widget, "selectable_catalog",
                    lambda rows: SimpleNamespace(rows=(), run_id_alias={})), \
                mock.patch.object(
                    widget, "render_add_to_basket_button", lambda *a, **k: None), \
                mock.patch.object(
                    widget, "render_basket_panel", lambda *a, **k: None):
            view = widget.render_add_to_basket("x", key_prefix="t")
            widget.render_basket(view, key_prefix="t")

        self.assertEqual(len(calls), 1)

    def test_jobs_page_offers_the_basket(self) -> None:
        source = _JOBS_PAGE.read_text(encoding="utf-8")

        self.assertIn(
            "            _basket_catalog = render_add_to_basket(\n"
            '                selected.run_id, key_prefix="jobs")\n',
            source,
        )
        # 面板必须画在动作列**之外**（挤进三分之一列宽没法读），**也要在
        # 「选中某一行」之外**——这张表默认没有选中行，挂在里面的话操作人
        # 从别的页攒好篮子切过来会看到「篮子不见了」，随便点中任意一行它
        # 才回来。两个位置的源码串一模一样，所以钉的是**缩进**：模块级的
        # 那一层（无前导空格）才是选中块之外。
        self.assertIn(
            '\nif _selected_row is not None and 0 <= _selected_row < len(items):\n'
            '    render_basket(_basket_catalog, key_prefix="jobs")\n'
            "else:\n"
            '    render_standalone_basket(key_prefix="jobs")\n',
            source,
        )
        # 没有选中行时也要能画——那条路径没有加入按钮先读过目录。
        self.assertIn("render_standalone_basket", source)
        # 动作栏多了一列。旧的 2/3 列布局会把新按钮挤到别的动作上面。
        self.assertIn(
            "act_open, act_copy, act_compare, act_stop = st.columns(4)",
            source,
        )
        self.assertIn(
            "act_open, act_copy, act_compare = st.columns(3)", source)

    def test_the_results_basket_renders_before_the_autorefresh_rerun(
        self,
    ) -> None:
        """篮子必须排在自动刷新的 ``st.rerun()`` **之前**。

        运行中的作业勾了「每 5 秒自动刷新」之后，那个 ``st.rerun()`` 会抛
        ``RerunException`` 立刻终止本帧——挂在它后面的篮子从此一帧都画不出
        来：加入按钮没了，从别的页攒进去的篮子也整个消失，操作人会以为篮子
        丢了。而「运行中」正是这一页最常驻留的状态（典型 pipeline 跑
        1-8 小时，页面自己的注释就这么写）。

        按**源码位置**钉：这一页的早退是 `st.rerun()`，不是 return，源码序
        就是执行序。
        """
        source = _RESULTS_PAGE.read_text(encoding="utf-8")

        basket_at = source.index("_basket_catalog = render_add_to_basket(")
        panel_at = source.index('render_basket(_basket_catalog, key_prefix="results")')
        for later in (
            '        key="results_autorefresh",',
            "            _time.sleep(5)\n            st.rerun()",
            '    if mode == "pipeline" or pipeline_report:',
        ):
            with self.subTest(later=later.strip()[:40]):
                self.assertLess(basket_at, source.index(later))
                self.assertLess(panel_at, source.index(later))

    def test_results_page_offers_the_basket_in_both_modes(self) -> None:
        # 挂在 `_render_header_actions` 里只有 pipeline 分支会调,本页接受
        # 并展示的 walk_forward 运行就既没有加入按钮、也看不到已有的篮子。
        # 判据要放在**所有**入口都必经的位置(codex P2 on #472)。
        source = _RESULTS_PAGE.read_text(encoding="utf-8")

        self.assertIn(
            "    _basket_catalog = render_add_to_basket(\n"
            '        selected_job_id, key_prefix="results")\n'
            '    render_basket(_basket_catalog, key_prefix="results")\n',
            source,
        )
        # 必须在模式分支**之前**。分支之后放两份就又回到「两条路径各写一
        # 遍」,而那正是这条意见的来源。
        control_at = source.index("_basket_catalog = render_add_to_basket(")
        branch_at = source.index('    if mode == "pipeline" or pipeline_report:')
        self.assertLess(control_at, branch_at)
        self.assertEqual(source.count("render_add_to_basket("), 1)
        self.assertEqual(source.count("render_basket(_basket_catalog"), 1)
        # 旧位置(pipeline 专属的动作栏)不许留残余。
        render_source = _RESULTS_RENDER.read_text(encoding="utf-8")
        self.assertNotIn("compare_basket", render_source)

    def test_walk_forward_page_offers_the_basket(self) -> None:
        source = _WALK_FORWARD_PAGE.read_text(encoding="utf-8")

        self.assertIn(
            "        _basket_catalog = render_add_to_basket(\n"
            '            _wf_selected_run_id, key_prefix="wf")\n',
            source,
        )
        # 面板画在 1:3 动作列**之外**——挤进那四分之一宽会没法读。
        self.assertIn(
            '\n    render_basket(_basket_catalog, key_prefix="wf")\n', source)
        # 本页的「当前运行」是目录键映射出来的 id,不是 selectbox 的返回值。
        self.assertIn(
            '_wf_selected_run_id = str(run_options.get(str(selected), "")'
            ' or "")\n',
            source,
        )
        # 取不到 id 时**不画**按钮:画一个注定被拒的按钮就是把人送进拒绝页。
        self.assertIn("\nif _wf_selected_run_id:\n", source)
        # 位置必须在读产物的**每一条早退之前**。`if not folds:` 那条早退
        # （运行中 / 部分完成 / 空运行）会 `st.stop()`——挂在它后面的话,恰恰
        # 是最想「攒起来待会儿比」的那些运行既没有加入按钮、也看不到已有的
        # 篮子（codex P2 on #472 r2）。
        basket_at = source.index("if _wf_selected_run_id:")
        for early_exit in (
            "    wf_report = read_walk_forward_report(run_dir)",
            "if not folds:",
            '        "暂无单折数据",',
        ):
            with self.subTest(early_exit=early_exit):
                self.assertLess(basket_at, source.index(early_exit))
        # 也必须在**运行选定之后**——没选中运行时没有 id 可加入。
        self.assertLess(
            source.index("run_dir = Path(_dir_display.get("), basket_at)


class WidgetJudgesBeforeTheClickTests(unittest.TestCase):
    """准入必须在**按下之前**判好。

    等按下去再报错，等于把操作人送进一次注定失败的交互——那正是对比页
    ``st.stop()`` 那条路径的翻版，只是换了个地方发生。
    """

    def setUp(self) -> None:
        self.source = Path(
            "web/operator_ui/compare_basket_widget.py"
        ).read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def _function(self, name: str) -> ast.FunctionDef:
        return next(
            node
            for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        )

    def test_admission_is_computed_before_the_button(self) -> None:
        function = self._function("render_add_to_basket_button")
        body = function.body
        # 跳过 docstring。
        statements = [
            node for node in body
            if not (isinstance(node, ast.Expr)
                    and isinstance(node.value, ast.Constant))
        ]
        first = statements[0]
        self.assertTrue(
            isinstance(first, ast.Assign)
            and isinstance(first.value, ast.Call)
            and isinstance(first.value.func, ast.Name)
            and first.value.func.id == "admit_to_basket",
            "准入必须是这个函数做的第一件事",
        )

    def test_the_button_is_disabled_when_the_run_cannot_be_admitted(
        self,
    ) -> None:
        # 钉条件表达式整行:只钉 `disabled=` 的话,把它改成常量 False 能原样
        # 逃逸,按钮还在、语义已反转。
        self.assertIn(
            "        disabled=not admission.admissible or already or full,\n",
            self.source,
        )

    def test_every_refusal_says_which_kind_it_is(self) -> None:
        # 一句「不可用」不算如实。
        self.assertIn(
            "    if not admission.admissible:\n"
            '        st.caption(f"⚠ {admission.reason}")\n',
            self.source,
        )

    def test_an_alias_is_disclosed_before_it_is_added(self) -> None:
        self.assertIn("    elif admission.reason:\n", self.source)

    def test_the_panel_does_not_predict_comparability(self) -> None:
        # 可比性是对比页 `assess_comparability` 的事。在这里重推一遍就是
        # 第二份会漂移的推导。
        self.assertNotIn("assess_comparability", self.source)
        self.assertIn("不预判它们可不可比", self.source)

    def test_an_empty_basket_renders_nothing(self) -> None:
        self.assertIn(
            "    basket = current_basket()\n"
            "    if not basket:\n"
            "        return\n",
            self.source,
        )

    def test_the_link_carries_only_revalidated_members(self) -> None:
        # 加入时校验过 ≠ 送出时还成立:篮子是会话级的,而在此期间目录归属
        # 可能变。照原样拼进 URL,对比页会 st.stop() ——一模一样的拒绝页,
        # 只是晚了一步发生(codex P1 on #472)。
        self.assertIn(
            '                query_params={"run_ids": '
            "basket_query_value(checked.live)},\n",
            self.source,
        )
        # 生篮子**绝不**直接进 URL。
        self.assertNotIn("basket_query_value(basket)", self.source)
        # 篮子不足 2 个时不给链接:给了就是把人送进对比页的「请选择 2-5
        # 个」提示,而那句话在这里就该说完。判据也走复核后的成员。
        self.assertIn(
            "        gap = basket_readiness(checked.live)\n",
            self.source,
        )

    def test_revalidation_happens_before_any_link_is_rendered(self) -> None:
        # 用 AST 问「复核在不在 page_link 之前」,不按文本行号猜:两处都在
        # 同一个函数体里,顺序颠倒后源码串照样命中。
        function = self._function("render_basket_panel")
        revalidate_line = next(
            node.lineno
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "revalidate_basket"
        )
        link_line = next(
            node.lineno
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "page_link"
        )
        self.assertLess(revalidate_line, link_line)

    def test_the_stale_header_does_not_pick_one_cause_for_all_of_them(
        self,
    ) -> None:
        # 失效可以是被接管 / 被删除 / 类型不收 / 没有产物目录 / id 带不进
        # URL。把其中一种写成总标题，对另外四种就是假话——而紧跟的逐条说明
        # 会与它直接打架。总标题只报数，原因交给逐条。
        self.assertIn("各自的原因如下", self.source)
        for single_cause in ("加入之后目录归属变了", "已被接管", "已被删除"):
            self.assertNotIn(
                single_cause, self.source,
                f"总标题不该替所有失效成员断言 {single_cause!r} 这一种原因",
            )

    def test_stale_members_block_the_link_instead_of_being_dropped(
        self,
    ) -> None:
        # 自动踢出等于替操作人决定「这个不要了」,而他可能正想知道它去哪了。
        self.assertIn(
            "        if checked.stale or checked.collapsed:\n", self.source)
        self.assertIn("        if checked.stale:\n", self.source)
        self.assertIn("        if checked.collapsed:\n", self.source)
        # 每个失效成员都要说出**自己**的原因,不是一句「有几个不能用」。
        self.assertIn(
            '                st.caption(f"· `{_stale.run_id}`：'
            '{_stale.reason}")\n',
            self.source,
        )

    def test_a_member_that_changed_id_is_disclosed(self) -> None:
        # 加入时当场披露别名是本模块的纪律。复核路径不披露的话,篮子显示 A、
        # 链接静默带 B 过去——两个名字都合法,操作人无从发现（codex P2 on
        # #472 r2）。同一条纪律要覆盖**两条**路径。
        self.assertIn("        if checked.rerouted:\n", self.source)
        self.assertIn(
            '                    f"`{_from}` → `{_to}`" '
            "for _from, _to in checked.rerouted)\n",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
