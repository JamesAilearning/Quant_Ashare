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
    BASELINE_BLOCK_CORRUPT_V2,
    BASELINE_BLOCK_DATE_MISMATCH,
    BASELINE_BLOCK_ENTRY_TIMING,
    BASELINE_BLOCK_HISTORY_GAP,
    BASELINE_BLOCK_MALFORMED_CADENCE,
    BASELINE_BLOCK_NO_CADENCE,
    BASELINE_BLOCK_SHAPE,
    BASELINE_BLOCK_UNREADABLE,
    BASELINE_BLOCK_UNSUPPORTED_SCHEMA,
    BASELINE_SKIP_HOLD,
    DEFAULT_BASELINE_SCAN_LIMIT,
    baseline_roster,
    find_nominal_baseline,
    unaccounted_weekdays_between,
)

_UNSET = object()


def _pick(code: str, rank: int, score: float) -> dict[str, Any]:
    """产出器 ``RecommendationPick`` 的**全部**六键——少写一个就不是产出器
    产得出的行，而一份产出器产不出的 fixture 证明不了任何读侧行为。"""
    return {
        "stock_code": code,
        "stock_name": f"名称{rank}",
        "rank": rank,
        "predicted_score": score,
        "tradable_flag": True,
        "unavailable_reason": "",
    }


def _artifact(
    *,
    as_of: str,
    entry: str = "",
    rebalance: object = True,
    schema: object = 2,
    picks: list[dict[str, Any]] | None = None,
    drop_cadence: bool = False,
    next_rebalance: object = _UNSET,
    drop_next: bool = False,
) -> dict[str, Any]:
    """一份形状真实的出单工件（字段取自 daily_recommend.write_outputs）。

    节奏双字段**同写**——产出器在同一个守卫块里写 ``rebalance_day`` 与
    ``next_rebalance_date``，只带其一是它产不出的形态。要构造那种违约形态
    的用例，显式传 ``drop_next=True`` 或 ``next_rebalance=<值>``。
    """
    entry_date = entry or _next_day(as_of)
    payload: dict[str, Any] = {
        "artifact_schema_version": schema,
        "as_of_date": as_of,
        "entry_date": entry_date,
        "picks": picks if picks is not None else [
            _pick("SH600584", 1, 0.26),
            _pick("SZ000001", 2, 0.19),
        ],
        "meta": {"topk": 50, "instruments": "csi800"},
    }
    if not drop_cadence:
        payload["rebalance_day"] = rebalance
        if not drop_next:
            payload["next_rebalance_date"] = (
                _default_next(as_of, entry_date, rebalance)
                if next_rebalance is _UNSET else next_rebalance
            )
    return payload


