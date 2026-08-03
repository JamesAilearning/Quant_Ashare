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
from datetime import datetime
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


class MemberRunConfigResolution(unittest.TestCase):
    """codex #392 r9: the semantic gate must read the PRODUCER's run
    config (``<run>/config.yaml``), never a stray copy in the
    artifacts dir that an upward search would hit first."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.run_dir = Path(self._tmp.name) / "run_x"
        (self.run_dir / "artifacts").mkdir(parents=True)
        self.pkl = self.run_dir / "artifacts" / "model.pkl"
        self.pkl.write_bytes(b"model")
        (self.run_dir / "config.yaml").write_text(
            "instruments: csi800\n", encoding="utf-8")
        self.addCleanup(self._tmp.cleanup)

    def test_reads_the_run_root_config(self) -> None:
        resolved = bc._member_run_config(self.pkl)
        assert resolved is not None
        cfg, sha = resolved
        self.assertEqual("csi800", cfg["instruments"])
        # The digest is over the SAME bytes the parse consumed
        # (codex #392 r14) — what the provenance check binds is what
        # the semantic gate read.
        import hashlib

        self.assertEqual(
            hashlib.sha256(
                (self.run_dir / "config.yaml").read_bytes()
            ).hexdigest(),
            sha)

    def test_stray_artifacts_config_is_ambiguous_and_refuses(self) -> None:
        # The scenario: a copied/stale config in artifacts/ that an
        # upward search would have validated INSTEAD of the real one.
        (self.run_dir / "artifacts" / "config.yaml").write_text(
            "instruments: csi300\n", encoding="utf-8")
        self.assertIsNone(bc._member_run_config(self.pkl))

    def test_unknown_layout_refuses(self) -> None:
        flat = Path(self._tmp.name) / "model.pkl"
        flat.write_bytes(b"model")
        self.assertIsNone(bc._member_run_config(flat))

    def test_missing_run_config_refuses(self) -> None:
        (self.run_dir / "config.yaml").unlink()
        self.assertIsNone(bc._member_run_config(self.pkl))


class InjectedNowValidation(unittest.TestCase):
    """codex #392 r15: `--now` is test determinism, not evidence time
    travel — it must sit within 24h of the wall clock."""

    _WALL = datetime.fromisoformat("2026-08-03T12:00:00+00:00")

    def test_near_present_admits(self) -> None:
        for iso in ("2026-08-03T00:00:00+00:00",
                    "2026-08-04T11:00:00+00:00",
                    "2026-08-03T20:00:00+08:00"):
            bc._validate_injected_now(iso, self._WALL)

    def test_time_travel_refused(self) -> None:
        # The exact abuse: pin --now beside the frozen dry-run window
        # long after the fact.
        for iso in ("2026-06-17T00:00:00+00:00",
                    "2027-08-03T12:00:00+00:00"):
            with self.assertRaises(bc.CutoverRefusal, msg=iso):
                bc._validate_injected_now(iso, self._WALL)

    def test_naive_or_garbage_refused(self) -> None:
        for iso in ("2026-08-03T12:00:00", "not-a-time", ""):
            with self.assertRaises(bc.CutoverRefusal, msg=iso):
                bc._validate_injected_now(iso, self._WALL)


class RegisteredDefaultExpansion(unittest.TestCase):
    """codex #392 r15: the expected provider identity comes from the
    COMMITTED template default, never from the live environment."""

    def test_committed_default_wins_over_live_env(self) -> None:
        with patch.dict(os.environ,
                        {"QUANT_PROVIDER_URI": "Z:/wrong_bundle"}):
            out = bc._expand_registered_default(
                "${QUANT_PROVIDER_URI:-D:/qlib_data/my_cn_data_pit}",
                "config.yaml@abc")
        self.assertEqual("D:/qlib_data/my_cn_data_pit", out)

    def test_defaultless_template_refused(self) -> None:
        with self.assertRaises(bc.CutoverRefusal) as ctx:
            bc._expand_registered_default(
                "${QUANT_PROVIDER_URI}", "config.yaml@abc")
        self.assertIn("no committed default", str(ctx.exception))

    def test_literal_passes_through(self) -> None:
        self.assertEqual(
            "D:/qlib_data/my_cn_data_pit",
            bc._expand_registered_default(
                "D:/qlib_data/my_cn_data_pit", "config.yaml@abc"))


class RegisteredCommitCheck(unittest.TestCase):
    """codex #392 r15: the training commit must be mainline ancestry
    under the pinned revision — adjudicated by real git."""

    @classmethod
    def setUpClass(cls) -> None:
        import subprocess

        cls._tmp = TemporaryDirectory()
        cls.repo = Path(cls._tmp.name)
        # Hermetic fixture repo: blank out global/system git config so
        # the user's settings (gpgsign, hooks, templates) cannot leak
        # into — or fail — the fixture commits.
        env = {**os.environ,
               "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_CONFIG_SYSTEM": os.devnull,
               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

        def git(*args: str) -> str:
            proc = subprocess.run(
                ["git", *args], cwd=cls.repo, capture_output=True,
                check=True, text=True, env=env)
            return proc.stdout.strip()

        git("init", "-q")
        (cls.repo / "a.txt").write_text("1", encoding="utf-8")
        git("add", "a.txt")
        git("commit", "-q", "-m", "c1")
        cls.c1 = git("rev-parse", "HEAD")
        (cls.repo / "a.txt").write_text("2", encoding="utf-8")
        git("commit", "-q", "-am", "c2")
        cls.c2 = git("rev-parse", "HEAD")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_ancestor_commit_admits(self) -> None:
        bc._require_registered_commit(
            self.repo, self.c1, self.c2, "member[0]")
        bc._require_registered_commit(
            self.repo, self.c2, self.c2, "member[0]")

    def test_descendant_of_pin_refused(self) -> None:
        # Trained on code NEWER than the pinned mainline snapshot —
        # not registered under the revision the gates read.
        with self.assertRaises(bc.CutoverRefusal) as ctx:
            bc._require_registered_commit(
                self.repo, self.c2, self.c1, "member[0]")
        self.assertIn("unregistered source", str(ctx.exception))

    def test_unknown_commit_refused(self) -> None:
        with self.assertRaises(bc.CutoverRefusal):
            bc._require_registered_commit(
                self.repo, "de" * 20, self.c2, "member[0]")


class BackupExclusiveCreation(unittest.TestCase):
    """codex #392 r14: backups are born O_EXCL — creation itself is
    the adjudication, so overlapping cutovers can never share (and
    then roll-back-destroy) one rollback kit."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.incumbent = self.tmp / "alpha158_lgb_pit.pkl"
        self.incumbent.write_bytes(b"incumbent-model")

    def test_existing_backup_refuses_and_is_not_registered(self) -> None:
        foreign = self.incumbent.with_name(
            self.incumbent.name + ".pre_bootstrap_STAMP")
        foreign.write_bytes(b"the-winning-runs-rollback-kit")
        created: list[Path] = []
        with self.assertRaises(bc.CutoverRefusal):
            bc._backup_incumbent(self.incumbent, "STAMP", created)
        # The foreign kit was neither truncated nor registered for
        # rollback deletion.
        self.assertEqual(b"the-winning-runs-rollback-kit",
                         foreign.read_bytes())
        self.assertEqual([], created)

    def test_backup_copies_bytes_and_registers(self) -> None:
        created: list[Path] = []
        record = bc._backup_incumbent(self.incumbent, "STAMP", created)
        dst = Path(record[self.incumbent.name])
        self.assertEqual(b"incumbent-model", dst.read_bytes())
        self.assertIn(dst, created)


