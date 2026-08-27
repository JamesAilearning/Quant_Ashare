"""对比篮子的准入判定与篮子操作。

判定的价值全在**分因**上：来源页要能说出「为什么这次运行送不到对比页」，
而不是把人送进对比页的 ``st.error`` + ``st.stop()``，也不是给一句「不可用」。
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from web.operator_ui._param_guard import sanitize
from web.operator_ui.compare_basket import (
    ADMIT_ALIASED,
    ADMIT_NO_ARTIFACTS,
    ADMIT_OK,
    ADMIT_SUPERSEDED,
    ADMIT_UNKNOWN,
    ADMIT_UNROUTABLE_ID,
    ADMIT_WRONG_TYPE,
    MAX_BASKET_SIZE,
    MIN_COMPARE_SIZE,
    add_to_basket,
    admit_to_basket,
    basket_query_value,
    basket_readiness,
    remove_from_basket,
    revalidate_basket,
)
from web.operator_ui.pages._research_run_comparison_helpers import (
    parse_selected_run_ids,
)


@dataclass(frozen=True)
class _Row:
    run_id: str
    type: str = "pipeline"
    run_dir: str = "output/runs/x"


class AdmissionTests(unittest.TestCase):
    def test_a_selectable_run_is_admitted_as_itself(self) -> None:
        admission = admit_to_basket(
            "run-a", selectable_ids=["run-a", "run-b"], run_id_alias={})

        self.assertTrue(admission.admissible)
        self.assertEqual(admission.verdict, ADMIT_OK)
        self.assertEqual(admission.resolved_run_id, "run-a")
        self.assertEqual(admission.reason, "")

    def test_an_aliased_run_says_which_id_will_be_compared(self) -> None:
        # 别名行加进去的 id 与按钮旁显示的不是同一个。不说的话,操作人加了
        # A、对比页显示 B,而无处解释。
        admission = admit_to_basket(
            "cli-1",
            selectable_ids=["ui-9"],
            run_id_alias={"cli-1": "ui-9"},
        )

        self.assertTrue(admission.admissible)
        self.assertEqual(admission.verdict, ADMIT_ALIASED)
        self.assertEqual(admission.resolved_run_id, "ui-9")
        self.assertIn("ui-9", admission.reason)

    def test_a_superseded_run_names_the_id_that_represents_its_directory(
        self,
    ) -> None:
        # 同一份产物目录上另有一条**可选**记录 ⇒ 目录里由它代表。把那个 id
        # 说出来，操作人才知道下一步该找谁。
        #
        # 这与「目录里根本没有这条」对操作人的下一步完全不同；也与「被一次
        # 更新的运行接管」不同——最常见的成因其实是同一次运行的 UI 作业与
        # CLI 目录记录之间镜像证明不成立，那是**同一份产物**的两条记录。
        admission = admit_to_basket(
            "ui-9",
            selectable_ids=["cli-1"],
            run_id_alias={},
            all_rows=[_Row("ui-9"), _Row("cli-1")],
        )

        self.assertFalse(admission.admissible)
        self.assertEqual(admission.verdict, ADMIT_SUPERSEDED)
        self.assertIn("cli-1", admission.reason)
        # 而且**不能**把它说成「被更新的运行接管」：最常见的成因是同一次运行
        # 的 UI 作业与 CLI 目录记录之间镜像证明不成立，那是同一份产物的两条
        # 记录，不是两次运行。说成后者会让操作人去找一个并不存在的新运行。
        self.assertNotIn("接管", admission.reason)

    def test_a_row_on_a_different_directory_is_not_offered_as_its_owner(
        self,
    ) -> None:
        # 只有**同一份产物目录**上的记录才代表得了它。拿另一个目录的可选
        # 记录去顶，会把操作人指向一次毫无关系的运行。
        admission = admit_to_basket(
            "ui-9",
            selectable_ids=["other"],
            run_id_alias={},
            all_rows=[_Row("ui-9", run_dir="output/runs/a"),
                      _Row("other", run_dir="output/runs/b")],
        )

        self.assertFalse(admission.admissible)
        self.assertEqual(admission.verdict, ADMIT_UNKNOWN)
        self.assertNotIn("other", admission.reason)

    def test_an_inconsistent_catalog_is_not_explained_away(self) -> None:
        # 「我不是所有者」按 `selectable_catalog` 的构造蕴含「同目录另有一个
        # 所有者」。真走到「同目录找不到代表」这一格，说明输入本身不自洽——
        # 这时**不猜原因**，说成「被更新的运行接管」是编一个并不知道的因果。
        admission = admit_to_basket(
            "ui-9",
            selectable_ids=[],
            run_id_alias={},
            all_rows=[_Row("ui-9")],
        )

        self.assertEqual(admission.verdict, ADMIT_UNKNOWN)
        self.assertIn("不自洽", admission.reason)

    def test_a_non_comparable_type_names_its_type(self) -> None:
        admission = admit_to_basket(
            "prov-1",
            selectable_ids=[],
            run_id_alias={},
            all_rows=[_Row("prov-1", type="provider")],
        )

        self.assertEqual(admission.verdict, ADMIT_WRONG_TYPE)
        self.assertIn("provider", admission.reason)

    def test_a_run_without_artifacts_says_so(self) -> None:
        admission = admit_to_basket(
            "no-dir",
            selectable_ids=[],
            run_id_alias={},
            all_rows=[_Row("no-dir", run_dir="")],
        )

        self.assertEqual(admission.verdict, ADMIT_NO_ARTIFACTS)

    def test_an_unknown_run_is_not_guessed_at(self) -> None:
        admission = admit_to_basket(
            "ghost", selectable_ids=["run-a"], run_id_alias={}, all_rows=[])

        self.assertEqual(admission.verdict, ADMIT_UNKNOWN)
        self.assertEqual(admission.resolved_run_id, "")

    def test_an_id_the_url_guard_rejects_is_refused_at_the_entry_point(
        self,
    ) -> None:
        # CLI 目录行的 run_id 只被**结构**校验过。含 `:` 或 `/` 的 id 结构
        # 合法、`selectable_catalog` 也照收,于是按钮显示可用;但拼进 URL 后
        # `_param_guard._run_ids` 会把**整条选择**静默换成空默认值——对比页
        # 什么都没选中,而按钮刚刚说这次运行可以加入(codex P2 on #472 r2)。
        for bad in ("run:a", "run/a", "run a"):
            with self.subTest(run_id=bad):
                admission = admit_to_basket(
                    bad, selectable_ids=[bad], run_id_alias={})

                self.assertFalse(admission.admissible)
                self.assertEqual(admission.verdict, ADMIT_UNROUTABLE_ID)
                self.assertEqual(admission.resolved_run_id, "")

    def test_an_alias_target_the_url_guard_rejects_is_refused(self) -> None:
        # 别名解析出来的那个 id 才是真正会进 URL 的。
        admission = admit_to_basket(
            "clean-id",
            selectable_ids=["cli:9"],
            run_id_alias={"clean-id": "cli:9"},
        )

        self.assertFalse(admission.admissible)
        self.assertEqual(admission.verdict, ADMIT_UNROUTABLE_ID)

    def test_a_comma_in_the_id_is_refused_although_sanitize_accepts_it(
        self,
    ) -> None:
        # 逗号是那个 URL 参数的**分隔符**。`sanitize("run_ids", "run,a")`
        # 原样返回(实测)——它把这串读成两个合法 id。对比页于是去找两个并不
        # 存在的运行,然后 st.stop()。所以判据必须是**完整回环**,不是
        # 「过得了 sanitize」。
        self.assertEqual(sanitize("run_ids", "run,a", default=""), "run,a")

        admission = admit_to_basket(
            "run,a", selectable_ids=["run,a"], run_id_alias={})

        self.assertFalse(admission.admissible)
        self.assertEqual(admission.verdict, ADMIT_UNROUTABLE_ID)

    def test_the_url_check_is_a_full_round_trip(self) -> None:
        # 抄一份字符集会与守卫分叉,而分叉的症状正是那条静默丢弃。这里钉的
        # 是「拼进去再解析回来,原样得到它自己」。
        for run_id in ("run-a", "run_a", "run.a", "RUN-1", "a" * 200):
            with self.subTest(run_id=run_id):
                admission = admit_to_basket(
                    run_id, selectable_ids=[run_id], run_id_alias={})
                round_trip = parse_selected_run_ids(
                    sanitize("run_ids", run_id, default="")) == (run_id,)
                self.assertEqual(admission.admissible, round_trip)

    def test_an_empty_run_id_is_refused_without_touching_the_catalog(
        self,
    ) -> None:
        admission = admit_to_basket("", selectable_ids=[], run_id_alias={})

        self.assertFalse(admission.admissible)
        self.assertEqual(admission.verdict, ADMIT_UNKNOWN)


class BasketTests(unittest.TestCase):
    def _ok(self, run_id: str) -> object:
        return admit_to_basket(
            run_id, selectable_ids=[run_id], run_id_alias={})

    def test_adding_stores_the_resolved_id(self) -> None:
        # 篮子存**解析后**的 id:对比页收到的就是它,篮子里显示的也该是它。
        admission = admit_to_basket(
            "cli-1", selectable_ids=["ui-9"], run_id_alias={"cli-1": "ui-9"})

        basket, _ = add_to_basket((), admission)

        self.assertEqual(basket, ("ui-9",))

    def test_adding_the_same_run_twice_is_reported_not_duplicated(self) -> None:
        # URL 白名单 (`_param_guard._run_ids`) 拒绝重复项,静默去重会让篮子
        # 计数与实际能带过去的数量对不上。
        basket, note = add_to_basket(("run-a",), self._ok("run-a"))

        self.assertEqual(basket, ("run-a",))
        self.assertIn("已经在", note)

    def test_the_basket_stops_at_the_url_whitelist_ceiling(self) -> None:
        # 上界与 `_param_guard._run_ids` 的 1-5 及对比页 max_selections=5
        # 对齐。攒到第 6 个再跳转会撞上 URL 守卫的静默拒绝。
        full = tuple(f"run-{i}" for i in range(MAX_BASKET_SIZE))

        basket, note = add_to_basket(full, self._ok("run-extra"))

        self.assertEqual(basket, full)
        self.assertIn(str(MAX_BASKET_SIZE), note)

    def test_an_inadmissible_run_never_enters_the_basket(self) -> None:
        admission = admit_to_basket(
            "old-run", selectable_ids=[], run_id_alias={},
            all_rows=[_Row("old-run")])

        basket, note = add_to_basket(("run-a",), admission)

        self.assertEqual(basket, ("run-a",))
        self.assertEqual(note, admission.reason)

    def test_removing_is_by_value_and_keeps_order(self) -> None:
        self.assertEqual(
            remove_from_basket(("a", "b", "c"), "b"), ("a", "c"))

    def test_query_value_matches_the_url_whitelist_shape(self) -> None:
        self.assertEqual(basket_query_value(("a", "b")), "a,b")
        # 空篮子给空串——对比页把空串当作「没有请求」而不是一个空 id。
        self.assertEqual(basket_query_value(()), "")

    def test_readiness_states_what_is_missing(self) -> None:
        self.assertIn("空", basket_readiness(()))
        self.assertIn(str(MIN_COMPARE_SIZE), basket_readiness(("a",)))
        self.assertEqual(basket_readiness(("a", "b")), "")


class RevalidationTests(unittest.TestCase):
    """加入时校验过 ≠ 送出时还成立。

    篮子是会话级的，而在此期间同一产物目录可能被一次更新的运行接管。照原样
    拼进 URL，对比页会把它判成未知并 ``st.stop()``——一模一样的拒绝页，只是
    晚了一步发生，而这正是本模块声称要防的事。
    """

    def test_a_member_superseded_after_it_was_added_is_caught(self) -> None:
        checked = revalidate_basket(
            ("run-a", "old-run"),
            selectable_ids=["run-a", "new-run"],
            run_id_alias={},
            all_rows=[_Row("run-a"), _Row("old-run"), _Row("new-run")],
        )

        self.assertEqual(checked.live, ("run-a",))
        self.assertEqual([d.run_id for d in checked.stale], ["old-run"])
        self.assertEqual(checked.stale[0].verdict, ADMIT_SUPERSEDED)

    def test_two_members_that_now_resolve_to_one_owner_are_caught(self) -> None:
        # 对比页有重复检查,重复会让整页停下。这在加入时看不出来:那时它们
        # 确实是两个不同的可选运行。
        checked = revalidate_basket(
            ("ui-9", "cli-1"),
            selectable_ids=["ui-9"],
            run_id_alias={"cli-1": "ui-9"},
            all_rows=[_Row("ui-9"), _Row("cli-1")],
        )

        self.assertEqual(checked.live, ("ui-9",))
        self.assertEqual(checked.collapsed, ("cli-1",))

    def test_revalidation_resolves_aliases_and_says_it_rerouted(self) -> None:
        # 加入时当场披露别名是本模块的纪律。复核路径不披露的话,篮子显示
        # `cli-1`、链接静默带 `ui-9` 过去——两个名字都合法,操作人无从发现。
        # 同一条纪律要覆盖**两条**路径。
        checked = revalidate_basket(
            ("cli-1", "run-b"),
            selectable_ids=["ui-9", "run-b"],
            run_id_alias={"cli-1": "ui-9"},
        )

        self.assertEqual(checked.live, ("ui-9", "run-b"))
        self.assertEqual(checked.stale, ())
        self.assertEqual(checked.rerouted, (("cli-1", "ui-9"),))

    def test_a_member_that_still_resolves_to_itself_is_not_rerouted(
        self,
    ) -> None:
        # 每个成员都报一句「改名了」等于把这条提示变成噪音。
        checked = revalidate_basket(
            ("run-a", "run-b"),
            selectable_ids=["run-a", "run-b"],
            run_id_alias={},
        )

        self.assertEqual(checked.rerouted, ())

    def test_an_intact_basket_passes_through_in_order(self) -> None:
        checked = revalidate_basket(
            ("run-b", "run-a"),
            selectable_ids=["run-a", "run-b"],
            run_id_alias={},
        )

        self.assertEqual(checked.live, ("run-b", "run-a"))
        self.assertEqual(checked.stale, ())
        self.assertEqual(checked.collapsed, ())

    def test_revalidation_survives_one_shot_iterators(self) -> None:
        # 签名收的是 Iterable，而每个成员都要把它**再交给** admit_to_basket
        # 消费一遍。传一次性迭代器时，第一个成员就把它抽干，之后每个成员都
        # 看到空目录、被判成失效。类型标注反而给这个不成立的契约背了书：
        # mypy 不报，而 list 字面量的用例永远走不到这条路径。
        checked = revalidate_basket(
            ("a", "b", "c"),
            selectable_ids=(i for i in ["a", "b", "c"]),
            run_id_alias={},
            all_rows=(_Row(x) for x in "abc"),
        )

        self.assertEqual(checked.live, ("a", "b", "c"))
        self.assertEqual(checked.stale, ())

    def test_a_stale_member_is_not_silently_dropped_from_the_report(
        self,
    ) -> None:
        # 自动踢出等于替操作人决定「这个不要了」,而他可能正想知道它去哪了。
        checked = revalidate_basket(
            ("ghost",), selectable_ids=[], run_id_alias={}, all_rows=[])

        self.assertEqual(checked.live, ())
        self.assertEqual(len(checked.stale), 1)
        self.assertTrue(checked.stale[0].reason)


if __name__ == "__main__":
    unittest.main()
