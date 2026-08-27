"""「名义持仓基准」的回溯：找最近一次再平衡日工件，并逐条交代跳过了什么。

生产是 csi800 / N5 / 周频 iso_week，绝大多数交易日是 HOLD 日。所以「我此刻
名义上跟的是哪一天的那张单」这个问题，答案通常不是今天。这一页此前只能一次
看一天，要回答它得逐个日期点开、逐个看 HOLD 横幅。

这里盯三件事：

1. **不推断缺失**——老工件没有 cadence 字段时不当作再平衡日（``hold_state``
   对它返回 ``is_hold=False``，只看 ``is_hold`` 就会把它误当基准）；
2. **逐条记账**——「基准在 30 天前」与「一份合格的基准都没有」对操作人的下一
   步完全不同，中途被 HOLD 日填满还是被损坏工件填满也不同；
3. **不造仓位**——工件里没有权重/股数/金额，名单只能是代码集合。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.operator_ui.pages._daily_decision_helpers import (  # noqa: E402
    BASELINE_SKIP_ENTRY_TIMING,
    BASELINE_SKIP_HOLD,
    BASELINE_SKIP_MALFORMED_CADENCE,
    BASELINE_SKIP_NO_CADENCE,
    BASELINE_SKIP_UNREADABLE,
    BASELINE_SKIP_UNSUPPORTED_SCHEMA,
    DEFAULT_BASELINE_SCAN_LIMIT,
    baseline_roster,
    find_nominal_baseline,
)


def _artifact(
    *,
    as_of: str,
    entry: str = "",
    rebalance: object = True,
    schema: object = 2,
    picks: list[dict[str, Any]] | None = None,
    drop_cadence: bool = False,
) -> dict[str, Any]:
    """一份形状真实的出单工件（字段取自 daily_recommend.write_outputs）。"""
    payload: dict[str, Any] = {
        "artifact_schema_version": schema,
        "as_of_date": as_of,
        "entry_date": entry or _next_day(as_of),
        "picks": picks if picks is not None else [
            {"stock_code": "SH600584", "rank": 1, "predicted_score": 0.26,
             "tradable_flag": True},
            {"stock_code": "SZ000001", "rank": 2, "predicted_score": 0.19,
             "tradable_flag": True},
        ],
        "meta": {"topk": 50, "instruments": "csi800"},
    }
    if not drop_cadence:
        payload["rebalance_day"] = rebalance
    return payload


def _next_day(day: str) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def _index(*dates: str) -> tuple[tuple[str, Path], ...]:
    """`list_recommendation_artifacts` 的形状：日期倒序的 (date, path)。"""
    return tuple(
        (d, Path(f"output/daily_recommend/daily_recommendation_{d}.json"))
        for d in sorted(dates, reverse=True)
    )


def _reader(by_date: dict[str, dict[str, Any] | None]):
    def read(path: Path) -> dict[str, Any] | None:
        stem = path.name.removeprefix("daily_recommendation_").removesuffix(".json")
        return by_date.get(stem)
    return read


class FindBaselineTests(unittest.TestCase):
    def test_a_rebalance_day_artifact_is_the_baseline(self) -> None:
        result = find_nominal_baseline(
            _index("2026-08-03"),
            read_payload=_reader({"2026-08-03": _artifact(as_of="2026-08-03")}),
        )

        self.assertTrue(result.found)
        self.assertEqual(result.baseline_date, "2026-08-03")
        self.assertEqual(result.skipped, ())
        self.assertEqual(result.scanned, 1)

    def test_hold_days_are_skipped_and_named(self) -> None:
        # 周频节奏下这是最常见的形态：往回翻过几个 HOLD 日才到基准。
        result = find_nominal_baseline(
            _index("2026-08-05", "2026-08-04", "2026-08-03"),
            read_payload=_reader({
                "2026-08-05": _artifact(as_of="2026-08-05", rebalance=False),
                "2026-08-04": _artifact(as_of="2026-08-04", rebalance=False),
                "2026-08-03": _artifact(as_of="2026-08-03", rebalance=True),
            }),
        )

        self.assertEqual(result.baseline_date, "2026-08-03")
        self.assertEqual(
            [(s.trade_date, s.reason) for s in result.skipped],
            [("2026-08-05", BASELINE_SKIP_HOLD),
             ("2026-08-04", BASELINE_SKIP_HOLD)],
        )

    def test_a_legacy_artifact_without_cadence_is_never_the_baseline(
        self,
    ) -> None:
        # `hold_state` 对它返回 is_hold=False。只看 is_hold 就会把一份根本没有
        # 节奏语义的老工件当成再平衡日——那是替一次没记录节奏的运行编造语义。
        result = find_nominal_baseline(
            _index("2026-06-16"),
            read_payload=_reader({
                "2026-06-16": _artifact(as_of="2026-06-16", drop_cadence=True),
            }),
        )

        self.assertFalse(result.found)
        self.assertEqual(
            [s.reason for s in result.skipped], [BASELINE_SKIP_NO_CADENCE])

    def test_a_non_bool_cadence_field_is_a_shape_violation(self) -> None:
        result = find_nominal_baseline(
            _index("2026-08-03"),
            read_payload=_reader({
                "2026-08-03": _artifact(as_of="2026-08-03", rebalance="yes"),
            }),
        )

        self.assertFalse(result.found)
        self.assertEqual(
            [s.reason for s in result.skipped],
            [BASELINE_SKIP_MALFORMED_CADENCE])

    def test_an_unsupported_schema_is_refused_before_its_fields_are_read(
        self,
    ) -> None:
        # schema 版本不对时，字段语义无法确认——包括 rebalance_day 本身。
        result = find_nominal_baseline(
            _index("2026-08-03"),
            read_payload=_reader({
                "2026-08-03": _artifact(as_of="2026-08-03", schema=1),
            }),
        )

        self.assertEqual(
            [s.reason for s in result.skipped],
            [BASELINE_SKIP_UNSUPPORTED_SCHEMA])

    def test_a_backwards_entry_date_is_refused(self) -> None:
        result = find_nominal_baseline(
            _index("2026-08-03"),
            read_payload=_reader({
                "2026-08-03": _artifact(as_of="2026-08-03", entry="2026-08-02"),
            }),
        )

        self.assertEqual(
            [s.reason for s in result.skipped], [BASELINE_SKIP_ENTRY_TIMING])

    def test_an_unreadable_artifact_is_recorded_not_silently_skipped(
        self,
    ) -> None:
        result = find_nominal_baseline(
            _index("2026-08-04", "2026-08-03"),
            read_payload=_reader({
                "2026-08-04": None,
                "2026-08-03": _artifact(as_of="2026-08-03"),
            }),
        )

        self.assertEqual(result.baseline_date, "2026-08-03")
        self.assertEqual(
            [(s.trade_date, s.reason) for s in result.skipped],
            [("2026-08-04", BASELINE_SKIP_UNREADABLE)])

    def test_nothing_found_says_it_exhausted_rather_than_hit_a_limit(
        self,
    ) -> None:
        # 「翻完了都没有」与「翻到上限就停了」对操作人的下一步不同。
        result = find_nominal_baseline(
            _index("2026-08-05", "2026-08-04"),
            read_payload=_reader({
                "2026-08-05": _artifact(as_of="2026-08-05", rebalance=False),
                "2026-08-04": _artifact(as_of="2026-08-04", rebalance=False),
            }),
        )

        self.assertFalse(result.found)
        self.assertTrue(result.exhausted)
        self.assertFalse(result.limit_reached)

    def test_the_scan_is_bounded_and_says_so(self) -> None:
        # 无上界回溯既慢，又会把「基准早已过期」说成「找到了」。
        dates = [f"2026-0{m}-{d:02d}" for m in (5, 6, 7) for d in range(1, 29)]
        result = find_nominal_baseline(
            _index(*dates),
            read_payload=_reader({
                d: _artifact(as_of=d, rebalance=False) for d in dates}),
            limit=3,
        )

        self.assertFalse(result.found)
        self.assertTrue(result.limit_reached)
        self.assertFalse(result.exhausted)
        self.assertEqual(result.scanned, 3)

    def test_the_default_limit_is_bounded(self) -> None:
        self.assertGreater(DEFAULT_BASELINE_SCAN_LIMIT, 0)
        self.assertLess(DEFAULT_BASELINE_SCAN_LIMIT, 1000)

    def test_the_search_starts_at_the_selected_date_not_the_newest(
        self,
    ) -> None:
        # 操作人在看历史某一天时，问的是「**那时**名义上跟的是哪一张单」。
        result = find_nominal_baseline(
            _index("2026-08-10", "2026-08-03", "2026-07-27"),
            read_payload=_reader({
                "2026-08-10": _artifact(as_of="2026-08-10", rebalance=True),
                "2026-08-03": _artifact(as_of="2026-08-03", rebalance=False),
                "2026-07-27": _artifact(as_of="2026-07-27", rebalance=True),
            }),
            as_of="2026-08-03",
        )

        self.assertEqual(result.baseline_date, "2026-07-27")
        # 比 as_of 更新的那一份根本不该被读。
        self.assertEqual(result.scanned, 2)

    def test_the_selected_date_itself_can_be_the_baseline(self) -> None:
        result = find_nominal_baseline(
            _index("2026-08-10", "2026-08-03"),
            read_payload=_reader({
                "2026-08-10": _artifact(as_of="2026-08-10", rebalance=True),
                "2026-08-03": _artifact(as_of="2026-08-03", rebalance=True),
            }),
            as_of="2026-08-03",
        )

        self.assertEqual(result.baseline_date, "2026-08-03")


class RosterTests(unittest.TestCase):
    def test_the_roster_is_codes_in_rank_order(self) -> None:
        roster = baseline_roster(_artifact(as_of="2026-08-03"))

        self.assertEqual(roster, ("SH600584", "SZ000001"))

    def test_the_roster_carries_no_quantity_of_any_kind(self) -> None:
        # 工件里只有 rank / predicted_score / tradable_flag——没有权重、没有
        # 股数、没有金额。把等权假设写进来就是凭空造出一份从未记录的仓位。
        roster = baseline_roster(_artifact(as_of="2026-08-03"))

        for entry in roster:
            self.assertIsInstance(entry, str)

    def test_a_pick_without_a_code_raises_rather_than_being_dropped(
        self,
    ) -> None:
        # `picks_table_rows` 对 stock_code 不做校验（缺失即 None）。静默丢弃
        # 会让名单比工件的候选数**少一条却不说**——操作人看到「共 1 只」，
        # 无从知道另一条是被丢了还是本来就没有。
        payload = _artifact(as_of="2026-08-03", picks=[
            {"stock_code": "SH600584", "rank": 1, "predicted_score": 0.26},
            {"rank": 2, "predicted_score": 0.19},
        ])

        with self.assertRaises(ValueError):
            baseline_roster(payload)

    def test_a_blank_code_is_also_refused(self) -> None:
        payload = _artifact(as_of="2026-08-03", picks=[
            {"stock_code": "", "rank": 1, "predicted_score": 0.26},
        ])

        with self.assertRaises(ValueError):
            baseline_roster(payload)

    def test_a_malformed_payload_raises_rather_than_yielding_nothing(
        self,
    ) -> None:
        # `picks_table_rows` 对缺 picks 的 payload **抛**（本模块既有的
        # fail-loud 纪律）。在这里吞掉它换成空名单，会让一份损坏工件看起来
        # 像「那天什么都没选」——而它其实是「这份文件读不成」。
        with self.assertRaises(ValueError):
            baseline_roster({})


class RealArtifactsOnThisMachineTests(unittest.TestCase):
    """拿本机真实工件跑一遍——手写「像的」样例是这类测试最常见的空转来源。"""

    def test_the_real_index_reaches_an_honest_verdict(self) -> None:
        import json

        from web.operator_ui.pages._daily_decision_helpers import (
            list_recommendation_artifacts,
        )

        artifacts = list_recommendation_artifacts()
        if not artifacts:
            self.skipTest("本机没有出单工件（worktree 里通常没有 output/）")

        def read(path: Path) -> dict[str, Any] | None:
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            return loaded if isinstance(loaded, dict) else None

        result = find_nominal_baseline(artifacts, read_payload=read)

        # 不断言「找得到」——本机当下的正确答案很可能就是「没有可信基准」。
        # 断言的是：每一份被跳过的工件都给出了**具体**原因，而不是沉默。
        self.assertEqual(
            result.scanned, len(result.skipped) + (1 if result.found else 0))
        for candidate in result.skipped:
            with self.subTest(date=candidate.trade_date):
                self.assertTrue(candidate.reason)
                self.assertTrue(candidate.detail)
        if not result.found:
            self.assertTrue(result.exhausted or result.limit_reached)


if __name__ == "__main__":
    unittest.main()
