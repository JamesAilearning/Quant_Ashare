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
    git_resolve_mainline_cmd,
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


_REV = "a" * 40


class GitCommandPins(unittest.TestCase):
    def test_mainline_resolved_to_one_commit(self) -> None:
        # codex #391 r25: origin/main is a MOVING ref — it is resolved
        # ONCE and every subsequent read is pinned to that commit id.
        self.assertEqual(
            ["git", "rev-parse", "origin/main^{commit}"],
            git_resolve_mainline_cmd())

    def test_status_read_is_pinned_content_only(self) -> None:
        cmd = git_show_status_cmd(_REV)
        self.assertEqual(
            ["git", "show", f"{_REV}:{RECERT_STATUS_PATH}"], cmd)
        # The moving ref must not appear in the pinned read.
        self.assertNotIn("origin/main", " ".join(cmd))

    def test_validity_anchor_is_status_path_tip(self) -> None:
        # codex #389 r5: the anchor follows the STATUS artifact path —
        # a non-recert touch of the verdict SIDECAR path can never
        # drift the validity window because the sidecar path simply
        # does not appear in the command. codex #391 r25: and it reads
        # the SAME pinned revision as the content.
        cmd = git_status_tip_cmd(_REV)
        self.assertEqual(
            ["git", "log", "-1", "--format=%cI", _REV, "--",
             RECERT_STATUS_PATH],
            cmd)
        self.assertNotIn("docs/research/csi800_cadence_verdict.json",
                         cmd)
        self.assertNotIn("origin/main", " ".join(cmd))

    def test_status_path_pin(self) -> None:
        self.assertEqual("docs/promotion/csi800_recert_status.json",
                         RECERT_STATUS_PATH)


def _member_gate_artifact(overall: str = "PASS") -> dict:
    return {
        "schema_version": GATE_SCHEMA_VERSION,
        "profile": "csi800_n5",
        "scope": SCOPE_MEMBER,
        "subject": {"pkl_sha256": "ab" * 32, "meta_sha256": "ac" * 32,
                    "fit_start": "2023-09-20", "fit_end": "2025-09-18"},
        "window": {"valid_start": "2025-09-25",
                   "valid_end": "2025-12-20"},
        "gates": {"trainer_integrity": {"verdict": "PASS"},
                  "ic_direction": {"verdict": "PASS"}},
        "overall": overall,
    }


