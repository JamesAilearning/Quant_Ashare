"""对比篮子的准入判定与篮子操作。

判定的价值全在**分因**上：来源页要能说出「为什么这次运行送不到对比页」，
而不是把人送进对比页的 ``st.error`` + ``st.stop()``，也不是给一句「不可用」。
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from web.operator_ui.compare_basket import (
    ADMIT_ALIASED,
    ADMIT_NO_ARTIFACTS,
    ADMIT_OK,
    ADMIT_SUPERSEDED,
    ADMIT_UNKNOWN,
    ADMIT_WRONG_TYPE,
    MAX_BASKET_SIZE,
    MIN_COMPARE_SIZE,
    add_to_basket,
    admit_to_basket,
    basket_query_value,
    basket_readiness,
    remove_from_basket,
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

    def test_a_superseded_run_says_so_instead_of_just_unavailable(self) -> None:
        # 同目录被更新的运行接管 ⇒ 不再是该目录的当前所有者。这与「目录里
        # 根本没有这条」对操作人的下一步完全不同。
        admission = admit_to_basket(
            "old-run",
            selectable_ids=["new-run"],
            run_id_alias={},
            all_rows=[_Row("old-run"), _Row("new-run")],
        )

        self.assertFalse(admission.admissible)
        self.assertEqual(admission.verdict, ADMIT_SUPERSEDED)
        self.assertIn("接管", admission.reason)

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


if __name__ == "__main__":
    unittest.main()
