"""Rotation executor end-to-end states (PR-B') — a scratch git repo
plays origin/main, stub files play members; NO qlib, NO real models
(the executor moves a manifest file after the gates said yes — model
loading was proven by the gate artifacts' binding).

States (codex #389 r2/r3/r4/r11 + tasks §PR-B'):
  legal rotation full chain (backup written + single-step rollback) /
  gate artifact missing refused / gate artifact FAIL refused /
  LOSE frozen / expired WIN frozen / absent status artifact refused /
  tampered candidate (plan-integrity) refused — each refusal with
  manifest bytes untouched.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.retrain_gate_lib import (  # noqa: E402
    GATE_SCHEMA_VERSION,
    SCOPE_ENSEMBLE,
    SCOPE_MEMBER,
)
from scripts.rotate_ensemble_member import main as rotate_main  # noqa: E402
from scripts.rotation_lib import (  # noqa: E402
    RECERT_STATUS_PATH,
    RECERT_STATUS_SCHEMA_VERSION,
)

# Staggered quarterly windows satisfying the serving-loader pins.
_CURRENT_WINDOWS = [
    ("2022-12-20", "2024-12-18"),
    ("2023-03-20", "2025-03-18"),
    ("2023-06-20", "2025-06-18"),
]
_NEW_WINDOW = ("2023-09-20", "2025-09-18")   # +92d gap, 729d span


class _StubModel:
    """Pickle-able member stub with the .predict serving requires."""

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def predict(self, dataset, segment="infer"):  # noqa: ANN001
        return None


def _write_member_files(tmp: Path, name: str, window: tuple[str, str],
                        ) -> dict:
    """A REAL loadable member (pkl + trainer sidecar with the version
    chain the serving loader enforces) — execute's member-chain
    re-validation (codex #391 r9) unpickles these."""
    import hashlib
    import pickle

    import lightgbm

    pkl = tmp / f"{name}.pkl"
    pkl.write_bytes(pickle.dumps(_StubModel(name)))
    meta = tmp / f"{name}.pkl.meta.json"
    meta.write_text(json.dumps({
        "schema_version": "v1", "model_type": "LGBModel",
        "best_iteration": 321, "num_boost_round": 1000,
        "lightgbm_version": lightgbm.__version__,
        "pkl_sha256": hashlib.sha256(pkl.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    return {
        "pkl_path": str(pkl),
        "pkl_sha256": hashlib.sha256(pkl.read_bytes()).hexdigest(),
        "meta_path": str(meta),
        "meta_sha256": hashlib.sha256(meta.read_bytes()).hexdigest(),
        "fit_start": window[0], "fit_end": window[1],
    }


# Pinned committer instant — the 15-month validity clock in these
# tests must not depend on the machine's real clock.
_COMMIT_INSTANT = "2026-07-01T10:00:00+08:00"


def _git(repo: Path, *args: str) -> None:
    import os

    env = dict(os.environ)
    env["GIT_COMMITTER_DATE"] = _COMMIT_INSTANT
    env["GIT_AUTHOR_DATE"] = _COMMIT_INSTANT
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t",
         "-c", "user.name=t", *args],
        check=True, capture_output=True, env=env)


def _make_mainline_repo(repo: Path, *, status: dict | None) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(repo)],
                   check=True, capture_output=True)
    if status is not None:
        target = repo / RECERT_STATUS_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(status, indent=2),
                          encoding="utf-8")
    else:
        (repo / "README.md").write_text("no status", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "state")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")


def _win_status() -> dict:
    return {
        "schema_version": RECERT_STATUS_SCHEMA_VERSION,
        "verdict": "WIN",
        "verdict_sidecar_path":
            "docs/research/csi800_cadence_verdict.json",
        "verdict_sidecar_sha256": "6a" * 32,
        "evidence_anchor_commit": "3f" * 20,
        "note": "bootstrap WIN for executor tests",
    }


