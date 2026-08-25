"""Governance: CSI800 N5 bootstrap cutover (PR-C' of
2026-07-20-csi800-n5-production-promotion).

Pins the PRE-REGISTERED bootstrap arithmetic — these windows were
fixed BEFORE ignition (R1-DP-C / tasks §PR-C': 跑前钉死), so a later
edit is a governed change, not a tuning knob:

  * three staggered members, training terminals one quarter apart,
    24-month rolling train windows, ~3-month valid windows;
  * the same numbers the serving manifest will be validated against
    (PR-A' pins: fit_end gaps in [75,100], train span in [700,745]);
  * the csi800 mandatory guard trio + GPU device on every member;
  * the bootstrap path's deliberate divergence from the maintenance
    path: member gate windows are NOT recency-bound (the members are
    staggered into the past by protocol), while the ensemble gate's
    trailing quarter still is.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_PRESETS = _PROJECT_ROOT / "config" / "presets"

# The pre-registered windows (trading-calendar exact, bundle tail
# 2026-08-03; re-anchored per RA-DP-1 of
# 2026-08-04-csi800-n5-bootstrap-reanchor after the v1 trio's m2
# gate refusal). train | valid | test.
_BOOTSTRAP_WINDOWS = {
    "m1": (("2023-09-28", "2025-09-29"), ("2025-10-10", "2026-01-09"),
           ("2026-01-14", "2026-02-27")),
    "m2": (("2023-12-29", "2025-12-30"), ("2026-01-06", "2026-04-03"),
           ("2026-04-09", "2026-05-21")),
    "m3": (("2024-04-01", "2026-04-01"), ("2026-04-07", "2026-07-07"),
           ("2026-07-10", "2026-07-31")),
}


def _load(name: str) -> dict:
    path = _PRESETS / f"csi800_n5_bootstrap_{name}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class BootstrapPresetPins(unittest.TestCase):
    def test_windows_are_the_preregistered_ones(self) -> None:
        for name, (train, valid, test) in _BOOTSTRAP_WINDOWS.items():
            cfg = _load(name)
            with self.subTest(member=name):
                self.assertEqual(train[0], cfg["train_start"])
                self.assertEqual(train[1], cfg["train_end"])
                self.assertEqual(valid[0], cfg["valid_start"])
                self.assertEqual(valid[1], cfg["valid_end"])
                self.assertEqual(test[0], cfg["test_start"])
                self.assertEqual(test[1], cfg["test_end"])

    def test_member_arithmetic_satisfies_serving_pins(self) -> None:
        from src.inference.ensemble_serving import (
            MEMBER_SPACING_DAYS_MAX,
            MEMBER_SPACING_DAYS_MIN,
            TRAIN_WINDOW_DAYS_MAX,
            TRAIN_WINDOW_DAYS_MIN,
        )

        def d(s: str) -> date:
            return date.fromisoformat(s)

        ends = []
        for name, (train, _valid, _test) in _BOOTSTRAP_WINDOWS.items():
            span = (d(train[1]) - d(train[0])).days
            with self.subTest(member=name, check="train span"):
                self.assertGreaterEqual(span, TRAIN_WINDOW_DAYS_MIN)
                self.assertLessEqual(span, TRAIN_WINDOW_DAYS_MAX)
            ends.append(d(train[1]))
        for i in range(1, len(ends)):
            gap = (ends[i] - ends[i - 1]).days
            with self.subTest(pair=i, check="quarterly stagger"):
                self.assertGreaterEqual(gap, MEMBER_SPACING_DAYS_MIN)
                self.assertLessEqual(gap, MEMBER_SPACING_DAYS_MAX)
        self.assertEqual(sorted(ends), ends, "members must be oldest->newest")

    def test_valid_windows_satisfy_gate_window_pins(self) -> None:
        # The member IC gate binds its measured window to the member's
        # own training window (PR-B' r19): strictly out of sample,
        # promptly after it, quarter-to-half-year span.
        from scripts.rotation_lib import (
            GATE_WINDOW_SPAN_DAYS_MAX,
            GATE_WINDOW_SPAN_DAYS_MIN,
            MEMBER_VALID_GAP_DAYS_MAX,
        )

        def d(s: str) -> date:
            return date.fromisoformat(s)

        for name, (train, valid, _test) in _BOOTSTRAP_WINDOWS.items():
            gap = (d(valid[0]) - d(train[1])).days
            span = (d(valid[1]) - d(valid[0])).days
            with self.subTest(member=name):
                self.assertGreater(gap, 0, "valid must follow training")
                self.assertLessEqual(gap, MEMBER_VALID_GAP_DAYS_MAX)
                self.assertGreaterEqual(span, GATE_WINDOW_SPAN_DAYS_MIN)
                self.assertLessEqual(span, GATE_WINDOW_SPAN_DAYS_MAX)

    def test_guard_trio_and_device(self) -> None:
        for name in _BOOTSTRAP_WINDOWS:
            cfg = _load(name)
            with self.subTest(member=name):
                self.assertEqual("csi800", cfg["instruments"])
                self.assertEqual("SH000906TR", cfg["benchmark_code"])
                self.assertIs(True, cfg["attribution_sleeve_grouping"])
                self.assertIs(True, cfg["risk_constraints_enabled"])
                self.assertEqual("campaign_v1",
                                 cfg["risk_constraints_calibration"])
                self.assertEqual("gpu", cfg["compute_device"])

    def test_presets_differ_only_in_windows(self) -> None:
        # Same-family configuration (R1-DP-A): three members must be
        # one protocol, not three experiments.
        window_keys = {"train_start", "train_end", "valid_start",
                       "valid_end", "test_start", "test_end"}
        loaded = {n: _load(n) for n in _BOOTSTRAP_WINDOWS}
        base = loaded["m1"]
        for name, cfg in loaded.items():
            diff = {k for k in set(base) | set(cfg)
                    if base.get(k) != cfg.get(k)}
            with self.subTest(member=name):
                self.assertTrue(
                    diff <= window_keys,
                    f"{name} diverges outside the window keys: "
                    f"{sorted(diff - window_keys)}")


class BootstrapGateSemantics(unittest.TestCase):
    def test_member_scope_recency_is_opt_out_for_bootstrap(self) -> None:
        # The maintenance path binds gate recency; the bootstrap's
        # members are staggered into the past ON PURPOSE, so the
        # executor opts out for member scope ONLY.
        import inspect

        from scripts.rotation_lib import check_gate_window

        self.assertIn("enforce_recency",
                      inspect.signature(check_gate_window).parameters)
        src = (_PROJECT_ROOT / "scripts"
               / "bootstrap_ensemble_cutover.py").read_text(
            encoding="utf-8")
        self.assertIn("enforce_recency=False", src)
        # ...and the ensemble artifact must NOT opt out: the trailing
        # quarter has to describe the present.
        ensemble_call = src.split("scope=SCOPE_ENSEMBLE", 1)[1][:400]
        self.assertNotIn("enforce_recency", ensemble_call)

    def test_status_artifact_is_written_only_by_the_bootstrap(self) -> None:
        # R1-DP-D: PR-B' ships the reader; the FIRST write is here.
        rotation = (_PROJECT_ROOT / "scripts" / "rotate_ensemble_member.py"
                    ).read_text(encoding="utf-8")
        self.assertNotIn("write_text(json.dumps(status", rotation)
        cutover = (_PROJECT_ROOT / "scripts"
                   / "bootstrap_ensemble_cutover.py").read_text(
            encoding="utf-8")
        self.assertIn("build_initial_status", cutover)
        # Refuses to overwrite an existing state.
        self.assertIn("already exists", cutover)

    def test_status_file_absent_until_the_cutover_runs(self) -> None:
        from scripts.rotation_lib import RECERT_STATUS_PATH

        # PR-C' ships the executor; the artifact itself lands with the
        # cutover commit (writing it earlier starts the 15-month clock).
        path = _PROJECT_ROOT / RECERT_STATUS_PATH
        if path.exists():
            from scripts.rotation_lib import parse_recert_status

            parse_recert_status(path.read_text(encoding="utf-8"))




# 季度维护成员 m4（chore/csi800-n5-m4-preset，跑前钉死）。窗口按三成员
# 既定算术实算：train 终点 = Q2 末、距 m3 90 天 ∈ [75,100]、跨度 729 天
# ∈ [700,745]；valid = 训终后第 3 个交易日起 +3 个日历月回拉；test 为
# 内嵌诊断段（非晋升证据），点火须待 bundle 尾 ≥ 2026-11-02。
_M4_WINDOWS = (("2024-07-01", "2026-06-30"), ("2026-07-03", "2026-09-30"),
               ("2026-10-12", "2026-10-30"))
#: m4 两个段边界附近的 2026 交易日（合成日历，仅覆盖 embargo 判定所需的
#: 「严格介于」区间；出处 = 上交所 2025-12-22 休市安排：国庆 10-01..10-07
#: 休市、10-08 起照常开市，10-10(周六) 周末休市；6 月末-7 月初无假日）。
_M4_BOUNDARY_SESSIONS = (
    "2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03",
    "2026-07-06",
    "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-08", "2026-10-09",
    "2026-10-12", "2026-10-13",
)
#: 六个窗口键之外，m4 必须与 m3 **逐字同族**——preset 的头注承诺如此，
#: 治理在此作证（codex #466 P2：没有钉，之后一笔「顺手调参」静默失效
#: 预注册）。
_WINDOW_KEYS = ("train_start", "train_end", "valid_start", "valid_end",
                "test_start", "test_end")


class MaintenanceMemberM4Pins(unittest.TestCase):
    @staticmethod
    def _m4() -> dict:
        path = _PRESETS / "csi800_n5_m4.yaml"
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_windows_are_the_preregistered_ones(self) -> None:
        cfg = self._m4()
        (train, valid, test) = _M4_WINDOWS
        self.assertEqual(train, (cfg["train_start"], cfg["train_end"]))
        self.assertEqual(valid, (cfg["valid_start"], cfg["valid_end"]))
        self.assertEqual(test, (cfg["test_start"], cfg["test_end"]))

    def test_window_boundaries_survive_calendar_arithmetic(self) -> None:
        """字面钉之外的日历自检（codex #466 P2：只重复字面测不出算错）。

        交易日推算依赖官方休市安排（2026 国庆 = 10-01..10-07 休市、10-08
        开市，上交所 2025-12-22 通知）——那部分进不了 stdlib，出处钉在
        preset 头注；此处管 stdlib 能管的：六个边界都得是周内日（周末边界
        必错），且「点火须 bundle 尾 ≥ 2026-11-02」必须等于 test_end 之后
        第一个周内日（T+1 结算日主张与数字互证，防改一处漏一处）。
        """
        cfg = self._m4()
        for key in _WINDOW_KEYS:
            day = date.fromisoformat(cfg[key])
            self.assertLess(day.weekday(), 5, f"{key}={cfg[key]} 落在周末")
        settlement = date.fromisoformat(cfg["test_end"]) + timedelta(days=1)
        while settlement.weekday() >= 5:
            settlement += timedelta(days=1)
        self.assertEqual(date(2026, 11, 2), settlement,
                         "头注的点火下限与 test_end 的 T+1 算术对不上")

    def test_embargo_gaps_clear_the_canonical_validator(self) -> None:
        """embargo 直接驱动运行时同一校验器（codex P2：weekday≠交易日）。

        周内日自检管不住假日：两个边界改到只隔一个真实交易日的周内日，
        weekday 检查照绿而 FeatureDatasetBuilder 的 validate_segment_embargo
        点火即拒。所以这里拿官方休市安排合成的边界日历，喂**运行时那同一个
        校验器**（钉调用同一函数的既定纪律），train→valid 与 valid→test 两
        个边界都要过；再用负对照证明校验器在咬——空/错日历的绿不算数。
        """
        import dataclasses

        from src.core._yaml_loader import load_yaml_with_inheritance
        from src.core.pipeline import PipelineConfig
        from src.data._segment_embargo import (
            label_lookahead_days,
            validate_segment_embargo,
        )
        cfg = self._m4()
        # lookahead 不抄缺省值：生产 builder 传 label_lookahead_days(
        # config.label_horizon_days)——config.yaml 日后改 horizon>1 时，
        # 拿缺省 2 的治理会绿着而点火即拒（codex P2）。horizon 从**运行时
        # 同一装载器**解析的合并配置取（extends 链生效值；未显式配置时按
        # PipelineConfig 字段缺省，与运行时构造一致），再走同一推导函数。
        merged = load_yaml_with_inheritance(_PRESETS / "csi800_n5_m4.yaml")
        default_horizon = next(
            f.default for f in dataclasses.fields(PipelineConfig)
            if f.name == "label_horizon_days")
        lookahead = label_lookahead_days(
            merged.get("label_horizon_days", default_horizon))
        calendar = [date.fromisoformat(d) for d in _M4_BOUNDARY_SESSIONS]
        errors = validate_segment_embargo(
            train_end=date.fromisoformat(cfg["train_end"]),
            valid_start=date.fromisoformat(cfg["valid_start"]),
            valid_end=date.fromisoformat(cfg["valid_end"]),
            test_start=date.fromisoformat(cfg["test_start"]),
            calendar=calendar,
            lookahead_days=lookahead,
        )
        self.assertEqual([], errors, "m4 边界过不了运行时 embargo 校验器")
        # 负对照：test_start 提前到假期后次日（10-09），严格介于 09-30 与
        # 它之间只剩 10-08 一个交易日——同一校验器必须报错，否则上面的绿
        # 只是校验器没在咬。
        bitten = validate_segment_embargo(
            train_end=date.fromisoformat(cfg["train_end"]),
            valid_start=date.fromisoformat(cfg["valid_start"]),
            valid_end=date.fromisoformat(cfg["valid_end"]),
            test_start=date(2026, 10, 9),
            calendar=calendar,
            lookahead_days=lookahead,
        )
        self.assertTrue(bitten, "校验器没咬负对照——本用例的绿没有意义")

    def test_the_header_discloses_truncated_diagnostics_at_floor(self) -> None:
        # 下限点火时长视界诊断为空是**预告过的已知代价**（codex #466 P2），
        # 不是异常——预告必须钉在操作人真正会读的 preset 头注里，且点名
        # 21 个交易日与「非晋升证据」两个关键事实，防止后续编辑把披露删了
        # 而点火下限还在（操作人读到 NaN 会当故障排查）。
        text = (_PRESETS / "csi800_n5_m4.yaml").read_text(encoding="utf-8")
        self.assertIn("21 个交易日", text, "缺 analyzer 视界事实")
        self.assertIn("非晋升证据", text, "缺「诊断不进门」的定性")
        self.assertIn("2026-11-02", text, "点火下限被改动或删除")

    def test_serving_pins_arithmetic(self) -> None:
        # 界值从 serving 契约本尊导入（codex P2：抄 75/100/700/745 字面会
        # 在契约收紧时治理绿着而 load_ensemble_manifest 拒载——竞争快照）。
        from src.inference.ensemble_serving import (
            MEMBER_SPACING_DAYS_MAX,
            MEMBER_SPACING_DAYS_MIN,
            TRAIN_WINDOW_DAYS_MAX,
            TRAIN_WINDOW_DAYS_MIN,
        )
        cfg = self._m4()
        m3 = _load("m3")
        gap = (date.fromisoformat(cfg["train_end"])
               - date.fromisoformat(m3["train_end"])).days
        span = (date.fromisoformat(cfg["train_end"])
                - date.fromisoformat(cfg["train_start"])).days
        self.assertGreaterEqual(gap, MEMBER_SPACING_DAYS_MIN,
                                f"与 m3 的 fit_end 间距 {gap} 出 pin")
        self.assertLessEqual(gap, MEMBER_SPACING_DAYS_MAX,
                             f"与 m3 的 fit_end 间距 {gap} 出 pin")
        self.assertGreaterEqual(span, TRAIN_WINDOW_DAYS_MIN,
                                f"训窗跨度 {span} 出 pin")
        self.assertLessEqual(span, TRAIN_WINDOW_DAYS_MAX,
                             f"训窗跨度 {span} 出 pin")

    def test_family_parity_outside_the_window_keys(self) -> None:
        cfg = self._m4()
        m3 = _load("m3")
        stripped_m4 = {k: v for k, v in cfg.items() if k not in _WINDOW_KEYS}
        stripped_m3 = {k: v for k, v in m3.items() if k not in _WINDOW_KEYS}
        self.assertEqual(stripped_m3, stripped_m4,
                         "m4 在窗口键之外与 m3 不同族——预注册被静默改动")

    def test_the_guard_trio_and_device_are_pinned_directly(self) -> None:
        # parity 之外再直接钉一层：m3 若也被改，parity 会双双漂移而绿着。
        cfg = self._m4()
        # 身份比对钉字面布尔（codex P2）：assertTrue 吃 truthy——m3/m4 同
        # 漂成 YAML 字符串 "false" 时 parity 与 truthy 双双照绿，而
        # PipelineConfig 不管这两个字段的运行时类型。
        self.assertIs(True, cfg["attribution_sleeve_grouping"])
        self.assertIs(True, cfg["risk_constraints_enabled"])
        self.assertEqual("campaign_v1", cfg["risk_constraints_calibration"])
        self.assertEqual("gpu", cfg["compute_device"])
        self.assertEqual("csi800", cfg["instruments"])
        self.assertEqual("SH000906TR", cfg["benchmark_code"])


if __name__ == "__main__":
    unittest.main()
