"""Keep bootstrap behavior evidence distinct from unseen performance evidence.

Reads only committed presets, gate JSON and the operator card: no bundle,
training, model loading or new OOS/return calculation path.
"""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_EVIDENCE = _ROOT / "docs/research/evidence/csi800_n5_runs/bootstrap_v2_gates"


class BootstrapValidationDisclosureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.preset = yaml.safe_load(
            (_ROOT / "config/presets/csi800_n5_bootstrap_m3.yaml").read_text(
                encoding="utf-8"))
        self.member = json.loads(
            (_EVIDENCE / "m3_member_gate.json").read_text(encoding="utf-8"))
        self.ensemble = json.loads(
            (_EVIDENCE / "ensemble_gate.json").read_text(encoding="utf-8"))
        runbook = (_ROOT / "docs/csi800-n5-production-runbook.md").read_text(
            encoding="utf-8")
        self.step = runbook.split("4. **ensemble 级门 ×1**", 1)[1].split(
            "5. **切换执行**", 1)[0]

    def test_operator_card_discloses_the_evidence_derived_validation_overlap(self) -> None:
        member_window = self.member["window"]
        window = self.ensemble["window"]
        for key in ("valid_start", "valid_end"):
            self.assertEqual(self.preset[key], member_window[key])
        self.assertEqual(
            self.member["subject"]["pkl_sha256"],
            self.ensemble["subject"]["members"][-1]["pkl_sha256"])
        overlap_start = max(date.fromisoformat(window["window_start"]),
                            date.fromisoformat(member_window["valid_start"]))
        overlap_end = min(date.fromisoformat(window["window_end"]),
                          date.fromisoformat(member_window["valid_end"]))
        self.assertLessEqual(overlap_start, overlap_end)
        self.assertIn(f"--window-start {window['window_start']} "
                      f"--window-end {window['window_end']}", self.step)
        self.assertIn(f"`{member_window['valid_start']}.."
                      f"{member_window['valid_end']}`", self.step)
        self.assertIn(f"`{overlap_start}..{overlap_end}`", self.step)
        self.assertIn("验证重叠", self.step)
        self.assertNotIn("对三名成员全样本外", self.step)

    def test_operator_card_limits_the_gate_to_behavior_not_unseen_performance(self) -> None:
        for phrase in ("训练窗之后", "早停", "模型选择",
                       "不是独立未见数据的样本外业绩验证", "已认证战役证据",
                       "年度再认证", "原始 PASS 工件不变"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.step)
        for target in ("../config/presets/csi800_n5_bootstrap_m3.yaml",
                       "research/evidence/csi800_n5_runs/bootstrap_v2_gates/m3_member_gate.json",
                       "research/evidence/csi800_n5_runs/bootstrap_v2_gates/ensemble_gate.json"):
            with self.subTest(link=target):
                self.assertIn(f"]({target})", self.step)
                self.assertTrue((_ROOT / "docs" / target).is_file())

    def test_later_member_diagnostic_is_not_relabelled_as_ensemble_certification(self) -> None:
        self.assertIn(f"`{self.preset['test_start']}..{self.preset['test_end']}`", self.step)
        for phrase in ("内嵌日频诊断", "不能自动当作整个 ensemble 的独立样本外认证",
                       "不能仅截取 valid_end 之后的日期就声称完全未见"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.step)


if __name__ == "__main__":
    unittest.main()
