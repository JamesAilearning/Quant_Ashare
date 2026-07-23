"""Bootstrap-cutover WRITE-phase pins (PR-C', codex #392 r3).

The gate phase is adjudicated by the pure lib (test_bootstrap_cutover_
lib.py). What only the executor can be held to is the WRITE
handoff — in particular the permission mirroring that keeps the
freshly created production manifest readable by the serving account
(mkstemp would otherwise install 0600), plus the artifacts the switch
must leave behind.

The gates are stubbed (they need git, certify and a bundle); every
write below is the real one.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import scripts.bootstrap_ensemble_cutover as bc  # noqa: E402
from scripts.rotation_lib import (  # noqa: E402
    RECERT_STATUS_PATH,
    parse_recert_status,
)

_WINDOWS = [("2023-08-14", "2025-08-13"),
            ("2023-11-13", "2025-11-13"),
            ("2024-02-19", "2026-02-13")]


class _Member:
    def __init__(self, pkl: Path, meta: Path,
                 window: tuple[str, str]) -> None:
        self.pkl_path = str(pkl)
        self.pkl_sha256 = "aa" * 32
        self.meta_path = str(meta)
        self.meta_sha256 = "bb" * 32
        self.fit_start, self.fit_end = window


class CutoverWritePhase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        self.incumbent = self.tmp / "alpha158_lgb_pit.pkl"
        self.incumbent.write_bytes(b"incumbent-model")
        # A permissive-but-explicit mode: on POSIX this is a real
        # 0644-vs-0600 assertion, on Windows both sides collapse.
        os.chmod(self.incumbent, 0o644)
        self.incumbent_mode = stat.S_IMODE(
            os.stat(self.incumbent).st_mode)

        self.members = []
        for i, window in enumerate(_WINDOWS):
            pkl = self.tmp / f"member_{i}.pkl"
            pkl.write_bytes(b"member-model")
            meta = self.tmp / f"member_{i}.pkl.meta.json"
            meta.write_text("{}", encoding="utf-8")
            self.members.append(_Member(pkl, meta, window))

        self.manifest_bytes = json.dumps({
            "schema_version": "csi800_n5_ensemble_manifest_v1",
            "members": []}).encode("utf-8")
        self.manifest_out = self.tmp / "prod" / "manifest.json"
        self.evidence = {
            "campaign": {
                "verdict_sidecar_path": "docs/research/v.json",
                "verdict_sidecar_sha256": "6a" * 32,
                "evidence_anchor_commit": "3f" * 20,
                "conservative_net_annualized": 0.0652,
                "gross_retention": 0.7881,
                "read_at_rev": "ab" * 20,
            },
            "isoweek": {"net_annualized": 0.0601, "num_folds": 23,
                        "rev": "ab" * 20},
            "gate_artifacts": {"ensemble": "g.json"},
            "members": self.members,
            "manifest_sha256": "cd" * 32,
            "manifest_bytes": self.manifest_bytes,
        }

    def _run(self) -> int:
        with patch.object(bc, "_gate_promotion",
                          return_value=self.evidence):
            return bc.main([
                "--manifest", str(self.tmp / "candidate.json"),
                "--ensemble-gate", str(self.tmp / "eg.json"),
                "--incumbent", str(self.incumbent),
                "--manifest-out", str(self.manifest_out),
                "--repo", str(self.tmp),
                "--now", "2026-07-23T00:00:00+00:00",
            ])

    def test_manifest_installed_with_incumbent_readability(self) -> None:
        self.assertEqual(0, self._run())
        self.assertTrue(self.manifest_out.is_file())
        self.assertEqual(self.manifest_bytes,
                         self.manifest_out.read_bytes())
        # The regression this pins: mkstemp's 0600 must NOT reach
        # production — the manifest carries the incumbent's mode.
        installed = os.stat(self.manifest_out)
        self.assertEqual(self.incumbent_mode,
                         stat.S_IMODE(installed.st_mode))
        if hasattr(os, "chown"):
            incumbent_stat = os.stat(self.incumbent)
            self.assertEqual(incumbent_stat.st_uid, installed.st_uid)
            self.assertEqual(incumbent_stat.st_gid, installed.st_gid)
        # ...and no staging residue survives the install.
        self.assertEqual([], list(self.manifest_out.parent
                                  .glob("*.install*")))

    def test_mode_handoff_is_explicit(self) -> None:
        # Platform-independent pin of the same regression: Windows
        # only tracks the read-only bit, so the mode ASSERTION above
        # is only a real 0644-vs-0600 check on the POSIX CI legs.
        # Here we pin the handoff itself — the executor must chmod the
        # STAGING file to the incumbent's mode before installing it.
        calls: list[tuple[str, int]] = []
        real_chmod = os.chmod

        def spy(path, mode, *a, **kw):  # noqa: ANN001, ANN002
            calls.append((str(path), mode))
            return real_chmod(path, mode, *a, **kw)

        with patch.object(bc.os, "chmod", spy):
            self.assertEqual(0, self._run())
        staged = [c for c in calls if ".install." in c[0]]
        self.assertTrue(staged, "the staging file was never chmod'ed")
        self.assertEqual(self.incumbent_mode, staged[-1][1])

    def test_baseline_records_the_installed_mode(self) -> None:
        self.assertEqual(0, self._run())
        baseline = json.loads(
            (self.tmp / bc.BASELINE_PATH).read_text(encoding="utf-8"))
        self.assertEqual(oct(self.incumbent_mode),
                         baseline["serving"]["manifest_mode"])
        self.assertEqual(3, len(baseline["serving"]["members"]))
        self.assertIn("incumbent_backup", baseline)

    def test_switch_leaves_the_full_artifact_set(self) -> None:
        self.assertEqual(0, self._run())
        # Incumbent backup (the rollback kit).
        backups = list(self.tmp.glob("*.pre_bootstrap_*"))
        self.assertTrue(backups)
        self.assertEqual(b"incumbent-model", backups[0].read_bytes())
        # Per-member inference meta with the manifest's fit windows.
        for member, window in zip(self.members, _WINDOWS, strict=True):
            meta = json.loads(
                Path(member.pkl_path).with_suffix(".meta.json")
                .read_text(encoding="utf-8"))
            self.assertEqual(window[0], meta["fit_start_for_inference"])
            self.assertEqual(window[1], meta["fit_end_for_inference"])
        # The initial status artifact — parseable by the QUARTERLY
        # executor that will read it for the next 15 months.
        status_text = (self.tmp / RECERT_STATUS_PATH).read_text(
            encoding="utf-8")
        status = parse_recert_status(status_text)
        self.assertEqual("WIN", status["verdict"])
        self.assertEqual("6a" * 32, status["verdict_sidecar_sha256"])

    def test_dry_run_writes_nothing(self) -> None:
        with patch.object(bc, "_gate_promotion",
                          return_value=self.evidence):
            rc = bc.main([
                "--manifest", str(self.tmp / "candidate.json"),
                "--ensemble-gate", str(self.tmp / "eg.json"),
                "--incumbent", str(self.incumbent),
                "--manifest-out", str(self.manifest_out),
                "--repo", str(self.tmp),
                "--now", "2026-07-23T00:00:00+00:00",
                "--dry-run",
            ])
        self.assertEqual(0, rc)
        self.assertFalse(self.manifest_out.exists())
        self.assertFalse((self.tmp / RECERT_STATUS_PATH).exists())
        self.assertFalse((self.tmp / bc.BASELINE_PATH).exists())
        self.assertEqual([], list(self.tmp.glob("*.pre_bootstrap_*")))


if __name__ == "__main__":
    unittest.main()
