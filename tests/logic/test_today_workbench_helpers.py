"""Provenance and operation-state coverage for Today Workbench summaries."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from web.operator_ui.incumbent import IncumbentIdentity
from web.operator_ui.job_io import JobSummary
from web.operator_ui.pages._ops_cockpit_helpers import BundleFreshness
from web.operator_ui.pages._today_workbench_helpers import (
    SUPPORTED_DAILY_RECOMMENDATION_ARTIFACT_SCHEMA_VERSION,
    DailySignalSummary,
    model_age_rows,
    summarise_daily_signal,
    summarise_operations,
    todays_buy_answer,
)

_ROOT = Path(__file__).resolve().parents[2]

# provider fixture 按宿主构造：`D:/data/prov` 是 Windows 专属拼写，Ubuntu
# CI 腿上 unusable_path_reason 判它外来、快乐路径全体在门口 unanswerable
# （codex P1；本机绿≠CI绿的在档教训现场版）。
_PROV = os.path.join(os.path.abspath(os.sep), "data", "prov")
_PROV_OTHER = os.path.join(os.path.abspath(os.sep), "data", "other")


def _pick(rank: int, code: str) -> dict[str, object]:
    """产出器 RecommendationPick 形态的一行合法候选（六键六型恒写）。"""
    return {
        "rank": rank,
        "stock_code": code,
        "stock_name": f"股票{rank}",
        "predicted_score": 0.1 * rank,
        "tradable_flag": True,
        "unavailable_reason": "",
    }


def _ensemble_payload(
    *, rebalance_day: bool = True, manifest: str = "manifest"
) -> dict[str, object]:
    return {
        "artifact_schema_version": 2,
        "as_of_date": "2026-08-18",
        "entry_date": "2026-08-19",
        "rebalance_day": rebalance_day,
        "next_rebalance_date": "2026-08-25",
        "meta": {"ensemble": {"manifest_sha256": manifest}},
        "picks": [],
    }


class DailySignalSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.incumbent = IncumbentIdentity(
            kind="ensemble", manifest_sha256="manifest"
        )

    def test_matching_rebalance_artifact_is_not_an_execution_instruction(self) -> None:
        result = summarise_daily_signal(
            "2026-08-18",
            _ensemble_payload(),
            incumbent=self.incumbent,
            current_model_sha=None,
        )
        self.assertEqual(result.kind, "rebalance")
        self.assertIn("人工核对", result.detail)
        self.assertEqual(result.entry_date, "2026-08-19")

    def test_matching_hold_artifact_is_explicitly_non_actionable(self) -> None:
        result = summarise_daily_signal(
            "2026-08-18",
            _ensemble_payload(rebalance_day=False),
            incumbent=self.incumbent,
            current_model_sha=None,
        )
        self.assertEqual(result.kind, "hold")
        self.assertIn("不构成入场指令", result.detail)

    def test_provenance_or_payload_mismatch_never_becomes_a_signal(self) -> None:
        cases = (
            ("other manifest", _ensemble_payload(manifest="other")),
            (
                "wrong payload date",
                {**_ensemble_payload(), "as_of_date": "2026-08-17"},
            ),
            (
                "corrupt v2",
                {
                    "artifact_schema_version": 2,
                    "as_of_date": "2026-08-18",
                    "entry_date": "2026-08-19",
                },
            ),
        )
        for label, payload in cases:
            with self.subTest(label=label):
                result = summarise_daily_signal(
                    "2026-08-18",
                    payload,
                    incumbent=self.incumbent,
                    current_model_sha=None,
                )
                self.assertEqual(result.kind, "needs_verification")

    def test_invalid_or_nonforward_entry_date_never_becomes_a_signal(self) -> None:
        cases = (
            ("malformed", "tomorrow"),
            ("same session", "2026-08-18"),
            ("earlier session", "2026-08-17"),
        )
        for label, entry_date in cases:
            with self.subTest(label=label):
                payload = _ensemble_payload()
                payload["entry_date"] = entry_date
                result = summarise_daily_signal(
                    "2026-08-18",
                    payload,
                    incumbent=self.incumbent,
                    current_model_sha=None,
                )
                self.assertEqual(result.kind, "needs_verification")
                self.assertIn("entry_date", result.detail)

    def test_invalid_picks_shape_never_becomes_a_current_signal(self) -> None:
        cases: tuple[tuple[str, object], ...] = (
            ("missing", None),
            ("not a list", "not-a-list"),
            ("non-object member", ["not-a-dict"]),
        )
        for label, picks in cases:
            with self.subTest(label=label):
                payload = _ensemble_payload()
                if label == "missing":
                    payload.pop("picks")
                else:
                    payload["picks"] = picks
                result = summarise_daily_signal(
                    "2026-08-18",
                    payload,
                    incumbent=self.incumbent,
                    current_model_sha=None,
                )
                self.assertEqual(result.kind, "needs_verification")
                self.assertIn("候选列表", result.detail)

    def test_empty_picks_remains_a_valid_rebalance_artifact(self) -> None:
        result = summarise_daily_signal(
            "2026-08-18",
            _ensemble_payload(),
            incumbent=self.incumbent,
            current_model_sha=None,
        )
        self.assertEqual(result.kind, "rebalance")

    def test_the_verified_summary_carries_the_pick_cardinality(self) -> None:
        # 空清单是合法产出——但基数必须随核验结果**传出去**：丢掉它，
        # 下游会把「再平衡日」当成「必有买入对象」（codex #468 P1）。
        empty = summarise_daily_signal(
            "2026-08-18", _ensemble_payload(),
            incumbent=self.incumbent, current_model_sha=None)
        self.assertEqual(0, empty.pick_count)
        payload = _ensemble_payload()
        payload["picks"] = [_pick(1, "SH600000"), _pick(2, "SZ000001")]
        two = summarise_daily_signal(
            "2026-08-18", payload,
            incumbent=self.incumbent, current_model_sha=None)
        self.assertEqual("rebalance", two.kind)
        self.assertEqual(2, two.pick_count)

    def test_a_pick_row_off_contract_never_counts_as_a_candidate(self) -> None:
        # `picks: [{}]` 数不出任何可买标的，却会把基数抬成 1、让最显眼的
        # 卡说「有指令 · 1 只候选」（codex P2）。产出器 RecommendationPick
        # 六键恒写——违约=需核查，不做静默缩数。逐键各试一个坏形态。
        cases: tuple[tuple[str, dict], ...] = (
            ("空对象", {}),
            ("空 stock_code", {**_pick(1, "SH600000"), "stock_code": " "}),
            ("rank 布尔", {**_pick(1, "SH600000"), "rank": True}),
            ("score 字符串", {**_pick(1, "SH600000"),
                              "predicted_score": "0.1"}),
            ("tradable 非布尔", {**_pick(1, "SH600000"),
                                 "tradable_flag": "yes"}),
            # 产出器只落可交易行（构造前过滤 + 构造器写死 True/""）——
            # 工件自己标注不可交易的行不许计入候选数（codex P2）。
            ("tradable False", {**_pick(1, "SH600000"),
                                "tradable_flag": False}),
            ("reason 非空", {**_pick(1, "SH600000"),
                             "unavailable_reason": "suspension"}),
            ("缺 unavailable_reason",
             {k: v for k, v in _pick(1, "SH600000").items()
              if k != "unavailable_reason"}),
            ("缺 stock_name",
             {k: v for k, v in _pick(1, "SH600000").items()
              if k != "stock_name"}),
        )
        for label, bad in cases:
            with self.subTest(label=label):
                payload = _ensemble_payload()
                payload["picks"] = [bad]
                got = summarise_daily_signal(
                    "2026-08-18", payload,
                    incumbent=self.incumbent, current_model_sha=None)
                self.assertEqual("needs_verification", got.kind,
                                 "违约行被当成了一只候选")
                self.assertIn("违约", got.detail)

    def test_the_verified_summary_retains_the_data_provenance(self) -> None:
        # 产出器写下的 meta.provider_uri / meta.bundle_tag 必须随核验结果
        # 传出去——丢掉它，provider 切换或 bundle 重建后，别的 bundle 的
        # 工件按日期巧合也能冒充「最新」（codex #468 P1）。
        payload = _ensemble_payload()
        meta = dict(payload["meta"])  # type: ignore[arg-type]
        meta["provider_uri"] = "D:/data/prov"
        meta["bundle_tag"] = "tag-1"
        meta["bundle_built_at"] = "2026-08-25T21:00:00+08:00"
        payload["meta"] = meta
        got = summarise_daily_signal(
            "2026-08-18", payload,
            incumbent=self.incumbent, current_model_sha=None)
        self.assertEqual("D:/data/prov", got.data_provider_uri)
        self.assertEqual("tag-1", got.data_bundle_tag)
        self.assertEqual("2026-08-25T21:00:00+08:00", got.data_bundle_built_at)
        # 产出器侧源码钉：nonce 真从 stamp 的 built_at 来、真落进 meta——
        # 读侧比对的前提是写侧真的在写（防两侧各自为政）。
        producer = (
            _ROOT / "src" / "inference" / "daily_recommend.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"bundle_built_at": bundle_built_at', producer)
        self.assertIn(
            "bundle_built_at = integrity.built_at if integrity is not None",
            producer)
        # 缺失时如实 None（老工件形态）——不冒充有来源。
        bare = summarise_daily_signal(
            "2026-08-18", _ensemble_payload(),
            incumbent=self.incumbent, current_model_sha=None)
        self.assertIsNone(bare.data_provider_uri)
        self.assertIsNone(bare.data_bundle_tag)

    def test_a_mistyped_provenance_is_unverifiable_not_absent(self) -> None:
        # 在场但类型违约 ≠ 缺席：把 `123` 静默降成 None 会借道「合法缺身份
        # 块」绕开 bundle 比对（codex P2）。产出器只写 str / str|null。
        for field, value in (("provider_uri", 123), ("bundle_tag", 123),
                             ("bundle_tag", True), ("bundle_built_at", 123)):
            with self.subTest(field=field, value=value):
                payload = _ensemble_payload()
                meta = dict(payload["meta"])  # type: ignore[arg-type]
                meta[field] = value
                payload["meta"] = meta
                got = summarise_daily_signal(
                    "2026-08-18", payload,
                    incumbent=self.incumbent, current_model_sha=None)
                self.assertEqual("needs_verification", got.kind,
                                 "类型违约被降级成了缺席")
                self.assertIn(field, got.detail)

    def test_a_malformed_next_rebalance_date_is_unverifiable(self) -> None:
        # 产出器只写严格 ISO 或 null（日历尾附近合法 None）——hold_state
        # 刻意宽容，`123`/"tomorrow" 会被头卡当成已核验 HOLD 宣布「无需
        # 动作」（codex P2）。验约在核验层，宽容展示层不动。
        for label, value, expect in (
            ("int", 123, "needs_verification"),
            ("非 ISO", "tomorrow", "needs_verification"),
            ("宽 ISO", "2026-8-25", "needs_verification"),
            # 产出器契约 next >= d 且 HOLD 日 as_of 非再平衡日 → 严格大于；
            # 过去/当日值产出器产不出（codex P2）。
            ("过去日期", "2026-08-01", "needs_verification"),
            ("等于 as_of", "2026-08-18", "needs_verification"),
            ("null 合法", None, "hold"),
        ):
            with self.subTest(label=label):
                payload = _ensemble_payload(rebalance_day=False)
                payload["next_rebalance_date"] = value
                got = summarise_daily_signal(
                    "2026-08-18", payload,
                    incumbent=self.incumbent, current_model_sha=None)
                self.assertEqual(expect, got.kind,
                                 "损坏节奏日期被当成了已核验 HOLD")
                if expect == "needs_verification":
                    self.assertIn("next_rebalance_date", got.detail)

    def test_duplicate_pick_codes_are_unverifiable(self) -> None:
        # 同一 stock_code 两行是产出器产不出的（_scores_to_inst_map 的
        # unique-instruments 守卫在构造 picks 前 fail-loud）——逐行验约看
        # 不见跨行重复，基数会把一只标的报成 2 只候选（codex P2）。
        payload = _ensemble_payload()
        payload["picks"] = [_pick(1, "SH600000"), _pick(2, "SH600000")]
        got = summarise_daily_signal(
            "2026-08-18", payload,
            incumbent=self.incumbent, current_model_sha=None)
        self.assertEqual("needs_verification", got.kind,
                         "重复代码被数成了两只候选")
        self.assertIn("SH600000", got.detail)

    def test_a_rebalance_day_next_equal_to_as_of_is_legal(self) -> None:
        # 再平衡日本身就是锚：next_rebalance_date(d) == d 合法，不受 HOLD
        # 侧「必须在未来」的限制。
        payload = _ensemble_payload()
        payload["next_rebalance_date"] = "2026-08-18"
        got = summarise_daily_signal(
            "2026-08-18", payload,
            incumbent=self.incumbent, current_model_sha=None)
        self.assertEqual("rebalance", got.kind,
                         "再平衡日的 next==as_of 被误伤")

    def test_missing_or_unsupported_schema_never_becomes_current_signal(self) -> None:
        cases: tuple[tuple[str, object], ...] = (
            ("missing", None),
            ("boolean", True),
            ("string", "2"),
            ("future", 999),
        )
        for label, version in cases:
            with self.subTest(label=label):
                payload = _ensemble_payload()
                if label == "missing":
                    payload.pop("artifact_schema_version")
                else:
                    payload["artifact_schema_version"] = version
                result = summarise_daily_signal(
                    "2026-08-18",
                    payload,
                    incumbent=self.incumbent,
                    current_model_sha=None,
                )
                self.assertEqual(result.kind, "needs_verification")
                self.assertIn("schema", result.detail)

    def test_workbench_version_is_pinned_to_named_producer_contract(self) -> None:
        producer = (
            _ROOT / "src" / "inference" / "daily_recommend.py"
        ).read_text(encoding="utf-8")
        self.assertEqual(SUPPORTED_DAILY_RECOMMENDATION_ARTIFACT_SCHEMA_VERSION, 2)
        self.assertIn(
            "DAILY_RECOMMENDATION_ARTIFACT_SCHEMA_VERSION: Final[int] = 2",
            producer,
        )
        self.assertIn(
            '"artifact_schema_version": DAILY_RECOMMENDATION_ARTIFACT_SCHEMA_VERSION',
            producer,
        )


def _job(*, run_id: str, status: str, finished_at: str = "") -> JobSummary:
    return JobSummary(
        run_id=run_id,
        type="pipeline",
        status=status,
        finished_at=finished_at,
        error_message="failed detail" if status == "failed" else "",
    )


class OperationSummaryTests(unittest.TestCase):
    def test_running_job_has_priority_over_prior_failure(self) -> None:
        result = summarise_operations(
            (
                _job(
                    run_id="old-failure",
                    status="failed",
                    finished_at="2026-08-18T09:00:00Z",
                ),
                _job(
                    run_id="current",
                    status="running",
                    finished_at="2026-08-18T10:00:00Z",
                ),
            )
        )
        self.assertEqual(result.kind, "running")
        self.assertEqual(result.job.run_id if result.job else None, "current")

    def test_pending_job_is_not_misreported_as_idle(self) -> None:
        result = summarise_operations(
            (_job(run_id="queued", status="pending"),)
        )
        self.assertEqual(result.kind, "pending")
        self.assertEqual(result.job.run_id if result.job else None, "queued")

    def test_latest_exception_is_surfaced_when_nothing_is_running(self) -> None:
        result = summarise_operations(
            (
                _job(
                    run_id="old",
                    status="failed",
                    finished_at="2026-08-18T09:00:00Z",
                ),
                _job(
                    run_id="new",
                    status="stopped",
                    finished_at="2026-08-18T10:00:00Z",
                ),
            )
        )
        self.assertEqual(result.kind, "attention")
        self.assertEqual(result.job.run_id if result.job else None, "new")

    def test_partial_job_is_surfaced_as_an_exception(self) -> None:
        result = summarise_operations(
            (_job(run_id="partial", status="partial"),)
        )
        self.assertEqual(result.kind, "attention")
        self.assertEqual(result.job.run_id if result.job else None, "partial")


if __name__ == "__main__":
    unittest.main()


class ModelAgeRowsMirrorTheCockpitDerivation(unittest.TestCase):
    """身份卡的模型时效行——数据照抄生产运维页的 retrain_window，零自造。

    P3 缺口（UI 序列③）：措辞层只翻译字段；known=False 如实说推导不了。
    """

    def test_a_known_window_yields_the_three_rows(self) -> None:
        from web.operator_ui.pages._ops_cockpit_helpers import RetrainWindow
        window = RetrainWindow(
            known=True, newest_fit_end="2026-04-01", days_since_newest=146,
            opens_on="2026-06-15", closes_on="2026-07-10", state="closed",
            days_closed=46, gap_if_fit_today=146, refused_if_fit_today=True)
        rows = model_age_rows(window)
        self.assertEqual("fit 至", rows[0][0])
        self.assertEqual("2026-04-01", rows[0][1])
        self.assertEqual(("模型年龄", "146 天"), rows[1])
        self.assertIn("2026-06-15~2026-07-10", rows[2][1])
        self.assertIn("已过", rows[2][1], "closed 态没有如实翻译")

    def test_the_window_row_discloses_its_derived_identity_visibly(self) -> None:
        # 披露契约（codex P1）：窗口走到哪，「推导 + 无机器可读到期锚」的
        # 告白就要跟到哪——且必须在**可见文案**里（label/value），docstring
        # 不渲染不算。数值 pin 也要在场，操作人才知道推导依据是什么。
        from web.operator_ui.pages._ops_cockpit_helpers import RetrainWindow
        window = RetrainWindow(
            known=True, newest_fit_end="2026-04-01", days_since_newest=146,
            opens_on="2026-06-15", closes_on="2026-07-10", state="closed",
            days_closed=46, gap_if_fit_today=146, refused_if_fit_today=True)
        label, value = model_age_rows(window)[2]
        self.assertIn("推导", label, "窗口行 label 没自报推导身份")
        self.assertIn(f"[{window.spacing_min},{window.spacing_max}]", value,
                      "推导依据（spacing pin 数值）不在可见文案里")
        self.assertIn("无机器可读", value, "缺「仓库无到期锚」的告白")

    def test_an_unknown_window_is_stated_not_blank(self) -> None:
        # 原因必须**原样**活到可见行里（codex P2：硬编码「非可解析
        # ensemble」会错报 fit_end 非法这类同走 known=False 的失败）。
        from web.operator_ui.pages._ops_cockpit_helpers import RetrainWindow
        rows = model_age_rows(RetrainWindow(
            known=False, error="现任最新 fit_end 不是合法 ISO 日期"))
        self.assertEqual(1, len(rows))
        self.assertIn("无法推导", rows[0][1])
        self.assertIn("现任最新 fit_end 不是合法 ISO 日期", rows[0][1],
                      "契约给的原因没活到可见文案")
        self.assertNotIn("ensemble", rows[0][1], "又把一种失败硬编码成了全部")

    def test_an_unknown_window_without_a_reason_says_so(self) -> None:
        from web.operator_ui.pages._ops_cockpit_helpers import RetrainWindow
        rows = model_age_rows(RetrainWindow(known=False, error=""))
        self.assertIn("原因未记录", rows[0][1], "空原因得明说，不能留白")

    def test_the_page_wires_the_cockpit_function_not_a_copy(self) -> None:
        # 接线钉：页面必须消费 ops_cockpit 的同一个 retrain_window——
        # 另写一份正是 #461 三决策全错的老路（干净数据上接线不可测，钉源码）。
        source = (Path(__file__).resolve().parents[2] / "web" / "operator_ui"
                  / "pages" / "today_workbench.py").read_text(encoding="utf-8")
        self.assertIn("model_age_rows(retrain_window(incumbent, cn_today()))",
                      source, "身份卡没有接生产运维页的同一推导")
        self.assertGreaterEqual(
            source.count(
                "model_age_rows(retrain_window(incumbent, cn_today()))"),
            3, "时效行没接到身份卡全部三个分支——unknown 的「无法推导+原因」"
               "到不了卡上，规格场景落空（codex P2）")



class TheTodaysAnswerIsSynthesizedNotInvented(unittest.TestCase):
    """「今天要不要买」合成句（UI 已批序列④）——零自造、零时钟。

    数据包前置=出单侧 usable 全额；节奏与候选基数=已核验信号分类；
    「新不新」= entry_date（已收盘会话）对出单侧日历尾，不比挂钟。
    """

    @staticmethod
    def _fresh(**overrides: object) -> BundleFreshness:
        base: dict[str, object] = dict(
            known=True, tail_date="2026-08-26", days_behind=0,
            max_age_days=14, headroom_days=14, refuses_today=False,
            integrity_accepted=True, provider_uri=_PROV,
            identity_tag="tag-1", built_at="2026-08-25T21:00:00+08:00")
        base.update(overrides)
        return BundleFreshness(**base)  # type: ignore[arg-type]

    @staticmethod
    def _signal(kind: str, **overrides: object) -> DailySignalSummary:
        base: dict[str, object] = dict(
            kind=kind, detail="x", as_of_date="2026-08-25",
            entry_date="2026-08-26", pick_count=5,
            data_provider_uri=_PROV, data_bundle_tag="tag-1",
            data_bundle_built_at="2026-08-25T21:00:00+08:00")
        base.update(overrides)
        return DailySignalSummary(**base)  # type: ignore[arg-type]

    def test_a_current_rebalance_names_count_and_closed_session(self) -> None:
        got = todays_buy_answer(self._signal("rebalance"), self._fresh())
        self.assertEqual("rebalance", got.state)
        self.assertIn("有再平衡指令", got.value)
        self.assertIn("人工核对", got.detail, "指令态不许省掉人工核对义务")
        self.assertIn("5 只候选", got.detail, "候选数没如实说出")

    def test_the_card_never_calls_entry_a_buy_day(self) -> None:
        # 基线契约（v2-daily-decision-page）：entry_date 是**已收盘会话**、
        # 清单不是「明早买入」指令、真实订单如何收敛是操作人的执行惯例
        # （codex #468 P1：把 entry 等同「今天买」会怂恿对已收盘价下单）。
        # 流程态（数据走过最新指令）也点名了 entry——披露必须跟到
        # （codex P2：只在现行三态披露违反本 change 规格）。
        for signal in (self._signal("rebalance"),
                       self._signal("rebalance", pick_count=0),
                       self._signal("hold"),
                       self._signal("rebalance", entry_date="2026-08-24")):
            got = todays_buy_answer(signal, self._fresh())
            with self.subTest(kind=signal.kind, picks=signal.pick_count,
                              entry=signal.entry_date):
                self.assertIn("已收盘会话", got.detail,
                              "已收盘披露没跟着工件走到本卡")
                self.assertNotIn("执行日", got.detail,
                                 "把已收盘会话说成了执行日")
                self.assertNotIn("今天（", got.detail,
                                 "又把挂钟日期塞回了指令语义")
        rebalance = todays_buy_answer(self._signal("rebalance"), self._fresh())
        self.assertIn("执行惯例", rebalance.detail,
                      "收敛方式归执行惯例这句丢了")

    def test_a_current_hold_says_watch_and_names_the_next_day(self) -> None:
        got = todays_buy_answer(
            self._signal("hold", next_rebalance_date="2026-08-28"),
            self._fresh())
        self.assertEqual("watch", got.state)
        self.assertIn("不动", got.value)
        self.assertIn("2026-08-28", got.detail, "下一再平衡日没说出来")

    def test_an_empty_rebalance_list_is_not_an_instruction(self) -> None:
        # 空清单是合法产出（--topk 0 / 全部候选被掩蔽，codex P1）。
        got = todays_buy_answer(
            self._signal("rebalance", pick_count=0), self._fresh())
        self.assertEqual("watch", got.state)
        self.assertIn("清单为空", got.value)
        self.assertIn("没有买入对象", got.detail)

    def test_a_rebalance_without_a_cardinality_refuses_to_answer(self) -> None:
        got = todays_buy_answer(
            self._signal("rebalance", pick_count=None), self._fresh())
        self.assertEqual("unanswerable", got.state)
        self.assertIn("候选数", got.detail)

    def test_staleness_beats_a_seemingly_current_instruction(self) -> None:
        # 优先级钉：出单侧今天会拒时，即使工件看起来对得上数据尾也拒答。
        got = todays_buy_answer(
            self._signal("rebalance"),
            self._fresh(refuses_today=True, days_behind=15, headroom_days=0))
        self.assertEqual("unanswerable", got.state)
        self.assertIn("15", got.detail, "落后天数没如实说出")
        self.assertIn("14", got.detail, "出单上限没如实说出")

    def test_an_integrity_refusal_is_stated_with_its_reason(self) -> None:
        got = todays_buy_answer(
            self._signal("rebalance"),
            self._fresh(integrity_accepted=False,
                        integrity_reason="built_from_holey_fetch"))
        self.assertEqual("unanswerable", got.state)
        self.assertIn("built_from_holey_fetch", got.detail)

    def test_an_unevaluated_integrity_gate_is_not_treated_as_open(self) -> None:
        got = todays_buy_answer(
            self._signal("rebalance"), self._fresh(integrity_accepted=None))
        self.assertEqual("unanswerable", got.state)
        self.assertIn("完整性", got.detail)

    def test_a_health_precondition_failure_blocks_the_answer(self) -> None:
        # 年龄与完整性都过、健康摘要仍扣分——usable=False 时给出指令会与
        # 同页健康卡自相矛盾（codex P1）。
        got = todays_buy_answer(
            self._signal("rebalance"),
            self._fresh(health_status="error",
                        health_warnings=("instruments 目录缺失",)))
        self.assertEqual("unanswerable", got.state)
        self.assertIn("instruments 目录缺失", got.detail,
                      "健康扣分的原因没活到可见文案")
        self.assertIn("健康状态 error", got.detail)

    def test_an_unreachable_freshness_verdict_is_stated(self) -> None:
        got = todays_buy_answer(
            self._signal("rebalance"),
            self._fresh(known=False, refuses_today=None,
                        integrity_accepted=None, tail_date=None,
                        message="日历尾不可读"))
        self.assertEqual("unanswerable", got.state)
        self.assertIn("日历尾不可读", got.detail, "裁决不可达的原因没活下来")

    def test_a_verdict_without_a_tail_cannot_compare_currency(self) -> None:
        got = todays_buy_answer(
            self._signal("rebalance"), self._fresh(tail_date=None))
        self.assertEqual("unanswerable", got.state)
        self.assertIn("日历尾", got.detail)

    def test_a_missing_artifact_is_a_flow_state_not_an_error(self) -> None:
        got = todays_buy_answer(
            DailySignalSummary("missing", "尚无日度信号工件。"), self._fresh())
        self.assertEqual("no_instruction", got.state)
        self.assertIn("运行中心", got.detail)

    def test_an_unverified_artifact_refuses_to_answer(self) -> None:
        got = todays_buy_answer(
            self._signal("needs_verification",
                         detail="工件来源无法与现任模型确认。"),
            self._fresh())
        self.assertEqual("unanswerable", got.state)
        self.assertIn("工件来源无法与现任模型确认", got.detail,
                      "核查原因没活下来")

    def test_data_moving_past_the_instruction_is_a_flow_state(self) -> None:
        # 数据尾走到了最新指令之后 = 出单没跟上——流程态，两个日期点名。
        got = todays_buy_answer(
            self._signal("rebalance", entry_date="2026-08-24"), self._fresh())
        self.assertEqual("no_instruction", got.state)
        self.assertIn("2026-08-24", got.detail, "旧指令的会话没点名")
        self.assertIn("2026-08-26", got.detail, "数据尾没点名")
        self.assertIn("运行中心", got.detail)

    def test_an_instruction_ahead_of_the_data_tail_is_abnormal(self) -> None:
        # 产出器出不了未收盘会话的清单——工件声称的会话晚于数据尾，
        # 两侧必有一侧在说谎，拒答并点名两个日期。
        got = todays_buy_answer(
            self._signal("rebalance", entry_date="2026-08-27"), self._fresh())
        self.assertEqual("unanswerable", got.state)
        self.assertIn("2026-08-27", got.detail)
        self.assertIn("2026-08-26", got.detail)

    def test_an_artifact_from_another_provider_is_refused(self) -> None:
        # 数据来源绑定（codex P1）：provider 切换后，旧 provider 的工件按
        # 日期巧合也能对上尾——而全页健康检查说的都是当前数据。
        got = todays_buy_answer(
            self._signal("rebalance", data_provider_uri=_PROV_OTHER),
            self._fresh())
        self.assertEqual("unanswerable", got.state)
        self.assertIn(_PROV_OTHER, got.detail, "工件侧 provider 没点名")
        self.assertIn(_PROV, got.detail, "当前侧 provider 没点名")

    def test_an_artifact_from_a_rebuilt_bundle_is_refused(self) -> None:
        # 同 provider、bundle 原地重建（身份戳换了）——不是这份数据的信号。
        got = todays_buy_answer(
            self._signal("rebalance", data_bundle_tag="tag-0"), self._fresh())
        self.assertEqual("unanswerable", got.state)
        self.assertIn("tag-0", got.detail)
        self.assertIn("tag-1", got.detail)

    def test_a_missing_artifact_provenance_is_refused(self) -> None:
        # v2 产出器无条件写 meta.provider_uri——缺失即需核查，不猜来源。
        got = todays_buy_answer(
            self._signal("rebalance", data_provider_uri=None), self._fresh())
        self.assertEqual("unanswerable", got.state)
        self.assertIn("meta.provider_uri", got.detail)

    def test_an_unidentified_current_side_cannot_bind(self) -> None:
        got = todays_buy_answer(
            self._signal("rebalance"), self._fresh(provider_uri=None))
        self.assertEqual("unanswerable", got.state)
        self.assertIn("provider 身份", got.detail)

    def test_a_nul_in_a_provider_spelling_refuses_not_crashes(self) -> None:
        # 内嵌 NUL 的拼写会让归一化的 realpath 抛 ValueError——整页变
        # traceback 而不是规格要求的拒答（codex P2）。既有 unusable_path_
        # reason 边界（NUL 先于任何文件系统调用）在比对前对称把门。
        for side, overrides in (
            ("工件侧", {"signal": {"data_provider_uri": "D:/da\x00ta/prov"}}),
            ("出单侧", {"fresh": {"provider_uri": "D:/da\x00ta/prov"}}),
        ):
            with self.subTest(side=side):
                got = todays_buy_answer(
                    self._signal("rebalance",
                                 **overrides.get("signal", {})),
                    self._fresh(**overrides.get("fresh", {})))
                self.assertEqual("unanswerable", got.state)
                self.assertIn("拼写不可用", got.detail)

    def test_a_blank_provider_spelling_refuses_on_both_sides(self) -> None:
        # 空/全空白是产出器产不出的拼写，而路径边界刻意放行空串——归一化
        # 会解析成进程 CWD：Streamlit 恰从 bundle 目录启动时损坏工件就
        # 绑定成功（codex P2）。两侧各自拒答。
        for side, overrides in (
            ("工件空串", {"signal": {"data_provider_uri": ""}}),
            ("工件全空白", {"signal": {"data_provider_uri": "   "}}),
            ("出单侧全空白", {"fresh": {"provider_uri": "   "}}),
        ):
            with self.subTest(side=side):
                got = todays_buy_answer(
                    self._signal("rebalance", **overrides.get("signal", {})),
                    self._fresh(**overrides.get("fresh", {})))
                self.assertEqual("unanswerable", got.state,
                                 "空白拼写被归一化成 CWD 绑定了")
                # 钉到**具体原因**：空白必须被空白门拒，而不是碰巧被
                # 「另一个 provider」的 mismatch 兜住（那条在 CWD 恰好
                # 等于 provider 目录时会放行——正是本洞的形状）。
                self.assertNotIn("另一个 provider", got.detail,
                                 "空白走了 mismatch 兜底而非专门的门")
                if side.startswith("工件"):
                    self.assertIn("产出器产不出", got.detail)
                else:
                    self.assertIn("未带 provider 身份", got.detail)

    def test_incidental_whitespace_around_a_relative_spelling_binds(self) -> None:
        # 出单器归一化第一步就是 strip（" data/prov " = incidental
        # whitespace）；先锚后 strip 会拼出 `<repo>/ data/prov ` 另一条路径，
        # 合法工件被误判外来（codex P1）。
        from web.operator_ui.incumbent import anchored_to_repo
        got = todays_buy_answer(
            self._signal("rebalance", data_provider_uri=" data/prov "),
            self._fresh(provider_uri=anchored_to_repo("data/prov")))
        self.assertEqual("rebalance", got.state,
                         "围空白的相对拼写被先锚后 strip 误判外来")

    def test_a_relative_artifact_provider_binds_regardless_of_cwd(self) -> None:
        # meta.provider_uri 可为相对拼写（生产配置语境=仓根）；Streamlit 从
        # 仓外启动时进程 CWD ≠ 仓根——按 CWD 归一会让**同一份** bundle 比
        # 不相等，最显眼的卡片假拒一份有效指令（codex P1）。同锚
        # （anchored_to_repo）后再走出单器归一化，比对与 CWD 无关。
        import os as _os
        import tempfile as _tf

        from web.operator_ui.incumbent import anchored_to_repo
        signal = self._signal("rebalance", data_provider_uri="data/prov")
        fresh = self._fresh(provider_uri=anchored_to_repo("data/prov"))
        old_cwd = _os.getcwd()
        with _tf.TemporaryDirectory() as t:
            try:
                _os.chdir(t)   # 模拟仓外启动的进程 CWD
                got = todays_buy_answer(signal, fresh)
            finally:
                _os.chdir(old_cwd)
        self.assertEqual("rebalance", got.state,
                         "相对拼写被按进程 CWD 归一——仓外启动时假拒")

    def test_an_in_place_rebuild_is_refused_by_the_built_at_nonce(self) -> None:
        # tag 只含日历尾+day.txt 哈希——宇宙/bin 变了而日历没变的原地重建
        # 它看不见（BundleIdentity docstring 明言非 full-bin 保证）；
        # built_at 每次重建都刷新（codex 二轮 P1）。provider 与 tag 都对得
        # 上、nonce 不同 → 拒答并点名两个时刻。
        got = todays_buy_answer(
            self._signal("rebalance",
                         data_bundle_built_at="2026-08-24T21:00:00+08:00"),
            self._fresh())
        self.assertEqual("unanswerable", got.state)
        self.assertIn("原地重建", got.detail)
        self.assertIn("2026-08-24T21:00:00+08:00", got.detail)
        self.assertIn("2026-08-25T21:00:00+08:00", got.detail)

    def test_a_missing_built_at_degrades_honestly(self) -> None:
        # 老工件无 bundle_built_at 键 / 无 stamp 无 built_at——合法缺席，
        # 按已比对的 provider/tag 绑定放行，不因此拒答。
        for overrides in ({"signal": {"data_bundle_built_at": None}},
                          {"fresh": {"built_at": None}}):
            with self.subTest(缺侧="工件" if "signal" in overrides else "当前"):
                got = todays_buy_answer(
                    self._signal("rebalance",
                                 **overrides.get("signal", {})),
                    self._fresh(**overrides.get("fresh", {})))
                self.assertEqual("rebalance", got.state,
                                 "合法缺席被当成了拒答理由")

    def test_a_missing_identity_tag_degrades_to_provider_binding(self) -> None:
        # 身份块是 stamp 的可选项（pre-PR-G+I 无块合法）——单侧缺 tag 时
        # 按 provider 绑定放行，不冒充比过，也不因此拒答。
        for overrides in ({"data_bundle_tag": None}, {}):
            fresh = (self._fresh(identity_tag=None)
                     if not overrides else self._fresh())
            got = todays_buy_answer(
                self._signal("rebalance", **overrides), fresh)
            with self.subTest(缺侧="工件" if overrides else "当前"):
                self.assertEqual("rebalance", got.state,
                                 "单侧无身份块不该拒答——provider 已绑定")

    def test_a_cadence_one_daily_artifact_is_an_executable_list(self) -> None:
        # 缺 rebalance_day = cadence-1 的 legacy daily 工件——契约明文
        # （hold_state：ABSENT=legacy、is_hold=False）每日皆为可执行清单，
        # 详情页同样按可执行渲染。此前把它拒答（「无节奏标记」）会让整个
        # cadence-1 部署形态的头卡永远哑火（codex P1）。
        got = todays_buy_answer(self._signal("daily"), self._fresh())
        self.assertEqual("rebalance", got.state,
                         "cadence-1 工件被拒答——头卡对该部署形态哑火")
        self.assertIn("5 只候选", got.detail)
        empty = todays_buy_answer(
            self._signal("daily", pick_count=0), self._fresh())
        self.assertEqual("watch", empty.state)
        self.assertIn("清单为空", empty.value)

    def test_a_lone_surrogate_spelling_refuses_not_crashes(self) -> None:
        # JSON 能表示孤立代理字符（"\ud800"，损坏可达）；路径边界放行它，
        # 而 POSIX 的 realpath 编码不了直接抛 UnicodeEncodeError——归一化
        # 只接 OSError/ValueError，整页崩（codex P2，NUL 同类）。
        for side, overrides in (
            ("工件侧", {"signal": {
                "data_provider_uri": "D:/da" + chr(0xD800) + "ta/prov"}}),
            ("出单侧", {"fresh": {
                "provider_uri": "D:/da" + chr(0xD800) + "ta/prov"}}),
        ):
            with self.subTest(side=side):
                got = todays_buy_answer(
                    self._signal("rebalance", **overrides.get("signal", {})),
                    self._fresh(**overrides.get("fresh", {})))
                self.assertEqual("unanswerable", got.state)
                self.assertIn("代理字符", got.detail)

    def test_every_state_carries_the_not_an_order_disclaimer(self) -> None:
        answers = [
            todays_buy_answer(self._signal("rebalance"), self._fresh()),
            todays_buy_answer(self._signal("hold"), self._fresh()),
            todays_buy_answer(DailySignalSummary("missing", "尚无。"),
                              self._fresh()),
            todays_buy_answer(self._signal("rebalance"),
                              self._fresh(refuses_today=True)),
        ]
        self.assertEqual(
            {"rebalance", "watch", "no_instruction", "unanswerable"},
            {a.state for a in answers}, "四态没有各出一个代表")
        for answer in answers:
            with self.subTest(state=answer.state):
                self.assertIn("不是订单", answer.detail,
                              "合成句丢了「不是订单」的免责声明")

    def test_the_page_wires_the_synthesis_with_the_shared_verdicts(self) -> None:
        # 接线钉：合成句必须消费页面里那份出单侧裁决（零时钟——挂钟参数
        # 已按 codex P1 移除），且四个状态都有显式配色。
        source = (Path(__file__).resolve().parents[2] / "web" / "operator_ui"
                  / "pages" / "today_workbench.py").read_text(encoding="utf-8")
        self.assertIn("todays_buy_answer(signal, _freshness)",
                      source, "合成句没有接共享裁决")
        for state in ("rebalance", "watch", "no_instruction", "unanswerable"):
            self.assertIn(f'"{state}":', source,
                          f"页面没有为 {state} 配色")
