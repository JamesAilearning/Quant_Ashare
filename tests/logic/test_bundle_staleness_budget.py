"""数据包新鲜度：剩余预算是一等公民（openspec 2026-08-22-bundle-staleness-in-health）。

出单侧对陈旧有硬闸（`bundle_max_age_days = 14`），而控制台的健康判定原本
**完全不看**它：状态只由结构完整性决定。实测 2026-08-22 本机——bundle 末日
2026-08-14、已 8 天、离下限只剩 6 天、夜间更新连续三晚失败，健康卡仍是
`状态 ok`，队列仍是 `attention`。三晚里操作人的第一视图始终是绿的。
"""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from web.operator_ui import bundle_health
from web.operator_ui.bundle_health import (
    BUDGET_HALF_SPENT_DAYS,
    BUNDLE_MAX_AGE_DAYS,
    days_until_stale_floor,
)
from web.operator_ui.pages._today_decision_queue_helpers import (
    TodayQueueItem,
    build_today_decision_queue,
)
from web.operator_ui.pages._today_workbench_helpers import DailySignalSummary

_ROOT = Path(__file__).resolve().parents[2]


class RemainingBudgetIsComputedNotGuessed(unittest.TestCase):
    def test_the_floor_is_pinned_against_the_producer_source(self) -> None:
        """下限值不重新发明。

        沿用 #454 为 `artifact_schema_version` 立下的惯例：UI 侧钉具名常量，
        用一条测试**读生产者源码做字面对齐**——导入 `daily_recommend` 会把整个
        qlib + gym 拉进渲染进程。生产者改了而这边没跟，这条立刻红。
        """
        producer = (
            _ROOT / "src" / "inference" / "daily_recommend.py"
        ).read_text(encoding="utf-8")
        self.assertEqual(BUNDLE_MAX_AGE_DAYS, 14)
        self.assertIn("bundle_max_age_days: int = 14", producer)

    def test_the_escalation_line_is_derived_from_the_floor(self) -> None:
        """界线必须**从下限推导**，不是另写一个恰好相等的字面量。

        只断言 `== BUNDLE_MAX_AGE_DAYS // 2` 分辨不出这两者：写死 `7` 在今天
        与推导值相等，测试照样绿——变异测试实测确认过。要盯的性质是「派生」，
        所以直接断言源码里写的就是那个推导式（与本仓 #444 用
        `assertIn("canonical_dir_key(", body)` 钉「委托而非重写」同一手法）。
        """
        self.assertEqual(BUDGET_HALF_SPENT_DAYS, BUNDLE_MAX_AGE_DAYS // 2)
        source = (
            _ROOT / "web" / "operator_ui" / "bundle_health.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "BUDGET_HALF_SPENT_DAYS = BUNDLE_MAX_AGE_DAYS // 2", source,
            "界线被写成了字面量——下限一改它就不会跟着改")

    def test_budget_counts_down_in_calendar_days(self) -> None:
        today = date(2026, 8, 22)
        self.assertEqual(days_until_stale_floor("2026-08-22", today=today), 14)
        self.assertEqual(days_until_stale_floor("2026-08-14", today=today), 6)
        self.assertEqual(days_until_stale_floor("2026-08-08", today=today), 0)
        self.assertEqual(days_until_stale_floor("2026-08-01", today=today), -7)

    def test_an_unknown_tail_is_unknown_not_assumed(self) -> None:
        today = date(2026, 8, 22)
        for tail in (None, "", "not-a-date"):
            with self.subTest(末日=tail):
                self.assertIsNone(days_until_stale_floor(tail, today=today))


class HealthGoesRedOnlyWhenServingWouldRefuse(unittest.TestCase):
    """预算用尽 = 出单此刻就会被拒。这不是新阈值，是既成事实。

    在 ``inspect_provider_metadata`` 这个缝上打桩，好让用例不必造一个真 bundle
    ——被测的是判定，不是元数据读取。
    """

    def _summary(self, tail: date | None, *, today: date) -> object:
        meta = SimpleNamespace(
            coverage_end_date=tail, instrument_count=5795,
            warnings=(), errors=(),
        )
        with mock.patch.object(
            bundle_health, "inspect_provider_metadata", return_value=meta,
        ):
            return bundle_health.summarise_bundle_health("D:/fake", today=today)

    def test_budget_left_does_not_change_the_colour(self) -> None:
        # 这正是三晚故障期间的真实取值：末日 08-14、今天 08-22、还剩 6 天。
        s = self._summary(date(2026, 8, 14), today=date(2026, 8, 22))
        self.assertEqual(s.status, "ok")
        self.assertEqual(s.days_until_stale_floor, 6)
        self.assertIn("还剩 6 天", s.message)

    def test_budget_exhausted_turns_red(self) -> None:
        s = self._summary(date(2026, 8, 8), today=date(2026, 8, 22))
        self.assertEqual(
            s.status, "error", "预算已用尽（出单会被拒），健康卡还是绿的")
        self.assertEqual(s.days_until_stale_floor, 0)

    def test_past_the_floor_says_how_far_past(self) -> None:
        s = self._summary(date(2026, 8, 1), today=date(2026, 8, 22))
        self.assertEqual(s.status, "error")
        self.assertIn("已越过出单下限 7 天", s.message)

    def test_unknown_tail_is_not_assumed_fresh_or_stale(self) -> None:
        s = self._summary(None, today=date(2026, 8, 22))
        self.assertIsNone(s.days_until_stale_floor)
        self.assertNotEqual(s.status, "error")


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


class AFailedUpdateEscalatesAsTheBudgetDrains(unittest.TestCase):
    """一个只会说「注意」的条目，在第一晚和第三晚说的是同一句话。"""

    def test_plenty_of_budget_stays_attention(self) -> None:
        item = _failed_item(_queue(
            update_days_until_stale_floor=BUDGET_HALF_SPENT_DAYS + 1))
        self.assertEqual(item.kind, "attention")

    def test_half_the_budget_gone_becomes_a_blocker(self) -> None:
        item = _failed_item(_queue(
            update_days_until_stale_floor=BUDGET_HALF_SPENT_DAYS))
        self.assertEqual(
            item.kind, "blocker",
            "预算已用掉一半，队列还在说「注意」")

    def test_past_the_floor_is_a_blocker_and_says_serving_would_refuse(self) -> None:
        item = _failed_item(_queue(update_days_until_stale_floor=-2))
        self.assertEqual(item.kind, "blocker")
        self.assertIn("出单会被拒", item.detail)

    def test_the_remaining_days_are_stated_in_the_item(self) -> None:
        # 三晚故障里没有任何一处把这个数摆出来。
        item = _failed_item(_queue(update_days_until_stale_floor=6))
        self.assertIn("还剩 6 天", item.detail)

    def test_an_unknown_budget_does_not_fabricate_severity(self) -> None:
        item = _failed_item(_queue(update_days_until_stale_floor=None))
        self.assertEqual(item.kind, "attention")
        self.assertEqual(item.detail, "抓取失败")


if __name__ == "__main__":
    unittest.main()
