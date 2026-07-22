"""Governance: per-retrain gates + rotation executor (PR-B' of
2026-07-20-csi800-n5-production-promotion).

Pins:
  * the gate libs are PURE (no qlib/pandas on the governance import
    path — the eval_profiles precedent);
  * veto thresholds equal the campaign attach constants verbatim
    (R1-DP-B: "数字原样" — a drifted threshold silently changes what
    counts as a legal production member);
  * schema versions + the single-status-artifact path;
  * the status artifact is NOT written by PR-B' (its first write is
    the PR-C' cutover — writing earlier would start the 15-month
    clock before production switched); if present it must parse;
  * the runbook carries the spec-mandated expectation anchors
    (20 bps conservative cost basis, ~73 bps breakeven, observation
    discipline, annual re-certification obligation).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


class GateLibPurity(unittest.TestCase):
    def test_gate_and_rotation_libs_import_no_heavy_runtime(self) -> None:
        # Source-level scan (the D5-gate style): the PURE libs must
        # never grow a qlib/pandas import — governance and the
        # rotation executor's fast tests sit on this import path.
        for rel in ("scripts/retrain_gate_lib.py",
                    "scripts/rotation_lib.py"):
            src = (_PROJECT_ROOT / rel).read_text(encoding="utf-8")
            for forbidden in ("import qlib", "from qlib",
                              "import pandas", "from pandas",
                              "import numpy", "from numpy"):
                self.assertNotIn(
                    forbidden, src,
                    f"{rel} must stay pure stdlib; found {forbidden!r}")


class VetoThresholdCrossPin(unittest.TestCase):
    def test_thresholds_equal_campaign_attach_verbatim(self) -> None:
        from scripts import retrain_gate_lib as lib
        from scripts.research import csi800_campaign_attach_vetoes as attach

        self.assertEqual(attach.CSI500_DEPENDENCE_THRESHOLD,
                         lib.CSI500_DEPENDENCE_THRESHOLD)
        self.assertEqual(attach.TURNOVER_RATIO_THRESHOLD,
                         lib.TURNOVER_RATIO_THRESHOLD)
        self.assertEqual(attach.CSI500_WEIGHT_THRESHOLD,
                         lib.CSI500_WEIGHT_THRESHOLD)
        self.assertEqual(attach.UNKNOWN_WEIGHT_THRESHOLD,
                         lib.UNKNOWN_WEIGHT_THRESHOLD)


class SchemaPins(unittest.TestCase):
    def test_gate_artifact_schema_version(self) -> None:
        from scripts.retrain_gate_lib import GATE_SCHEMA_VERSION

        self.assertEqual("csi800_n5_retrain_gate_v1",
                         GATE_SCHEMA_VERSION)

    def test_recert_status_schema_and_path(self) -> None:
        from scripts.rotation_lib import (
            RECERT_STATUS_PATH,
            RECERT_STATUS_SCHEMA_VERSION,
            VALIDITY_MONTHS,
        )

        self.assertEqual("csi800_recert_status_v1",
                         RECERT_STATUS_SCHEMA_VERSION)
        self.assertEqual("docs/promotion/csi800_recert_status.json",
                         RECERT_STATUS_PATH)
        self.assertEqual(15, VALIDITY_MONTHS)

    def test_gate_scopes_cover_all_five_gates(self) -> None:
        # R1-DP-B: five gates, split member/ensemble (codex #389 r13).
        from scripts.retrain_gate_lib import _SCOPE_GATES

        self.assertEqual(
            {"member": ("trainer_integrity", "ic_direction"),
             "ensemble": ("degeneracy", "constraint_dry_run",
                          "serving_veto")},
            dict(_SCOPE_GATES))


class StatusArtifactDiscipline(unittest.TestCase):
    def test_status_artifact_not_written_early_or_parses(self) -> None:
        # PR-B' ships schema + executor but NOT the state (first write
        # = PR-C' cutover). If a later change wrote it, it must parse
        # against the schema this executor consumes.
        from scripts.rotation_lib import (
            RECERT_STATUS_PATH,
            parse_recert_status,
        )

        path = _PROJECT_ROOT / RECERT_STATUS_PATH
        if not path.exists():
            return  # the PR-B' state: schema shipped, file absent
        parse_recert_status(path.read_text(encoding="utf-8"))


class RunbookAnchors(unittest.TestCase):
    def test_runbook_carries_mandated_expectation_anchors(self) -> None:
        # Spec (两级绑定链 requirement): the 20 bps conservative cost
        # basis + 73 bps breakeven reference SHALL live in the ops
        # runbook, alongside the observation discipline and the annual
        # re-certification obligation.
        runbook = (_PROJECT_ROOT
                   / "docs" / "csi800-n5-production-runbook.md")
        self.assertTrue(runbook.is_file(),
                        "csi800 N5 production runbook missing")
        text = runbook.read_text(encoding="utf-8")
        for anchor in ("20 bps", "73", "观察期", "年度再认证",
                       "csi800_recert_status.json",
                       "pre_rotation", "retrain_gate"):
            self.assertIn(anchor, text,
                          f"runbook missing mandated anchor {anchor!r}")


if __name__ == "__main__":
    unittest.main()