def _default_next(as_of: str, entry: str, rebalance: object) -> str:
    """产出器契约下这份工件的 ``next_rebalance_date``。

    再平衡日 → 恒为 ``as_of`` 自身；HOLD 日 → 首个 >= entry 的周一
    （生产是 iso_week 节奏，且必是交易日）。
    """
    if rebalance is True:
        return as_of
    from datetime import date, timedelta
    day = date.fromisoformat(entry)
    while day.weekday() != 0:
        day += timedelta(days=1)
    return day.isoformat()


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
        self.assertTrue(result.unknowable)
        assert result.blocked_by is not None
        self.assertEqual(result.blocked_by.reason, BASELINE_BLOCK_NO_CADENCE)
        # 它也**不**能当 HOLD 继续往回翻：那份工件可能本身就是一次再平衡，
        # 只是那时还没有这个字段。两种猜测都不做。
        self.assertEqual(result.skipped, ())

    def test_a_non_bool_cadence_field_is_a_shape_violation(self) -> None:
        result = find_nominal_baseline(
            _index("2026-08-03"),
            read_payload=_reader({
                "2026-08-03": _artifact(as_of="2026-08-03", rebalance="yes"),
            }),
        )

        self.assertFalse(result.found)
        assert result.blocked_by is not None
        self.assertEqual(
            result.blocked_by.reason, BASELINE_BLOCK_MALFORMED_CADENCE)

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

        assert result.blocked_by is not None
        self.assertEqual(
            result.blocked_by.reason, BASELINE_BLOCK_UNSUPPORTED_SCHEMA)

    def test_a_backwards_entry_date_is_refused(self) -> None:
        result = find_nominal_baseline(
            _index("2026-08-03"),
            read_payload=_reader({
                "2026-08-03": _artifact(as_of="2026-08-03", entry="2026-08-02"),
            }),
        )

        assert result.blocked_by is not None
        self.assertEqual(
            result.blocked_by.reason, BASELINE_BLOCK_ENTRY_TIMING)

    def test_an_unreadable_newer_artifact_stops_the_scan(self) -> None:
        # 读不出来的那一份**本身可能就是一次更近的再平衡**。翻过它去，把更早
        # 那张单报成当前基准，就是拿一张**可能已被取代**的清单当此刻该持有的。
        # 只有经过校验的 HOLD 才能证明「那天没换手、更早那张单仍有效」。
        result = find_nominal_baseline(
            _index("2026-08-04", "2026-08-03"),
            read_payload=_reader({
                "2026-08-04": None,
                "2026-08-03": _artifact(as_of="2026-08-03"),
            }),
        )

        self.assertFalse(result.found)
        self.assertTrue(result.unknowable)
        assert result.blocked_by is not None
        self.assertEqual(result.blocked_by.trade_date, "2026-08-04")
        self.assertEqual(result.blocked_by.reason, BASELINE_BLOCK_UNREADABLE)
        # 更早那份**没有被读**——回溯就地停下了。
        self.assertEqual(result.scanned, 1)

    def test_a_filename_payload_date_mismatch_stops_the_scan(self) -> None:
        # 改名/拷贝过的工件会让「八月三日那一份」其实装着八月十日的截面。
        # 拿它当基准就是把未来数据当成当日应持有；选中工件流对同一形状是
        # st.stop()，回溯不能更宽松。
        result = find_nominal_baseline(
            _index("2026-08-03"),
            read_payload=_reader({
                "2026-08-03": _artifact(as_of="2026-08-10"),
            }),
        )

        self.assertFalse(result.found)
        assert result.blocked_by is not None
        self.assertEqual(
            result.blocked_by.reason, BASELINE_BLOCK_DATE_MISMATCH)

    def test_a_corrupt_v2_artifact_stops_the_scan(self) -> None:
        # 带 v2 标记却没有 dict meta = 损坏（产出器对 v2 恒写 dict meta）。
        # 选中工件流对同一形状 st.stop()，这里同样不能放行。
        payload = _artifact(as_of="2026-08-03")
        payload.pop("meta")
        result = find_nominal_baseline(
            _index("2026-08-03"), read_payload=_reader({"2026-08-03": payload}))

        self.assertFalse(result.found)
        assert result.blocked_by is not None
        self.assertEqual(result.blocked_by.reason, BASELINE_BLOCK_CORRUPT_V2)

    def test_only_a_validated_hold_licenses_scanning_further_back(
        self,
    ) -> None:
        # 这条是整段语义的总闸：经过校验的 HOLD 才是「可以继续往回翻」的
        # 唯一凭据。把任何一种「回答不了」当成 HOLD，都会让回溯翻过一份
        # 可能取代了更早清单的工件。
        result = find_nominal_baseline(
            _index("2026-08-05", "2026-08-04", "2026-08-03"),
            read_payload=_reader({
                "2026-08-05": _artifact(as_of="2026-08-05", rebalance=False),
                "2026-08-04": _artifact(as_of="2026-08-04", schema=1),
                "2026-08-03": _artifact(as_of="2026-08-03", rebalance=True),
            }),
        )

        self.assertFalse(result.found)
        assert result.blocked_by is not None
        self.assertEqual(result.blocked_by.trade_date, "2026-08-04")
        # 已确认的 HOLD 仍如实记账。
        self.assertEqual([s.trade_date for s in result.skipped], ["2026-08-05"])

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
        # 「翻完了」只在**全程都是经过校验的 HOLD** 时才说得出口——证据链
        # 没断，所以确实是「这些工件里没有再平衡日」。
        self.assertFalse(result.unknowable)
        self.assertIsNone(result.blocked_by)

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
        #
        # 日期取**生产真会产出的**序列:每个交易日一份。07-31 是周五、08-03
        # 是周一,中间只隔周末——那段证明得了没有交易日。原来的夹具从 08-03
        # 直接跳到 07-27,中间四个工作日没有工件,而那正是「缺口闸」要拦的
        # 形态(codex P1 第三轮)。一份生产产不出的夹具证明不了任何读侧行为。
        result = find_nominal_baseline(
            _index("2026-08-04", "2026-08-03", "2026-07-31"),
            read_payload=_reader({
                "2026-08-04": _artifact(as_of="2026-08-04", rebalance=True),
                "2026-08-03": _artifact(as_of="2026-08-03", rebalance=False),
                "2026-07-31": _artifact(as_of="2026-07-31", rebalance=True),
            }),
            as_of="2026-08-03",
        )

        self.assertEqual(result.baseline_date, "2026-07-31")
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

        # 不断言「找得到」——本机当下的正确答案很可能是「不可知」。断言的是
        # **三种终局互斥且自洽**，且每一条记账都给出了具体原因。
        terminal = [
            result.found, result.unknowable,
            (not result.found and not result.unknowable),
        ]
        self.assertEqual(sum(1 for t in terminal if t), 1)
        self.assertEqual(
            result.scanned,
            len(result.skipped) + (1 if result.found or result.unknowable else 0),
        )
        for candidate in result.skipped:
            with self.subTest(date=candidate.trade_date):
                # 记账里现在只该有**经过校验的 HOLD**。
                self.assertEqual(candidate.reason, BASELINE_SKIP_HOLD)
                self.assertTrue(candidate.detail)
        if result.unknowable:
            assert result.blocked_by is not None
            self.assertTrue(result.blocked_by.detail)
        elif not result.found:
            self.assertTrue(result.exhausted or result.limit_reached)