# The rotation instant these window fixtures are dated against.
_NOW_ISO = "2025-12-22T00:00:00+00:00"


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

    def test_lying_overall_refused(self) -> None:
        # Adversarial self-review: the verdict is re-derived from the
        # per-gate blocks — a hand-edited artifact whose overall says
        # PASS over a failing or absent gate is refused.
        artifact = _member_gate_artifact()
        artifact["gates"]["ic_direction"]["verdict"] = "FAIL"
        with self.assertRaises(RotationRefusal) as ctx:
            check_gate_artifact(
                artifact, scope=SCOPE_MEMBER,
                expected_subject_sha="ab" * 32)
        self.assertIn("disagrees", str(ctx.exception))
        artifact = _member_gate_artifact()
        del artifact["gates"]["trainer_integrity"]
        with self.assertRaises(RotationRefusal):
            check_gate_artifact(
                artifact, scope=SCOPE_MEMBER,
                expected_subject_sha="ab" * 32)
        artifact = _member_gate_artifact()
        del artifact["gates"]
        with self.assertRaises(RotationRefusal):
            check_gate_artifact(
                artifact, scope=SCOPE_MEMBER,
                expected_subject_sha="ab" * 32)

    def test_extra_gate_block_refused(self) -> None:
        # codex #391 r7: an EXTRA gate block — even a failing one —
        # would be ignored by an expected-names-only loop; the gate set
        # must match exactly (extra PASS blocks refuse too: this
        # executor does not adjudicate gates it does not know).
        for verdict in ("FAIL", "PASS"):
            artifact = _member_gate_artifact()
            artifact["gates"]["surprise_gate"] = {"verdict": verdict}
            with self.assertRaises(RotationRefusal, msg=verdict) as ctx:
                check_gate_artifact(
                    artifact, scope=SCOPE_MEMBER,
                    expected_subject_sha="ab" * 32)
            self.assertIn("gate set", str(ctx.exception))

    def test_wrong_profile_refused(self) -> None:
        # codex #391 r12: a gate measured under different semantics
        # (e.g. csi300_daily) must never authorize this rotation.
        for profile in ("csi300_daily", None):
            artifact = _member_gate_artifact()
            if profile is None:
                del artifact["profile"]
            else:
                artifact["profile"] = profile
            with self.assertRaises(RotationRefusal, msg=profile) as ctx:
                check_gate_artifact(
                    artifact, scope=SCOPE_MEMBER,
                    expected_subject_sha="ab" * 32)
            self.assertIn("profile", str(ctx.exception))

    def test_fit_window_binding_mismatch_refused(self) -> None:
        # codex #391 r12: serving derives the inference normalization
        # window from the newest member's manifest dates — the gate
        # must have evaluated the SAME window.
        check_gate_artifact(
            _member_gate_artifact(), scope=SCOPE_MEMBER,
            expected_subject_sha="ab" * 32,
            expected_fit_window=("2023-09-20", "2025-09-18"))
        with self.assertRaises(RotationRefusal) as ctx:
            check_gate_artifact(
                _member_gate_artifact(), scope=SCOPE_MEMBER,
                expected_subject_sha="ab" * 32,
                expected_fit_window=("2023-09-21", "2025-09-18"))
        self.assertIn("window", str(ctx.exception))

    def test_measured_window_binding(self) -> None:
        # codex #391 r19: WHEN the gates were measured is bound too —
        # digests alone would let a 1900/stale/easier period through.
        kwargs = {"scope": SCOPE_MEMBER,
                  "expected_subject_sha": "ab" * 32,
                  "member_fit_end": "2025-09-18",
                  "now_iso": _NOW_ISO}
        check_gate_artifact(_member_gate_artifact(), **kwargs)
        cases = {
            "1900": {"valid_start": "1900-01-01",
                     "valid_end": "1900-03-31"},
            "in-sample": {"valid_start": "2025-06-01",
                          "valid_end": "2025-09-10"},
            "late start": {"valid_start": "2025-11-20",
                           "valid_end": "2026-02-20"},
            "too short": {"valid_start": "2025-09-25",
                          "valid_end": "2025-10-10"},
            # Ends after the rotation instant — the measured period
            # has not finished happening (codex #391 r34: no grace).
            "future": {"valid_start": "2025-09-25",
                       "valid_end": "2026-03-20"},
            "one day future": {"valid_start": "2025-09-25",
                               "valid_end": "2025-12-23"},
            "garbage": {"valid_start": "not-a-date",
                        "valid_end": "2025-12-20"},
        }
        for label, window in cases.items():
            artifact = _member_gate_artifact()
            artifact["window"] = window
            with self.assertRaises(RotationRefusal, msg=label):
                check_gate_artifact(artifact, **kwargs)
        missing = _member_gate_artifact()
        del missing["window"]
        with self.assertRaises(RotationRefusal):
            check_gate_artifact(missing, **kwargs)

    def test_ensemble_window_bound_by_recency_not_sample(self) -> None:
        # The trailing-quarter dry run legitimately overlaps training
        # data (its purpose is behavioral, not performance — R1 has no
        # net gate), so only recency/span are bound there.
        def artifact(window: dict) -> dict:
            return {
                "schema_version": GATE_SCHEMA_VERSION,
                "profile": "csi800_n5",
                "scope": SCOPE_ENSEMBLE,
                "subject": {"manifest_sha256": "cd" * 32},
                "window": window,
                "gates": {"degeneracy": {"verdict": "PASS"},
                          "constraint_dry_run": {"verdict": "PASS"},
                          "serving_veto": {"verdict": "PASS"}},
                "overall": "PASS",
            }

        kwargs = {"scope": SCOPE_ENSEMBLE,
                  "expected_subject_sha": "cd" * 32,
                  "now_iso": _NOW_ISO}
        # Overlapping the newest member's training window is fine.
        check_gate_artifact(
            artifact({"window_start": "2025-09-20",
                      "window_end": "2025-12-19"}), **kwargs)
        with self.assertRaises(RotationRefusal):   # two years stale
            check_gate_artifact(
                artifact({"window_start": "2023-09-20",
                          "window_end": "2023-12-19"}), **kwargs)

    def test_member_meta_binding_mismatch_refused(self) -> None:
        # The trainer-integrity gate judged the SIDECAR — a regenerated
        # sidecar under the same pickle must invalidate the artifact.
        check_gate_artifact(
            _member_gate_artifact(), scope=SCOPE_MEMBER,
            expected_subject_sha="ab" * 32,
            expected_meta_sha="ac" * 32)
        with self.assertRaises(RotationRefusal) as ctx:
            check_gate_artifact(
                _member_gate_artifact(), scope=SCOPE_MEMBER,
                expected_subject_sha="ab" * 32,
                expected_meta_sha="ff" * 32)
        self.assertIn("sidecar", str(ctx.exception))

    def test_ensemble_scope_binds_manifest_sha(self) -> None:
        artifact = {
            "schema_version": GATE_SCHEMA_VERSION,
            "profile": "csi800_n5",
            "scope": SCOPE_ENSEMBLE,
            "subject": {"manifest_sha256": "cd" * 32},
            "gates": {"degeneracy": {"verdict": "PASS"},
                      "constraint_dry_run": {"verdict": "PASS"},
                      "serving_veto": {"verdict": "PASS"}},
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
