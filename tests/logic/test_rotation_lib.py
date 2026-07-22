"""Rotation-executor decision logic (PR-B', R1 maintenance path).

State coverage (codex #389 r2/r3/r4/r5/r11):

  certification  — WIN valid / WIN expired (15 months) / LOSE frozen /
                   malformed status refused / naive timestamps refused /
                   sidecar-path touches don't matter (the git command
                   pins the STATUS path — asserted on the argv
                   builders) / new WIN restores.
  gate artifacts — missing (caller side) / overall FAIL refused /
                   PLAUSIBLE-shaped overall refused / wrong scope
                   refused / subject binding mismatch refused /
                   PASS + bound admits.
  rotation plan  — oldest dropped + new appended / wrong member count
                   refused / incomplete new member refused.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import json  # noqa: E402

from scripts.retrain_gate_lib import (  # noqa: E402
    GATE_SCHEMA_VERSION,
    SCOPE_ENSEMBLE,
    SCOPE_MEMBER,
)
from scripts.rotation_lib import (  # noqa: E402
    RECERT_STATUS_PATH,
    RECERT_STATUS_SCHEMA_VERSION,
    VALIDITY_MONTHS,
    RotationRefusal,
    check_gate_artifact,
    git_show_status_cmd,
    git_status_tip_cmd,
    parse_recert_status,
    plan_rotated_members,
    recert_validity,
)


def _win_status() -> dict:
    return {
        "schema_version": RECERT_STATUS_SCHEMA_VERSION,
        "verdict": "WIN",
        "verdict_sidecar_path": "docs/research/csi800_cadence_verdict.json",
        "verdict_sidecar_sha256": "6a" * 32,
        "evidence_anchor_commit": "3f" * 20,
        "note": "initial bootstrap WIN (campaign verdict #383)",
    }


class StatusArtifactParsing(unittest.TestCase):
    def test_win_status_parses(self) -> None:
        status = parse_recert_status(json.dumps(_win_status()))
        self.assertEqual("WIN", status["verdict"])

    def test_lose_status_parses_without_sidecar_fields(self) -> None:
        payload = _win_status()
        payload["verdict"] = "LOSE"
        del payload["verdict_sidecar_path"]
        del payload["verdict_sidecar_sha256"]
        status = parse_recert_status(json.dumps(payload))
        self.assertEqual("LOSE", status["verdict"])

    def test_malformed_statuses_refused(self) -> None:
        cases: list[dict] = []
        for mutate in (
            lambda p: p.update(schema_version="v0"),
            lambda p: p.update(verdict="MAYBE"),
            lambda p: p.update(evidence_anchor_commit="not-a-commit"),
            lambda p: p.update(note="   "),
            lambda p: p.pop("verdict"),
            lambda p: p.pop("note"),
            # WIN without the sidecar content-hash reference:
            lambda p: p.pop("verdict_sidecar_sha256"),
            lambda p: p.update(verdict_sidecar_sha256="zz" * 32),
        ):
            payload = _win_status()
            mutate(payload)
            cases.append(payload)
        for payload in cases:
            with self.assertRaises(RotationRefusal, msg=payload):
                parse_recert_status(json.dumps(payload))
        for raw in ("not json {", json.dumps(["a", "list"])):
            with self.assertRaises(RotationRefusal, msg=raw):
                parse_recert_status(raw)


class CertificationValidity(unittest.TestCase):
    _TIP = "2026-07-01T10:00:00+08:00"

    def test_win_within_window_allows(self) -> None:
        ok, reason = recert_validity(
            _win_status(), self._TIP, "2027-09-30T00:00:00+00:00")
        self.assertTrue(ok, reason)

    def test_win_expired_freezes(self) -> None:
        # tip 2026-07-01 + 15 months = 2027-10-01; the day after is out.
        ok, reason = recert_validity(
            _win_status(), self._TIP, "2027-10-02T00:00:00+00:00")
        self.assertFalse(ok)
        self.assertIn("expired", reason)

    def test_lose_freezes_regardless_of_dates(self) -> None:
        payload = _win_status()
        payload["verdict"] = "LOSE"
        ok, reason = recert_validity(
            payload, self._TIP, "2026-07-02T00:00:00+00:00")
        self.assertFalse(ok)
        self.assertIn("LOSE", reason)

    def test_new_win_restores_after_lose(self) -> None:
        # The freeze is a property of the CURRENT status content — a
        # merged new WIN (fresh tip) immediately allows again.
        ok, _ = recert_validity(
            _win_status(), "2027-01-05T09:00:00+08:00",
            "2027-02-01T00:00:00+00:00")
        self.assertTrue(ok)

    def test_naive_or_garbage_timestamps_freeze(self) -> None:
        for tip, now in (
            ("2026-07-01T10:00:00", "2026-08-01T00:00:00+00:00"),
            ("2026-07-01T10:00:00+08:00", "2026-08-01T00:00:00"),
            ("yesterday", "2026-08-01T00:00:00+00:00"),
        ):
            ok, _ = recert_validity(_win_status(), tip, now)
            self.assertFalse(ok, (tip, now))

    def test_validity_months_pin(self) -> None:
        # 12-month recert cycle + 3-month execution grace (codex #389 r2).
        self.assertEqual(15, VALIDITY_MONTHS)


class GitCommandPins(unittest.TestCase):
    def test_status_read_is_mainline_content_only(self) -> None:
        self.assertEqual(
            ["git", "show", f"origin/main:{RECERT_STATUS_PATH}"],
            git_show_status_cmd())

    def test_validity_anchor_is_status_path_tip(self) -> None:
        # codex #389 r5: the anchor follows the STATUS artifact path —
        # a non-recert touch of the verdict SIDECAR path can never
        # drift the validity window because the sidecar path simply
        # does not appear in the command.
        cmd = git_status_tip_cmd()
        self.assertEqual(
            ["git", "log", "-1", "--format=%cI", "origin/main", "--",
             RECERT_STATUS_PATH],
            cmd)
        self.assertNotIn("docs/research/csi800_cadence_verdict.json",
                         cmd)

    def test_status_path_pin(self) -> None:
        self.assertEqual("docs/promotion/csi800_recert_status.json",
                         RECERT_STATUS_PATH)


def _member_gate_artifact(overall: str = "PASS") -> dict:
    return {
        "schema_version": GATE_SCHEMA_VERSION,
        "scope": SCOPE_MEMBER,
        "subject": {"pkl_sha256": "ab" * 32},
        "overall": overall,
    }


class GateArtifactConsumption(unittest.TestCase):
    def test_pass_and_bound_admits(self) -> None:
        check_gate_artifact(
            _member_gate_artifact(), scope=SCOPE_MEMBER,
            expected_subject_sha="ab" * 32)

    def test_fail_artifact_refused(self) -> None:
        # codex #389 r11: the gate said FAIL — the executor must be a
        # closed channel, not a second opinion.
        with self.assertRaises(RotationRefusal) as ctx:
            check_gate_artifact(
                _member_gate_artifact("FAIL"), scope=SCOPE_MEMBER,
                expected_subject_sha="ab" * 32)
        self.assertIn("FAIL", str(ctx.exception))

    def test_non_pass_shapes_refused(self) -> None:
        for overall in (None, "pass", "OK", 1, True):
            artifact = _member_gate_artifact()
            artifact["overall"] = overall
            with self.assertRaises(RotationRefusal, msg=overall):
                check_gate_artifact(
                    artifact, scope=SCOPE_MEMBER,
                    expected_subject_sha="ab" * 32)

    def test_wrong_scope_refused(self) -> None:
        with self.assertRaises(RotationRefusal):
            check_gate_artifact(
                _member_gate_artifact(), scope=SCOPE_ENSEMBLE,
                expected_subject_sha="ab" * 32)

    def test_subject_binding_mismatch_refused(self) -> None:
        with self.assertRaises(RotationRefusal) as ctx:
            check_gate_artifact(
                _member_gate_artifact(), scope=SCOPE_MEMBER,
                expected_subject_sha="cd" * 32)
        self.assertIn("bind", str(ctx.exception))

    def test_schema_drift_refused(self) -> None:
        artifact = _member_gate_artifact()
        artifact["schema_version"] = "v0"
        with self.assertRaises(RotationRefusal):
            check_gate_artifact(
                artifact, scope=SCOPE_MEMBER,
                expected_subject_sha="ab" * 32)

    def test_ensemble_scope_binds_manifest_sha(self) -> None:
        artifact = {
            "schema_version": GATE_SCHEMA_VERSION,
            "scope": SCOPE_ENSEMBLE,
            "subject": {"manifest_sha256": "cd" * 32},
            "overall": "PASS",
        }
        check_gate_artifact(artifact, scope=SCOPE_ENSEMBLE,
                            expected_subject_sha="cd" * 32)
        with self.assertRaises(RotationRefusal):
            check_gate_artifact(artifact, scope=SCOPE_ENSEMBLE,
                                expected_subject_sha="ee" * 32)


class RotationPlan(unittest.TestCase):
    def _members(self) -> list[dict]:
        return [
            {"pkl_path": f"m{i}.pkl", "pkl_sha256": f"{i}" * 64,
             "meta_path": f"m{i}.pkl.meta.json",
             "meta_sha256": f"{i}a" * 32,
             "fit_start": f"202{i}-01-01", "fit_end": f"202{i}-12-31"}
            for i in range(3)
        ]

    def test_oldest_dropped_new_appended(self) -> None:
        members = self._members()
        new = dict(members[0], pkl_path="new.pkl", pkl_sha256="f" * 64,
                   meta_path="new.pkl.meta.json",
                   meta_sha256="fa" * 32,
                   fit_start="2024-01-01", fit_end="2025-12-31")
        planned = plan_rotated_members(members, new)
        self.assertEqual(3, len(planned))
        self.assertEqual(members[1], planned[0])
        self.assertEqual(members[2], planned[1])
        self.assertEqual(new, planned[2])

    def test_wrong_member_count_refused(self) -> None:
        with self.assertRaises(RotationRefusal):
            plan_rotated_members(self._members()[:2],
                                 self._members()[0])

    def test_incomplete_new_member_refused(self) -> None:
        new = self._members()[0]
        del new["meta_sha256"]
        with self.assertRaises(RotationRefusal):
            plan_rotated_members(self._members(), new)


if __name__ == "__main__":
    unittest.main()