class ProducerShapeContractTests(unittest.TestCase):
    """回溯的形状闸与今日工作台**是同一道**。

    codex 在 #475 点出其中两条（节奏双字段只带其一、重复 stock_code）。补那
    两条会留下同一类的其余六条——所以整类下沉到共用的
    ``producer_shape_violation``，两处调同一个函数。这一组用例钉的是**整类**，
    外加两条「确实走的是那个函数」的防漂移钉。
    """

    def _blocked(self, payload: dict[str, Any]) -> Any:
        result = find_nominal_baseline(
            _index("2026-08-03"),
            read_payload=_reader({"2026-08-03": payload}),
        )
        self.assertTrue(
            result.unknowable,
            f"这份工件本该让回溯停下，实际 baseline_date={result.baseline_date!r}",
        )
        self.assertFalse(result.found)
        assert result.blocked_by is not None
        return result.blocked_by

    def test_every_producer_shape_violation_stops_the_scan(self) -> None:
        # 一行一类，覆盖 producer_shape_violation 的每一道闸。放行任何一条，
        # 都会让一份工作台判「需核查」的工件在履历里当上可信基准。
        cases: tuple[tuple[str, dict[str, Any]], ...] = (
            # —— 节奏组（codex #475 第一条：双字段必须成对） ——
            ("只带 rebalance_day、没有 next",
             _artifact(as_of="2026-08-03", rebalance=False, drop_next=True)),
            ("next 是非 ISO 的自由文本",
             _artifact(as_of="2026-08-03", rebalance=False,
                       next_rebalance="tomorrow")),
            ("next 非 str/null",
             _artifact(as_of="2026-08-03", rebalance=False, next_rebalance=123)),
            ("再平衡日的 next 不是 as_of 自身",
             _artifact(as_of="2026-08-03", rebalance=True,
                       next_rebalance="2026-09-07")),
            ("再平衡日的 next 是 null",
             _artifact(as_of="2026-08-03", rebalance=True, next_rebalance=None)),
            ("HOLD 日的 next 早于 entry",
             _artifact(as_of="2026-08-03", rebalance=False,
                       next_rebalance="2026-07-27")),
            ("HOLD 日的 next 落在周末",
             _artifact(as_of="2026-08-03", rebalance=False,
                       next_rebalance="2026-08-09")),
            # —— 清单组（codex #475 第二条：重复代码不许当基数） ——
            ("同一 stock_code 出现两次",
             _artifact(as_of="2026-08-03",
                       picks=[_pick("SH600584", 1, 0.26),
                              _pick("SH600584", 2, 0.19)])),
            ("行缺 stock_name（六键不全）",
             _artifact(as_of="2026-08-03", picks=[
                 {k: v for k, v in _pick("SH600584", 1, 0.26).items()
                  if k != "stock_name"}])),
            ("行标注自己不可交易",
             _artifact(as_of="2026-08-03", picks=[
                 {**_pick("SH600584", 1, 0.26), "tradable_flag": False}])),
            ("行带非空 unavailable_reason",
             _artifact(as_of="2026-08-03", picks=[
                 {**_pick("SH600584", 1, 0.26),
                  "unavailable_reason": "停牌"}])),
            ("predicted_score 是 NaN",
             _artifact(as_of="2026-08-03", picks=[
                 {**_pick("SH600584", 1, 0.26),
                  "predicted_score": float("nan")}])),
            ("rank 序列不是连续 1..N",
             _artifact(as_of="2026-08-03",
                       picks=[_pick("SH600584", 2, 0.26),
                              _pick("SZ000001", 3, 0.19)])),
            ("predicted_score 非降序",
             _artifact(as_of="2026-08-03",
                       picks=[_pick("SH600584", 1, 0.19),
                              _pick("SZ000001", 2, 0.26)])),
            ("候选条数超出 meta.topk",
             {**_artifact(as_of="2026-08-03"), "meta": {"topk": 1}}),
            ("meta.topk 缺失",
             {**_artifact(as_of="2026-08-03"), "meta": {"instruments": "csi800"}}),
            ("picks 不是列表",
             {**_artifact(as_of="2026-08-03"), "picks": {}}),
        )
        for label, payload in cases:
            with self.subTest(label):
                blocked = self._blocked(payload)
                self.assertEqual(blocked.reason, BASELINE_BLOCK_SHAPE)
                self.assertEqual(blocked.trade_date, "2026-08-03")
                self.assertTrue(blocked.detail)

    def test_a_missing_entry_date_is_already_stopped_upstream(self) -> None:
        # 形状闸拿 entry 当「HOLD 的 next 不早于 entry」的下界，所以它必须
        # 拿得到一个严格 ISO 的 entry。这里钉的正是那个前提:缺 entry 的工件
        # 在更上游的 entry-timing 闸就停下了，形状闸不会拿到空串。前提没了
        # （比如有人调换闸序），这条会红。
        payload = _artifact(as_of="2026-08-03")
        del payload["entry_date"]

        blocked = self._blocked(payload)

        self.assertEqual(blocked.reason, BASELINE_BLOCK_ENTRY_TIMING)

    def test_the_scan_actually_calls_the_shared_gate(self) -> None:
        # 防漂移①：换掉共用闸的实现，回溯必须跟着变。否则「两处同一道闸」
        # 只是注释里的说法，实现随时能各走各的。
        from web.operator_ui.pages import _daily_decision_helpers as helpers

        original = helpers.producer_shape_violation
        try:
            helpers.producer_shape_violation = (  # type: ignore[assignment]
                lambda payload, *, as_of_date, entry_date: "SENTINEL-SCAN")
            blocked = self._blocked(_artifact(as_of="2026-08-03"))
        finally:
            helpers.producer_shape_violation = original  # type: ignore[assignment]

        self.assertEqual(blocked.reason, BASELINE_BLOCK_SHAPE)
        self.assertEqual(blocked.detail, "SENTINEL-SCAN")

    def test_the_workbench_actually_calls_the_shared_gate(self) -> None:
        # 防漂移②：同一把钉扎在另一头。工作台若哪天把闸抄回本地一份，这条
        # 就红——而那正是 #475 这类「第二条更弱路径」的复发形状。
        from web.operator_ui.incumbent import IncumbentIdentity
        from web.operator_ui.pages import _today_workbench_helpers as workbench

        payload = {
            "artifact_schema_version": 2,
            "as_of_date": "2026-08-18",
            "entry_date": "2026-08-19",
            "rebalance_day": True,
            "next_rebalance_date": "2026-08-18",
            "meta": {"ensemble": {"manifest_sha256": "manifest"}, "topk": 50},
            "picks": [],
        }
        original = workbench.producer_shape_violation
        try:
            workbench.producer_shape_violation = (  # type: ignore[assignment]
                lambda payload, *, as_of_date, entry_date: "SENTINEL-WORKBENCH")
            summary = workbench.summarise_daily_signal(
                "2026-08-18", payload,
                incumbent=IncumbentIdentity(
                    kind="ensemble", manifest_sha256="manifest"),
                current_model_sha=None,
            )
        finally:
            workbench.producer_shape_violation = original  # type: ignore[assignment]

        self.assertEqual(summary.kind, "needs_verification")
        self.assertEqual(summary.detail, "SENTINEL-WORKBENCH")