class RotationExecutorStates(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        # Current production manifest. The OLDEST member (dropped by
        # the rotation) may be a phantom entry — execute's member-chain
        # re-validation runs over the CANDIDATE members only; members
        # 1/2 survive into the candidate and must be real loadable
        # stubs (codex #391 r9).
        members = [{
            "pkl_path": "Z:/prod/member_0.pkl",
            "pkl_sha256": "00" * 32,
            "meta_path": "Z:/prod/member_0.pkl.meta.json",
            "meta_sha256": "0a" * 32,
            "fit_start": _CURRENT_WINDOWS[0][0],
            "fit_end": _CURRENT_WINDOWS[0][1],
        }]
        self.kept_members = [
            _write_member_files(self.tmp, f"member_{i}",
                                _CURRENT_WINDOWS[i])
            for i in (1, 2)
        ]
        members.extend(self.kept_members)
        self.manifest = self.tmp / "production_manifest.json"
        self.manifest.write_text(json.dumps({
            "schema_version": "csi800_n5_ensemble_manifest_v1",
            "members": members}, indent=2), encoding="utf-8")
        self.original_bytes = self.manifest.read_bytes()

        # The incoming member: a REAL loadable stub (hashed by `plan`,
        # unpickled by execute's member-chain re-validation).
        new_files = _write_member_files(self.tmp, "new_member",
                                        _NEW_WINDOW)
        self.new_pkl = Path(new_files["pkl_path"])
        self.new_meta = Path(new_files["meta_path"])

        self.candidate = self.tmp / "candidate_manifest.json"
        rc = rotate_main([
            "plan",
            "--manifest", str(self.manifest),
            "--new-pkl", str(self.new_pkl),
            "--new-meta", str(self.new_meta),
            "--fit-start", _NEW_WINDOW[0],
            "--fit-end", _NEW_WINDOW[1],
            "--out", str(self.candidate),
        ])
        self.assertEqual(0, rc, "plan must succeed")
        self.new_pkl_sha = json.loads(
            self.candidate.read_text(encoding="utf-8"),
        )["members"][-1]["pkl_sha256"]
        import hashlib
        self.candidate_sha = hashlib.sha256(
            self.candidate.read_bytes()).hexdigest()

        self.new_meta_sha = json.loads(
            self.candidate.read_text(encoding="utf-8"),
        )["members"][-1]["meta_sha256"]
        self.member_gate = self.tmp / "member_gate.json"
        self.ensemble_gate = self.tmp / "ensemble_gate.json"
        self._write_gate(self.member_gate, SCOPE_MEMBER,
                         {"pkl_sha256": self.new_pkl_sha,
                          "meta_sha256": self.new_meta_sha})
        self._write_gate(self.ensemble_gate, SCOPE_ENSEMBLE,
                         {"manifest_sha256": self.candidate_sha})

        self.repo = self.tmp / "mainline"
        _make_mainline_repo(self.repo, status=_win_status())

    _GATES_BY_SCOPE = {
        SCOPE_MEMBER: ("trainer_integrity", "ic_direction"),
        SCOPE_ENSEMBLE: ("degeneracy", "constraint_dry_run",
                         "serving_veto"),
    }

    def _write_gate(self, path: Path, scope: str, subject: dict,
                    overall: str = "PASS") -> None:
        path.write_text(json.dumps({
            "schema_version": GATE_SCHEMA_VERSION,
            "scope": scope, "subject": subject,
            "gates": {name: {"verdict": overall}
                      for name in self._GATES_BY_SCOPE[scope]},
            "overall": overall}), encoding="utf-8")

    def _execute(self, *, now: str = "2026-08-01T00:00:00+00:00",
                 repo: Path | None = None) -> int:
        return rotate_main([
            "execute",
            "--manifest", str(self.manifest),
            "--candidate", str(self.candidate),
            "--member-gate", str(self.member_gate),
            "--ensemble-gate", str(self.ensemble_gate),
            "--repo", str(repo or self.repo),
            "--now", now,
        ])

    def _assert_manifest_untouched(self) -> None:
        self.assertEqual(self.original_bytes,
                         self.manifest.read_bytes())
        self.assertEqual([], list(self.tmp.glob("*.pre_rotation_*")))
        # The private staging copy (unique mkstemp name) must not
        # survive a refusal either.
        self.assertEqual([], list(self.tmp.glob("*.swap*")))

    def test_legal_rotation_full_chain(self) -> None:
        import os
        import stat

        pre_stat = os.stat(self.manifest)
        pre_mode = stat.S_IMODE(pre_stat.st_mode)
        rc = self._execute()
        self.assertEqual(0, rc)
        # codex #391 r10/r11: the swap must not narrow the manifest's
        # permission bits to mkstemp's 0600 nor change its group (on
        # POSIX CI the mode check is a real 0644-vs-0600 assertion;
        # on Windows both are trivially equal).
        post_stat = os.stat(self.manifest)
        self.assertEqual(pre_mode, stat.S_IMODE(post_stat.st_mode))
        self.assertEqual(pre_stat.st_gid, post_stat.st_gid)
        # Manifest now equals the candidate; oldest member is gone.
        rotated = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(
            self.candidate.read_bytes(), self.manifest.read_bytes())
        self.assertEqual(
            _NEW_WINDOW[1], rotated["members"][-1]["fit_end"])
        self.assertNotIn(
            "2024-12-18",
            [m["fit_end"] for m in rotated["members"]])
        # Backup carries the EXACT pre-rotation bytes; rollback is the
        # single step of restoring it.
        backups = list(self.tmp.glob(
            "production_manifest.json.pre_rotation_*"))
        self.assertEqual(1, len(backups))
        self.assertEqual(self.original_bytes, backups[0].read_bytes())
        backups[0].replace(self.manifest)          # single-step rollback
        self.assertEqual(self.original_bytes,
                         self.manifest.read_bytes())

    def test_missing_member_gate_refused(self) -> None:
        self.member_gate.unlink()
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_missing_ensemble_gate_refused(self) -> None:
        self.ensemble_gate.unlink()
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_failed_member_gate_refused(self) -> None:
        # codex #389 r11: the gate FAILED but the executor is invoked
        # anyway — the member must NOT enter production.
        self._write_gate(self.member_gate, SCOPE_MEMBER,
                         {"pkl_sha256": self.new_pkl_sha},
                         overall="FAIL")
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_failed_ensemble_gate_refused(self) -> None:
        self._write_gate(self.ensemble_gate, SCOPE_ENSEMBLE,
                         {"manifest_sha256": self.candidate_sha},
                         overall="FAIL")
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_unbound_member_gate_refused(self) -> None:
        self._write_gate(self.member_gate, SCOPE_MEMBER,
                         {"pkl_sha256": "de" * 32,
                          "meta_sha256": self.new_meta_sha})
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_member_gate_meta_binding_mismatch_refused(self) -> None:
        # The gate judged a DIFFERENT sidecar than the one rotating in
        # (adversarial self-review: pkl-only binding would let a
        # regenerated sidecar ride an old artifact into production).
        self._write_gate(self.member_gate, SCOPE_MEMBER,
                         {"pkl_sha256": self.new_pkl_sha,
                          "meta_sha256": "de" * 32})
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_lying_overall_gate_refused(self) -> None:
        # overall says PASS but a per-gate block says FAIL — refused.
        payload = json.loads(self.member_gate.read_text(encoding="utf-8"))
        payload["gates"]["ic_direction"]["verdict"] = "FAIL"
        self.member_gate.write_text(json.dumps(payload),
                                    encoding="utf-8")
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_bad_manifest_inputs_refused_not_traceback(self) -> None:
        # codex #391 r4: typo'd/missing/corrupt manifest inputs are
        # ordinary precondition failures — classified refusal (exit 1),
        # never an escaping traceback.
        missing = self.tmp / "no_such_manifest.json"
        rc = rotate_main([
            "plan",
            "--manifest", str(missing),
            "--new-pkl", str(self.new_pkl),
            "--new-meta", str(self.new_meta),
            "--fit-start", _NEW_WINDOW[0],
            "--fit-end", _NEW_WINDOW[1],
            "--out", str(self.tmp / "x.json"),
        ])
        self.assertEqual(1, rc)
        # codex #391 r6: a live manifest with a wrong/missing schema
        # must not be silently converted into a fresh v1 candidate.
        for schema in ("v0", None):
            payload = json.loads(self.manifest.read_text(
                encoding="utf-8"))
            if schema is None:
                del payload["schema_version"]
            else:
                payload["schema_version"] = schema
            stale = self.tmp / f"stale_schema_{schema}.json"
            stale.write_text(json.dumps(payload), encoding="utf-8")
            rc = rotate_main([
                "plan",
                "--manifest", str(stale),
                "--new-pkl", str(self.new_pkl),
                "--new-meta", str(self.new_meta),
                "--fit-start", _NEW_WINDOW[0],
                "--fit-end", _NEW_WINDOW[1],
                "--out", str(self.tmp / f"s_{schema}.json"),
            ])
            self.assertEqual(1, rc, schema)
            self.assertFalse((self.tmp / f"s_{schema}.json").exists())
        corrupt = self.tmp / "corrupt_manifest.json"
        corrupt.write_text("not json {", encoding="utf-8")
        rc = rotate_main([
            "plan",
            "--manifest", str(corrupt),
            "--new-pkl", str(self.new_pkl),
            "--new-meta", str(self.new_meta),
            "--fit-start", _NEW_WINDOW[0],
            "--fit-end", _NEW_WINDOW[1],
            "--out", str(self.tmp / "y.json"),
        ])
        self.assertEqual(1, rc)
        # Same class on execute: missing new-member artifacts refuse.
        self.new_pkl.unlink()
        rc = rotate_main([
            "plan",
            "--manifest", str(self.manifest),
            "--new-pkl", str(self.new_pkl),
            "--new-meta", str(self.new_meta),
            "--fit-start", _NEW_WINDOW[0],
            "--fit-end", _NEW_WINDOW[1],
            "--out", str(self.tmp / "z.json"),
        ])
        self.assertEqual(1, rc)

    def test_plan_out_aliasing_live_manifest_refused(self) -> None:
        # codex #391 r2: plan --out pointed at the live manifest would
        # overwrite production during PLANNING, before certification/
        # gates/backup — refused with the manifest untouched.
        rc = rotate_main([
            "plan",
            "--manifest", str(self.manifest),
            "--new-pkl", str(self.new_pkl),
            "--new-meta", str(self.new_meta),
            "--fit-start", _NEW_WINDOW[0],
            "--fit-end", _NEW_WINDOW[1],
            "--out", str(self.manifest),
        ])
        self.assertEqual(1, rc)
        self.assertEqual(self.original_bytes,
                         self.manifest.read_bytes())

    def test_illegal_plan_produces_no_candidate_file(self) -> None:
        # Adversarial self-review: an illegal rotation must never even
        # publish a candidate file at --out (a stale invalid candidate
        # is an attractive wrong input for the next session).
        bad_out = self.tmp / "bad_candidate.json"
        rc = rotate_main([
            "plan",
            "--manifest", str(self.manifest),
            "--new-pkl", str(self.new_pkl),
            "--new-meta", str(self.new_meta),
            # Same quarter as the current newest member — violates the
            # strictly-increasing fit_end pin in the serving loader.
            "--fit-start", _CURRENT_WINDOWS[-1][0],
            "--fit-end", _CURRENT_WINDOWS[-1][1],
            "--out", str(bad_out),
        ])
        self.assertEqual(1, rc)
        self.assertFalse(bad_out.exists())
        self.assertFalse(
            bad_out.with_suffix(bad_out.suffix + ".tmp").exists())

    def test_lose_status_freezes(self) -> None:
        lose = _win_status()
        lose["verdict"] = "LOSE"
        del lose["verdict_sidecar_path"]
        del lose["verdict_sidecar_sha256"]
        repo = self.tmp / "mainline_lose"
        _make_mainline_repo(repo, status=lose)
        self.assertEqual(1, self._execute(repo=repo))
        self._assert_manifest_untouched()

    def test_expired_win_freezes(self) -> None:
        # Commit instant pinned at 2026-07-01 (+15 months = 2027-10-01);
        # the day after that window the WIN is expired.
        self.assertEqual(
            1, self._execute(now="2027-10-02T00:00:00+00:00"))
        self._assert_manifest_untouched()

    def test_absent_status_artifact_refused(self) -> None:
        repo = self.tmp / "mainline_empty"
        _make_mainline_repo(repo, status=None)
        self.assertEqual(1, self._execute(repo=repo))
        self._assert_manifest_untouched()

    def test_non_utf8_status_bytes_refused(self) -> None:
        # codex #391 r5: a status artifact whose bytes are not UTF-8
        # is malformed certification state — classified refusal, not
        # an escaping UnicodeDecodeError traceback.
        repo = self.tmp / "mainline_binary"
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(repo)],
                       check=True, capture_output=True)
        target = repo / RECERT_STATUS_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\xff\xfe\x00corrupt")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "binary status")
        _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.assertEqual(1, self._execute(repo=repo))
        self._assert_manifest_untouched()

    def test_member_pkl_deleted_after_gates_refused(self) -> None:
        # codex #391 r9: the gates proved the members were valid when
        # they RAN — a pkl deleted since then must refuse at execute,
        # or serving would refuse the freshly installed manifest at
        # the next morning run.
        Path(self.kept_members[0]["pkl_path"]).unlink()
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_member_sidecar_replaced_after_gates_refused(self) -> None:
        # A sidecar rewritten after gate time breaks the digest chain
        # the candidate manifest declares — refuse, zero writes.
        Path(self.kept_members[1]["meta_path"]).write_text(
            json.dumps({"schema_version": "v1",
                        "model_type": "LGBModel"}), encoding="utf-8")
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_tampered_candidate_refused(self) -> None:
        # Keep the serving-loader pins satisfied but break the plan
        # equality: slot 0 of the candidate is NOT current[1].
        payload = json.loads(self.candidate.read_text(encoding="utf-8"))
        payload["members"][0]["meta_sha256"] = "9b" * 32
        self.candidate.write_text(json.dumps(payload, indent=2),
                                  encoding="utf-8")
        import hashlib
        self._write_gate(
            self.ensemble_gate, SCOPE_ENSEMBLE,
            {"manifest_sha256": hashlib.sha256(
                self.candidate.read_bytes()).hexdigest()})
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()


if __name__ == "__main__":
    unittest.main()
