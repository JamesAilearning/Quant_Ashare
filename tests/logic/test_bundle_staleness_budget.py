"""今日待办队列按**出单侧自己的**新鲜度判定给「数据更新失败」定严重度。

（openspec 2026-08-22-bundle-staleness-in-health）

起因：夜间更新连续三晚失败（2026-08-17 / 08-20 / 08-21，均 exit 11），而队列
全程把它归为 `attention`，余量从 14 天掉到 6 天，第一视图始终不变。一个只会说
「注意」的条目，在第一晚和第三晚说的是同一句话。

**本 change 不新造任何新鲜度判据。** 仓库里已有 `_ops_cockpit_helpers`
一套与出单侧逐字对齐的：时钟用宿主本地日、边界 `behind > limit`（14 天整仍
接受）、末日读 `calendars/day.txt`。#461 首版在 `bundle_health` 里另写了一份，
三个决策全错、三条 P1、并打红既有 26 条 —— 这些用例守的正是「不许再写第二份」。
"""

from __future__ import annotations

import ast
import unittest
from datetime import date
from pathlib import Path

from web.operator_ui.pages._ops_cockpit_helpers import bundle_freshness
from web.operator_ui.pages._today_decision_queue_helpers import (
    TodayQueueItem,
    build_today_decision_queue,
)
from web.operator_ui.pages._today_workbench_helpers import DailySignalSummary

_ROOT = Path(__file__).resolve().parents[2]
_PAGE = _ROOT / "web" / "operator_ui" / "pages" / "today_workbench.py"
_QUEUE = _ROOT / "web" / "operator_ui" / "pages" / "_today_decision_queue_helpers.py"


def _queue(**over: object) -> tuple[TodayQueueItem, ...]:
    base: dict[str, object] = {
        "provider_problem": None,
        "bundle_status": "ok",
        "bundle_detail": "",
        "update_kind": "finished",
        "update_detail": "抓取失败",
        "update_time": "2026-08-21T22:07:10+08:00",
        "update_matches_provider": True,
        "update_running_class": None,
        "signal": DailySignalSummary(kind="ok", detail=""),
        "jobs": (),
        "jobs_error": None,
        "review": None,
        "review_error": None,
        "incumbent_kind": "ok",
        "incumbent_detail": "",
    }
    base.update(over)
    return build_today_decision_queue(**base)  # type: ignore[arg-type]


def _failed_item(items: tuple[TodayQueueItem, ...]) -> TodayQueueItem:
    found = [i for i in items if i.source_key == "update:failed"]
    assert len(found) == 1, f"期望恰好一条 update:failed，得到 {len(found)}"
    return found[0]


def _verdict(tail: str, today: date) -> object:
    """出单侧那套判据的裁决——用例一律经由它取值，不自己算天数。"""
    return bundle_freshness(
        today=today, tail_date=tail, provider_uri="X", max_age_days=14)


class TheQueueUsesTheServingVerdictNotItsOwnArithmetic(unittest.TestCase):
    def test_the_page_asks_the_existing_freshness_helper(self) -> None:
        """页面必须**调用**既有判据，而不是自己再算一遍。

        断言的是 import 与调用，不是数值：数值相等在本机可能只是巧合（同一条
        教训写在 `test_the_clock_matches_the_recommenders_not_the_operators`
        里——本机就在 +08:00，两个时钟一致，带 bug 也能过）。
        """
        tree = ast.parse(_PAGE.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "web.operator_ui.pages._ops_cockpit_helpers"
            for alias in node.names
        }
        self.assertEqual(
            {"bundle_calendar_tail", "bundle_freshness",
             "recommender_integrity_check"},
            imported,
            "今日工作台没有复用既有的新鲜度判据",
        )

    def test_the_queue_module_computes_no_staleness_of_its_own(self) -> None:
        # 第二道独立守卫：队列模块不许把任何陈旧度算术搬回自己家。
        source = _QUEUE.read_text(encoding="utf-8")
        for forbidden in ("date.today(", "datetime.now(", "BUNDLE_MAX_AGE_DAYS",
                          "timezone(timedelta"):
            with self.subTest(禁用=forbidden):
                self.assertNotIn(forbidden, source)


class AFailedUpdateEscalatesAsTheServingBudgetDrains(unittest.TestCase):
    def test_plenty_of_headroom_stays_attention(self) -> None:
        v = _verdict("2026-08-20", date(2026, 8, 22))          # 落后 2 天
        item = _failed_item(_queue(
            bundle_refuses_today=v.refuses_today,
            bundle_headroom_days=v.headroom_days,
            bundle_max_age_days=v.max_age_days))
        self.assertEqual(item.kind, "attention")
        self.assertIn("余 12 天", item.detail)

    def test_half_the_budget_gone_becomes_a_blocker(self) -> None:
        # 三晚故障第三晚的真实取值：末日 08-14、今天 08-22、落后 8 天、余 6 天。
        v = _verdict("2026-08-14", date(2026, 8, 22))
        item = _failed_item(_queue(
            bundle_refuses_today=v.refuses_today,
            bundle_headroom_days=v.headroom_days,
            bundle_max_age_days=v.max_age_days))
        self.assertEqual(
            item.kind, "blocker", "预算已用掉一半，队列还在说「注意」")
        self.assertIn("余 6 天", item.detail)

    def test_the_boundary_day_is_the_recommenders_boundary(self) -> None:
        """落后正好 14 天：出单侧**仍然接受**，所以不能说「会被拒」。

        #461 首版在这里判红并写「出单此刻就会被拒」，比出单侧早了一整天。
        """
        v = _verdict("2026-08-08", date(2026, 8, 22))          # 落后 14 天
        self.assertEqual(14, v.days_behind)
        self.assertFalse(v.refuses_today, "恰好等于阈值，出单侧仍接受")
        item = _failed_item(_queue(
            bundle_refuses_today=v.refuses_today,
            bundle_headroom_days=v.headroom_days,
            bundle_max_age_days=v.max_age_days))
        self.assertNotIn("会被拒", item.detail)

    def test_one_day_past_the_boundary_says_serving_refuses(self) -> None:
        v = _verdict("2026-08-07", date(2026, 8, 22))          # 落后 15 天
        self.assertTrue(v.refuses_today)
        item = _failed_item(_queue(
            bundle_refuses_today=v.refuses_today,
            bundle_headroom_days=v.headroom_days,
            bundle_max_age_days=v.max_age_days))
        self.assertEqual(item.kind, "blocker")
        self.assertIn("今天出单会被拒", item.detail)

    def test_an_unknown_verdict_does_not_fabricate_severity(self) -> None:
        # 末日读不出来时（日历字节有歧义），既不假装新鲜也不假装陈旧。
        v = _verdict("", date(2026, 8, 22))
        self.assertFalse(v.known)
        item = _failed_item(_queue(
            bundle_refuses_today=v.refuses_today,
            bundle_headroom_days=v.headroom_days,
            bundle_max_age_days=v.max_age_days))
        self.assertEqual(item.kind, "attention")
        self.assertEqual(item.detail, "抓取失败")


if __name__ == "__main__":
    unittest.main()