class ProviderUriCanonicalization(unittest.TestCase):
    """codex #392 r13: the family binding must not refuse two
    spellings of the SAME bundle (``~`` vs absolute, separators,
    case), and must still refuse genuinely different bundles."""

    def test_home_and_absolute_spellings_converge(self) -> None:
        a = bc._canonicalize_provider_uri(
            {"provider_uri": "~/bundle_r13"})
        b = bc._canonicalize_provider_uri(
            {"provider_uri": os.path.join(
                os.path.expanduser("~"), "bundle_r13")})
        self.assertEqual(a["provider_uri"], b["provider_uri"])

    def test_different_bundles_stay_different(self) -> None:
        a = bc._canonicalize_provider_uri(
            {"provider_uri": "~/bundle_r13"})
        b = bc._canonicalize_provider_uri(
            {"provider_uri": "~/bundle_other"})
        self.assertNotEqual(a["provider_uri"], b["provider_uri"])

    def test_uses_the_canonical_runtime_normalizer(self) -> None:
        # Same helper init_qlib_canonical applies — "equal after
        # normalization" must mean "qlib treated them as the same
        # bundle", not a private approximation of it.
        from src.core.qlib_runtime import _normalize_provider_uri

        raw = "~/bundle_r13"
        out = bc._canonicalize_provider_uri({"provider_uri": raw})
        self.assertEqual(_normalize_provider_uri(raw),
                         out["provider_uri"])

    def test_non_dict_and_absent_key_pass_through(self) -> None:
        self.assertIsNone(bc._canonicalize_provider_uri(None))
        self.assertEqual({}, bc._canonicalize_provider_uri({}))
        cfg = {"provider_uri": 7}
        self.assertEqual({"provider_uri": 7},
                         bc._canonicalize_provider_uri(cfg))


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
        # Pre-provisioned manifest directory (codex #392 r10: the
        # cutover refuses to create it with umask-dependent perms).
        (self.tmp / "prod").mkdir()
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
            "gate_artifacts": {"ensemble": {"path": "g.json",
                                            "sha256": "7c" * 32}},
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
        # codex #392 r13: the committed baseline binds the gate
        # artifacts by CONTENT digest, not just pathname.
        self.assertEqual(
            "7c" * 32,
            baseline["authorized_by"]["gate_artifacts"]["ensemble"]
            ["sha256"])

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

    def test_late_write_failure_rolls_back_everything(self) -> None:
        # codex #392 r11: a failure AFTER the manifest/baseline landed
        # (here: the once-only status appeared post-gates) must not
        # leave production half-switched. The incumbent canonical is
        # never modified, so the correct treatment is to DELETE every
        # artifact this run created — and report that state honestly.
        status_path = self.tmp / RECERT_STATUS_PATH
        status_path.parent.mkdir(parents=True)
        status_path.write_text("{}", encoding="utf-8")
        rc = self._run()
        self.assertEqual(1, rc)
        # Everything the run created was rolled back...
        self.assertFalse(self.manifest_out.exists())
        self.assertFalse((self.tmp / bc.BASELINE_PATH).exists())
        for member in self.members:
            self.assertFalse(
                Path(member.pkl_path).with_suffix(".meta.json").exists())
        self.assertEqual([], list(self.tmp.glob("*.pre_bootstrap_*")))
        self.assertEqual([], list(self.manifest_out.parent
                                  .glob("*.install*")))
        # ...the pre-existing status (NOT ours) survives untouched...
        self.assertEqual("{}", status_path.read_text(encoding="utf-8"))
        # ...and the incumbent was never modified.
        self.assertEqual(b"incumbent-model",
                         self.incumbent.read_bytes())

    def test_status_write_failure_after_exclusive_create_rolls_back(
            self) -> None:
        # codex #392 r12: the exclusive create can succeed and the
        # WRITE then fail (ENOSPC, quota, delayed I/O). The status
        # file exists at that moment — it must already be in the
        # rollback set, or the rollback removes the manifest and
        # metas while leaving a partial (empty but apparently
        # installable) status behind, blocking any retry.
        import builtins
        real_open = builtins.open
        status_path = self.tmp / RECERT_STATUS_PATH

        class _DiskFull:
            def __init__(self, fh):  # noqa: ANN001
                self._fh = fh

            def __enter__(self):
                return self

            def __exit__(self, *exc):  # noqa: ANN002
                self._fh.close()
                return False

            def write(self, data):  # noqa: ANN001
                raise OSError(28, "No space left on device")

        def failing(file, *a, **kw):  # noqa: ANN001, ANN002
            fh = real_open(file, *a, **kw)
            if a and "x" in str(a[0]) and Path(str(file)) == status_path:
                return _DiskFull(fh)
            return fh

        with patch.object(builtins, "open", failing):
            rc = self._run()
        self.assertEqual(1, rc)
        # The half-written status did NOT survive the rollback...
        self.assertFalse(status_path.exists())
        # ...nor did any other artifact of this run...
        self.assertFalse(self.manifest_out.exists())
        self.assertFalse((self.tmp / bc.BASELINE_PATH).exists())
        for member in self.members:
            self.assertFalse(
                Path(member.pkl_path).with_suffix(".meta.json").exists())
        self.assertEqual([], list(self.tmp.glob("*.pre_bootstrap_*")))
        self.assertEqual([], list(self.manifest_out.parent
                                  .glob("*.install*")))
        # ...and the incumbent was never modified.
        self.assertEqual(b"incumbent-model",
                         self.incumbent.read_bytes())

    def test_foreign_baseline_is_never_truncated(self) -> None:
        # codex #392 r14 P1: a baseline survivor (aborted run whose
        # rollback could not finish, or an overlapping cutover) must
        # not be truncated by our write nor deleted by our rollback.
        baseline_path = self.tmp / bc.BASELINE_PATH
        baseline_path.parent.mkdir(parents=True)
        baseline_path.write_text('{"owner": "someone-else"}',
                                 encoding="utf-8")
        rc = self._run()
        self.assertEqual(1, rc)
        self.assertEqual('{"owner": "someone-else"}',
                         baseline_path.read_text(encoding="utf-8"))
        # Everything this run created before the refusal rolled back.
        self.assertFalse(self.manifest_out.exists())
        self.assertFalse((self.tmp / RECERT_STATUS_PATH).exists())
        for member in self.members:
            self.assertFalse(
                Path(member.pkl_path).with_suffix(".meta.json").exists())
        self.assertEqual([], list(self.tmp.glob("*.pre_bootstrap_*")))
        self.assertEqual(b"incumbent-model",
                         self.incumbent.read_bytes())

    def test_foreign_inference_meta_is_never_truncated(self) -> None:
        # codex #392 r13 P1: a pre-existing `<model>.meta.json`
        # (another serving setup reusing the artifact, or an
        # overlapping cutover) must NOT be truncated by our write nor
        # deleted by our rollback — the exclusive create refuses, the
        # foreign bytes survive, and everything WE created rolls back.
        foreign = Path(self.members[1].pkl_path).with_suffix(
            ".meta.json")
        foreign.write_text('{"owner": "someone-else"}',
                           encoding="utf-8")
        rc = self._run()
        self.assertEqual(1, rc)
        self.assertEqual('{"owner": "someone-else"}',
                         foreign.read_text(encoding="utf-8"))
        # Everything this run created before the refusal rolled back.
        self.assertFalse(self.manifest_out.exists())
        self.assertFalse((self.tmp / bc.BASELINE_PATH).exists())
        self.assertFalse((self.tmp / RECERT_STATUS_PATH).exists())
        self.assertFalse(
            Path(self.members[0].pkl_path).with_suffix(
                ".meta.json").exists())
        self.assertEqual([], list(self.tmp.glob("*.pre_bootstrap_*")))
        self.assertEqual(b"incumbent-model",
                         self.incumbent.read_bytes())

    def test_post_link_cleanup_failure_is_not_a_failed_install(self) -> None:
        # codex #392 r11: once os.link succeeded the manifest IS
        # installed — a failing staging unlink must not surface as
        # "nothing installed"; it is benign residue, noted on the
        # success path.
        real_unlink = Path.unlink

        def stubborn(self, *a, **kw):  # noqa: ANN001, ANN002
            if ".install." in self.name:
                raise OSError("blocked by filesystem software")
            return real_unlink(self, *a, **kw)

        with patch.object(Path, "unlink", stubborn):
            rc = self._run()
        self.assertEqual(0, rc)
        self.assertTrue(self.manifest_out.is_file())
        self.assertEqual(self.manifest_bytes,
                         self.manifest_out.read_bytes())
        # The residue is real (unlink was blocked) — and documented.
        residue = list(self.manifest_out.parent.glob("*.install*"))
        self.assertEqual(1, len(residue))
        residue[0].unlink()  # test owns the temp dir

    def test_manifest_appearing_after_gates_is_not_clobbered(self) -> None:
        # codex #392 r10: the once-only precondition is racy — a
        # manifest that APPEARS between the (stubbed) gate phase and
        # the install must refuse, never be overwritten.
        self.manifest_out.write_bytes(b"appeared-after-gates")
        rc = self._run()
        self.assertEqual(1, rc)
        self.assertEqual(b"appeared-after-gates",
                         self.manifest_out.read_bytes())
        self.assertEqual([], list(self.manifest_out.parent
                                  .glob("*.install*")))

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
