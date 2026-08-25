"""Provenance and operation-state coverage for Today Workbench summaries."""

from __future__ import annotations

import unittest
from datetime import date
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



class TheTodaysAnswerIsSynthesizedNotInvented(unittest.TestCase):
    """「今天要不要买」合成句（UI 已批序列④）——三态 + 如实边缘。

    helper 零自造判定：陈旧/完整性来自出单侧裁决（BundleFreshness），节奏来
    自已核验来源的 summarise_daily_signal，「说给今天」只看 entry_date。
    """

    _TODAY = date(2026, 8, 26)

    @staticmethod
    def _fresh(**overrides: object) -> BundleFreshness:
        base: dict[str, object] = dict(
            known=True, tail_date="2026-08-25", days_behind=1,
            max_age_days=14, headroom_days=13, refuses_today=False,
            integrity_accepted=True)
        base.update(overrides)
        return BundleFreshness(**base)  # type: ignore[arg-type]

    @staticmethod
    def _signal(kind: str, **overrides: object) -> DailySignalSummary:
        base: dict[str, object] = dict(
            kind=kind, detail="x", as_of_date="2026-08-25",
            entry_date="2026-08-26")
        base.update(overrides)
        return DailySignalSummary(**base)  # type: ignore[arg-type]

    def test_a_rebalance_instruction_for_today_says_buy(self) -> None:
        got = todays_buy_answer(
            self._signal("rebalance"), self._fresh(), self._TODAY)
        self.assertEqual("buy", got.state)
        self.assertIn("有买入指令", got.value)
        self.assertIn("人工核对", got.detail, "买入态不许省掉人工核对义务")

    def test_a_hold_for_today_says_watch_and_names_the_next_day(self) -> None:
        got = todays_buy_answer(
            self._signal("hold", next_rebalance_date="2026-08-28"),
            self._fresh(), self._TODAY)
        self.assertEqual("watch", got.state)
        self.assertIn("不买", got.value)
        self.assertIn("2026-08-28", got.detail, "下一再平衡日没说出来")

    def test_staleness_beats_a_seemingly_current_instruction(self) -> None:
        # 优先级钉：出单侧今天会拒时，即使工件看起来是今天的也拒答——
        # 该组合正常流程到不了，真到了说明有一侧在说谎，拒答比选边站诚实。
        got = todays_buy_answer(
            self._signal("rebalance"),
            self._fresh(refuses_today=True, days_behind=15, headroom_days=0),
            self._TODAY)
        self.assertEqual("unanswerable", got.state)
        self.assertIn("15", got.detail, "落后天数没如实说出")
        self.assertIn("14", got.detail, "出单上限没如实说出")

    def test_an_integrity_refusal_is_stated_with_its_reason(self) -> None:
        got = todays_buy_answer(
            self._signal("rebalance"),
            self._fresh(integrity_accepted=False,
                        integrity_reason="built_from_holey_fetch"),
            self._TODAY)
        self.assertEqual("unanswerable", got.state)
        self.assertIn("built_from_holey_fetch", got.detail)

    def test_an_unevaluated_integrity_gate_is_not_treated_as_open(self) -> None:
        got = todays_buy_answer(
            self._signal("rebalance"),
            self._fresh(integrity_accepted=None), self._TODAY)
        self.assertEqual("unanswerable", got.state)
        self.assertIn("完整性", got.detail)

    def test_an_unreachable_freshness_verdict_is_stated(self) -> None:
        got = todays_buy_answer(
            self._signal("rebalance"),
            self._fresh(known=False, refuses_today=None,
                        integrity_accepted=None, message="日历尾不可读"),
            self._TODAY)
        self.assertEqual("unanswerable", got.state)
        self.assertIn("日历尾不可读", got.detail, "裁决不可达的原因没活下来")

    def test_a_missing_artifact_is_a_flow_state_not_an_error(self) -> None:
        got = todays_buy_answer(
            DailySignalSummary("missing", "尚无日度信号工件。"),
            self._fresh(), self._TODAY)
        self.assertEqual("no_instruction", got.state)
        self.assertIn("运行中心", got.detail)

    def test_an_unverified_artifact_refuses_to_answer(self) -> None:
        got = todays_buy_answer(
            self._signal("needs_verification",
                         detail="工件来源无法与现任模型确认。"),
            self._fresh(), self._TODAY)
        self.assertEqual("unanswerable", got.state)
        self.assertIn("工件来源无法与现任模型确认", got.detail,
                      "核查原因没活下来")

    def test_a_stale_instruction_names_its_entry_date(self) -> None:
        got = todays_buy_answer(
            self._signal("rebalance", entry_date="2026-08-24"),
            self._fresh(), self._TODAY)
        self.assertEqual("no_instruction", got.state)
        self.assertIn("2026-08-24", got.detail, "旧指令的执行日没点名")
        self.assertIn("尚未生成", got.detail)

    def test_a_future_instruction_is_not_todays(self) -> None:
        got = todays_buy_answer(
            self._signal("hold", entry_date="2026-08-27"),
            self._fresh(), self._TODAY)
        self.assertEqual("no_instruction", got.state)
        self.assertIn("2026-08-27", got.detail)
        self.assertIn("未来", got.detail)

    def test_an_unmarked_daily_artifact_cannot_be_synthesized(self) -> None:
        got = todays_buy_answer(
            self._signal("daily"), self._fresh(), self._TODAY)
        self.assertEqual("unanswerable", got.state)
        self.assertIn("节奏标记", got.detail)

    def test_every_state_carries_the_not_an_order_disclaimer(self) -> None:
        answers = [
            todays_buy_answer(self._signal("rebalance"), self._fresh(),
                              self._TODAY),
            todays_buy_answer(self._signal("hold"), self._fresh(),
                              self._TODAY),
            todays_buy_answer(DailySignalSummary("missing", "尚无。"),
                              self._fresh(), self._TODAY),
            todays_buy_answer(self._signal("rebalance"),
                              self._fresh(refuses_today=True), self._TODAY),
        ]
        self.assertEqual(
            {"buy", "watch", "no_instruction", "unanswerable"},
            {a.state for a in answers}, "四态没有各出一个代表")
        for answer in answers:
            with self.subTest(state=answer.state):
                self.assertIn("不是订单", answer.detail,
                              "合成句丢了「不是订单」的免责声明")

    def test_the_page_wires_the_synthesis_with_the_shared_verdicts(self) -> None:
        # 接线钉：合成句必须消费页面里那份出单侧裁决与 CN 日历日，
        # 且四个状态都有显式配色（漏一个就是 KeyError 或静默缺省）。
        source = (Path(__file__).resolve().parents[2] / "web" / "operator_ui"
                  / "pages" / "today_workbench.py").read_text(encoding="utf-8")
        self.assertIn("todays_buy_answer(signal, _freshness, cn_today())",
                      source, "合成句没有接共享裁决")
        for state in ("buy", "watch", "no_instruction", "unanswerable"):
            self.assertIn(f'"{state}":', source,
                          f"页面没有为 {state} 配色")