class RosterDuplicateTests(unittest.TestCase):
    def test_duplicate_codes_are_rejected_rather_than_counted(self) -> None:
        # 报数的那一处自己也验（codex #475）：这个元组的长度就是页面上那句
        # 「共 N 只」。去重会悄悄少一只，照数会把两行一只标的报成两只。
        payload = _artifact(
            as_of="2026-08-03",
            picks=[_pick("SH600584", 1, 0.26), _pick("SH600584", 2, 0.19)],
        )

        with self.assertRaises(ValueError) as caught:
            baseline_roster(payload)

        self.assertIn("重复代码", str(caught.exception))
        self.assertIn("SH600584", str(caught.exception))



class HistoryGapTests(unittest.TestCase):
    """一份经过校验的 HOLD 只证明了**那一天**没换手。

    它对「没有工件的那些天」一无所知——而工件清单枚举的只是**存在的文件**。
    生产每个交易日出一次单，所以两份相邻工件之间夹着工作日，就是夹着一次
    没有记录的运行；那一天完全可能是再平衡日，会让更早那张单当场作废
    （codex P1 on #475 第三轮）。

    这是「只有正面证实的安全信号才许可继续」再深一层：HOLD 证实的是**那
    一天**，不是**那一段**。
    """

    def test_a_missing_weekday_between_two_artifacts_stops_the_scan(
        self,
    ) -> None:
        # 08-11(二) HOLD → 缺 08-10(一) → 08-03(一) 再平衡。照原样翻过去，
        # 页面会把 8 月 3 日那张单报成当前基准，而缺掉的 8 月 10 日那次运行
        # 完全可能已经取代了它。
        result = find_nominal_baseline(
            _index("2026-08-11", "2026-08-03"),
            read_payload=_reader({
                "2026-08-11": _artifact(as_of="2026-08-11", rebalance=False),
                "2026-08-03": _artifact(as_of="2026-08-03", rebalance=True),
            }),
        )

        self.assertFalse(result.found)
        self.assertTrue(result.unknowable)
        assert result.blocked_by is not None
        self.assertEqual(result.blocked_by.reason, BASELINE_BLOCK_HISTORY_GAP)
        self.assertEqual(result.blocked_by.trade_date, "2026-08-11")
        self.assertIn("2026-08-10", result.blocked_by.detail)

    def test_a_weekend_only_gap_is_provably_empty(self) -> None:
        # 周五 → 周一之间只隔周六周日。没有交易日历也证得了那两天不是交易日，
        # 所以这不是缺口——否则这道闸会把**每一个周末**都判成不可知。
        result = find_nominal_baseline(
            _index("2026-08-03", "2026-07-31"),
            read_payload=_reader({
                "2026-08-03": _artifact(as_of="2026-08-03", rebalance=False),
                "2026-07-31": _artifact(as_of="2026-07-31", rebalance=True),
            }),
        )

        self.assertEqual(result.baseline_date, "2026-07-31")

    def test_the_gap_between_the_selected_date_and_the_first_artifact_counts(
        self,
    ) -> None:
        # 同一道闸的第一格：选中日与最新那份工件之间同样可能夹着缺失的运行。
        result = find_nominal_baseline(
            _index("2026-08-03"),
            read_payload=_reader({
                "2026-08-03": _artifact(as_of="2026-08-03", rebalance=True),
            }),
            as_of="2026-08-05",
        )

        assert result.blocked_by is not None
        self.assertEqual(result.blocked_by.reason, BASELINE_BLOCK_HISTORY_GAP)
        self.assertIn("2026-08-04", result.blocked_by.detail)

    def test_the_selected_date_itself_is_not_a_gap(self) -> None:
        # 选中日自己就有工件时，区间是空的——第一格恒为空缺口，不是多余的闸。
        result = find_nominal_baseline(
            _index("2026-08-03"),
            read_payload=_reader({
                "2026-08-03": _artifact(as_of="2026-08-03", rebalance=True),
            }),
            as_of="2026-08-03",
        )

        self.assertEqual(result.baseline_date, "2026-08-03")


class UnaccountedWeekdaysTests(unittest.TestCase):
    def test_it_lists_only_weekdays(self) -> None:
        # 2026-08-07 周五 → 2026-08-11 周二：缺 08-10(一)，周末不算。
        self.assertEqual(
            unaccounted_weekdays_between("2026-08-07", "2026-08-11"),
            ("2026-08-10",),
        )

    def test_adjacent_days_have_no_interval(self) -> None:
        self.assertEqual(
            unaccounted_weekdays_between("2026-08-03", "2026-08-04"), ())

    def test_the_same_day_has_no_interval(self) -> None:
        self.assertEqual(
            unaccounted_weekdays_between("2026-08-03", "2026-08-03"), ())

    def test_an_unreadable_date_is_treated_as_a_gap(self) -> None:
        # 证不出「没有缺口」就当有缺口。返回空元组会让一对读不出来的日期
        # 静默放行——那正是这道闸要拦的形态。
        for older, newer in (
            ("not-a-date", "2026-08-11"),
            ("2026-08-03", "not-a-date"),
        ):
            with self.subTest(older=older, newer=newer):
                self.assertTrue(unaccounted_weekdays_between(older, newer))



if __name__ == "__main__":
    unittest.main()
