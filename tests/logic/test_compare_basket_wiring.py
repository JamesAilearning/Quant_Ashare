"""篮子与它下游那几处约束的**同一性**，以及三个来源页的接线。

篮子的上界、可比类型、最小数量都是**别处**定下的规则的副本。副本不钉住
就会分叉：放宽了对比页的 ``max_selections`` 而篮子没跟上，操作人攒到第 6
个再跳转，会撞上 URL 守卫的静默拒绝——一个什么也不说的空页。
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

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
_RESULTS_RENDER = Path("web/operator_ui/pages/_results_render.py")
_WALK_FORWARD_PAGE = Path("web/operator_ui/pages/walk_forward.py")


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
    """三个来源页都要接，且都要把**全量**目录行传下去。

    只传 ``catalog.rows``（当前所有者）的话，「被同目录的更新运行接管」会
    退化成「目录里根本没有这条」——分因就没了，操作人只剩一句「不可用」。
    """

    def _assert_full_catalog_is_passed(self, path: Path) -> None:
        source = path.read_text(encoding="utf-8")
        self.assertIn(
            "_all_catalog_rows = load_all_jobs_read_only()\n", source)
        self.assertIn(
            "_compare_catalog = selectable_catalog(_all_catalog_rows)\n",
            source,
        )
        self.assertIn("            all_rows=_all_catalog_rows,\n", source)
        # 可选 id 与别名都取自 catalog 本身,不许在页面里重推目录归属。
        self.assertIn(
            "selectable_ids=[row.run_id for row in _compare_catalog.rows],",
            source,
        )
        self.assertIn(
            "run_id_alias=_compare_catalog.run_id_alias,", source)

    def test_jobs_page_offers_the_basket(self) -> None:
        source = _JOBS_PAGE.read_text(encoding="utf-8")

        self.assertIn("render_add_to_basket_button(\n", source)
        self.assertIn('key_prefix="jobs",', source)
        self.assertIn('render_basket_panel(key_prefix="jobs")', source)
        self._assert_full_catalog_is_passed(_JOBS_PAGE)
        # 动作栏多了一列。旧的 2/3 列布局会把新按钮挤到别的动作上面。
        self.assertIn(
            "act_open, act_copy, act_compare, act_stop = st.columns(4)",
            source,
        )
        self.assertIn(
            "act_open, act_copy, act_compare = st.columns(3)", source)

    def test_results_page_offers_the_basket(self) -> None:
        source = _RESULTS_RENDER.read_text(encoding="utf-8")

        self.assertIn("render_add_to_basket_button(\n", source)
        self.assertIn('key_prefix="results",', source)
        self.assertIn('render_basket_panel(key_prefix="results")', source)
        self._assert_full_catalog_is_passed(_RESULTS_RENDER)
        self.assertIn("action_cols = st.columns([1, 1, 1, 1, 1])", source)

    def test_walk_forward_page_offers_the_basket(self) -> None:
        source = _WALK_FORWARD_PAGE.read_text(encoding="utf-8")

        self.assertIn("render_add_to_basket_button(\n", source)
        self.assertIn('key_prefix="wf",', source)
        self.assertIn('render_basket_panel(key_prefix="wf")', source)
        self._assert_full_catalog_is_passed(_WALK_FORWARD_PAGE)
        # 本页的「当前运行」是目录键映射出来的 id,不是 selectbox 的返回值。
        self.assertIn(
            '_wf_selected_run_id = str(run_options.get(str(selected), "")'
            ' or "")\n',
            source,
        )
        # 取不到 id 时**不画**按钮:画一个注定被拒的按钮就是把人送进拒绝页。
        self.assertIn("\nif _wf_selected_run_id:\n", source)


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

    def test_the_link_carries_the_basket_as_run_ids(self) -> None:
        self.assertIn(
            '                query_params={"run_ids": '
            "basket_query_value(basket)},\n",
            self.source,
        )
        # 篮子不足 2 个时**不给**链接:给了就是把人送进对比页的「请选择
        # 2-5 个」提示,而那句话在这里就该说完。
        self.assertIn(
            "        gap = basket_readiness(basket)\n"
            "        if gap:\n",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
