#!/usr/bin/env python3
"""Quarterly ensemble-member rotation executor (PR-B' of
2026-07-20-csi800-n5-production-promotion — the R1 MAINTENANCE path).

Two subcommands:

  plan     Build the CANDIDATE manifest: current members minus the
           oldest, plus the new member (oldest->newest order kept).
           The candidate is validated by the STRICT serving loader
           (spacing/window/duplicate-identity pins) before it is
           written — an illegal rotation never even produces a file.

  execute  Perform the rotation, refusing with ZERO manifest writes
           unless every precondition holds:
           1. certification state — read EXCLUSIVELY via
              ``git show origin/main:docs/promotion/
              csi800_recert_status.json``; verdict comes from the file
              CONTENT (LOSE freezes; parse failure freezes); the
              15-month validity window anchors on the status-artifact
              PATH's mainline tip committer date (``git log -1
              --format=%cI origin/main -- <path>``) — codex #389
              r2/r3/r4/r5;
           2. gate artifacts — BOTH the member-scope and the
              ensemble-scope gate artifacts must exist, parse, carry
              ``overall: PASS`` and bind to the incoming member's pkl
              digest / the candidate manifest digest respectively
              (missing artifact = refuse; FAIL artifact = refuse —
              codex #389 r11);
           3. plan integrity — the candidate manifest must equal
              "current minus oldest plus gated member" exactly and
              re-validate under the serving loader;
           4. member-chain re-validation at execute time — the strict
              serving loader runs over the staged members (existence,
              digest chain, sidecar version guard, unpickle,
              ``.predict``) so a pkl/sidecar deleted or replaced since
              the gates ran can never be installed into a manifest
              serving would refuse next morning (codex #391 r9);
           5. backup — the pre-rotation manifest bytes are copied to a
              timestamped sibling BEFORE the swap; rollback is the
              single step of restoring that file;
           6. serialization — an exclusive advisory lock
              (``<manifest>.rotation.lock``) spans the whole
              adjudication INCLUDING the final replace, so concurrent
              executes refuse at acquisition instead of racing the
              digest recheck (codex #391 r13).

The executor never trains, never scores, never touches canonical
model artifacts — it moves ONE manifest file after the protocol's
gates said yes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.retrain_gate_lib import (  # noqa: E402
    SCOPE_ENSEMBLE,
    SCOPE_MEMBER,
)
from scripts.rotation_lib import (  # noqa: E402
    RotationRefusal,
    check_gate_artifact,
    git_show_status_cmd,
    git_status_tip_cmd,
    parse_recert_status,
    plan_rotated_members,
    recert_validity,
)
from src.inference.ensemble_serving import (  # noqa: E402
    EnsembleServingError,
    load_ensemble_manifest,
    load_member_models,
)

MANIFEST_SCHEMA_VERSION = "csi800_n5_ensemble_manifest_v1"


def _members_from_bytes(raw: bytes, what: str) -> list[dict[str, Any]]:
    # A typo'd/missing/corrupt manifest input is an ORDINARY
    # precondition failure (codex #391 r4) — it must surface through
    # the classified `[rotate] REFUSED` path like every other
    # certification/gate precondition, never a raw traceback.
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RotationRefusal(
            f"manifest unreadable: {what} ({exc})") from exc
    if not isinstance(payload, dict):
        raise RotationRefusal(
            f"{what}: manifest top level is not an object")
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        # codex #391 r6: an unknown/missing schema on the LIVE manifest
        # must fail closed — planning over it would silently convert an
        # unrecognized manifest contract into a fresh v1 candidate.
        raise RotationRefusal(
            f"{what}: manifest schema "
            f"{payload.get('schema_version')!r} != "
            f"{MANIFEST_SCHEMA_VERSION!r} — refusing to plan/execute "
            "over an unrecognized manifest contract")
    members = payload.get("members")
    if not isinstance(members, list):
        raise RotationRefusal(f"{what}: manifest carries no members list")
    return members


def _read_manifest_members(path: Path) -> list[dict[str, Any]]:
    return _members_from_bytes(
        _read_bytes_or_refuse(path, "manifest"), str(path))


class _ManifestLock:
    """Exclusive advisory lock serializing rotations on one manifest
    (codex #391 r13): the digest recheck alone leaves a window between
    check and ``os.replace`` — under the lock, executor-vs-executor
    races are closed entirely (the loser refuses instead of clobbering)
    and the recheck retains its role against out-of-band manual edits
    made before the lock was taken. The lockfile persists next to the
    manifest (deleting it would be racy); only the LOCK is released."""

    def __init__(self, manifest_path: Path) -> None:
        self._path = manifest_path.with_name(
            manifest_path.name + ".rotation.lock")
        self._fh: Any = None

    def __enter__(self) -> _ManifestLock:
        fh = open(self._path, "a+b")  # noqa: SIM115 — held past scope
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            fh.close()
            raise RotationRefusal(
                f"another rotation holds the manifest lock "
                f"({self._path}): {exc} — refusing to run "
                "concurrently") from exc
        self._fh = fh
        return self

    def __exit__(self, *exc_info: Any) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()


def _same_file(a: Path, b: Path) -> bool:
    """Do these paths name the same file? (codex #391 r22)

    ``resolve()`` equality catches symlink aliases; ``os.path.samefile``
    additionally catches HARDLINKS, which resolve to distinct paths
    while sharing an inode."""
    try:
        if a.resolve() == b.resolve():
            return True
    except OSError:
        pass
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _remove_or_note(path: Path) -> str | None:
    """Best-effort cleanup; returns a note when the file SURVIVES.

    A cleanup failure must never mask the classified refusal it is
    cleaning up after (codex #391 r19): on Windows a backup made
    read-only by ``copymode`` from a read-only manifest can refuse to
    unlink, which would surface a raw OSError AND leave residue the
    operator was never told about."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        return f"{path} ({type(exc).__name__}: {exc})"
    return None


def _read_bytes_or_refuse(path: Path, what: str) -> bytes:
    """Byte reads of operator-supplied paths fail as classified
    refusals (same class as the manifest read, codex #391 r4)."""
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RotationRefusal(
            f"{what} unreadable: {path} ({exc})") from exc


def _load_json(path: Path, what: str) -> Any:
    if not path.is_file():
        raise RotationRefusal(f"{what} not found: {path} — refusing "
                              "(a gate that never ran cannot authorize "
                              "a rotation)")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RotationRefusal(f"{what} unreadable: {path} ({exc})") from exc


def _git(cmd: list[str], repo: Path) -> str:
    proc = subprocess.run(cmd, cwd=repo, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RotationRefusal(
            f"{' '.join(cmd)} failed: "
            f"{proc.stderr.decode(errors='replace').strip()} — no "
            "certification state readable from the mainline, refusing")
    try:
        return proc.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        # codex #391 r5: a status artifact whose BYTES are not UTF-8 is
        # malformed certification state — the same classified freeze as
        # unparseable JSON, never an escaping traceback.
        raise RotationRefusal(
            f"{' '.join(cmd)} produced non-UTF-8 bytes ({exc}) — "
            "malformed certification state, refusing") from exc


def _validate_candidate(path: Path) -> str:
    """Strict serving-loader validation; returns the manifest sha256."""
    try:
        _members, sha = load_ensemble_manifest(path)
    except EnsembleServingError as exc:
        raise RotationRefusal(
            f"candidate manifest refused by the serving loader: "
            f"{exc}") from exc
    return sha


def cmd_plan(args: argparse.Namespace) -> int:
    # The plan step may ONLY create an inert candidate artifact — an
    # --out that aliases the live manifest would overwrite production
    # during planning, before certification/gates/backup ever ran
    # (codex #391 r2). Path.resolve() also catches symlink and
    # case-differing spellings of the same file.
    manifest_path = Path(args.manifest)
    out_path = Path(args.out)
    if _same_file(out_path, manifest_path):
        raise RotationRefusal(
            "plan --out must not alias the live manifest "
            f"({manifest_path}) — the plan step only creates an inert "
            "candidate; the swap belongs to `execute` after the gates")
    current = _read_manifest_members(manifest_path)
    new_member = {
        "pkl_path": args.new_pkl,
        "pkl_sha256": hashlib.sha256(_read_bytes_or_refuse(
            Path(args.new_pkl), "new member pkl")).hexdigest(),
        "meta_path": args.new_meta,
        "meta_sha256": hashlib.sha256(_read_bytes_or_refuse(
            Path(args.new_meta), "new member meta sidecar")).hexdigest(),
        "fit_start": args.fit_start,
        "fit_end": args.fit_end,
    }
    planned = plan_rotated_members(current, new_member)
    payload = {"schema_version": MANIFEST_SCHEMA_VERSION,
               "members": planned}
    out = out_path
    # Staging is an EXCLUSIVELY created unique file (codex #391 r22):
    # a predictable `<out>.tmp` that already exists as a symlink or
    # hardlink to the live manifest would be followed and truncated
    # here — during the step that is supposed to be inert. Directory
    # creation and the write are fallible too (missing parent,
    # ENOSPC/quota, late I/O — codex #391 r21): a partial file
    # escaping as a raw OSError would bypass the classified refusal
    # and leave an unnamed artifact for the next operator.
    import tempfile

    tmp: Path | None = None
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=out.name + ".stage.", dir=str(out.parent))
        tmp = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2))
        # The published candidate keeps mkstemp's 0600 deliberately:
        # it is an inert operator-local artifact consumed by the gate
        # runner and `execute` (both run by the same operator per the
        # runbook), and the production manifest's own mode comes from
        # the live file at install time, never from this one.
    except OSError as exc:
        residue = _remove_or_note(tmp) if tmp is not None else None
        raise RotationRefusal(
            f"cannot stage the candidate manifest for {out}: {exc}"
            + (f"; a partial staging file SURVIVES at {residue}"
               if residue else "")) from exc
    # Validate the PRIVATE tmp BEFORE publishing (adversarial self-
    # review): an illegal rotation never produces a candidate file at
    # --out — a stale invalid candidate would sit there as an
    # attractive wrong input for the next session.
    try:
        sha = _validate_candidate(tmp)
    except RotationRefusal as exc:
        # Same discipline as execute (codex #391 r19/r20): a cleanup
        # that itself fails must not mask the classified refusal —
        # keep the refusal and name any surviving temp file.
        residue = _remove_or_note(tmp)
        if residue:
            raise RotationRefusal(
                f"{exc}; additionally the staging file could not be "
                f"removed and SURVIVES at {residue}") from exc
        raise
    try:
        os.replace(tmp, out)
    except OSError as exc:
        residue = _remove_or_note(tmp)
        raise RotationRefusal(
            f"cannot publish the candidate manifest to {out}: {exc}"
            + (f"; the staging file SURVIVES at {residue}"
               if residue else "")) from exc
    print(f"[rotate] candidate manifest written: {out}")
    print(f"[rotate] candidate manifest sha256: {sha}")
    print("[rotate] next: run scripts/retrain_gate.py --scope ensemble "
          "on this candidate, then execute.")
    return 0


def cmd_execute(args: argparse.Namespace) -> int:
    repo = Path(args.repo or PROJECT_ROOT)
    manifest_path = Path(args.manifest)
    candidate_path = Path(args.candidate)

    # 1. Certification state — content-only, mainline-only.
    status_text = _git(git_show_status_cmd(), repo)
    status = parse_recert_status(status_text)
    tip_iso = _git(git_status_tip_cmd(), repo).strip()
    if not tip_iso:
        raise RotationRefusal(
            "status artifact has no mainline tip commit — cannot anchor "
            "the validity window, refusing")
    now_iso = args.now or datetime.now(tz=timezone.utc).isoformat()
    allowed, reason = recert_validity(status, tip_iso, now_iso)
    print(f"[rotate] certification: {reason}")
    if not allowed:
        raise RotationRefusal(reason)

    # 2. Candidate bytes are read EXACTLY ONCE (adversarial self-
    # review: the swap must install the same bytes the gate artifact
    # was verified against — a second read would open a TOCTOU window
    # where a concurrent `plan` run swaps the file between the digest
    # check and the write). Every downstream step — digest binding,
    # structural validation, plan integrity, the swap itself — derives
    # from THIS buffer.
    candidate_bytes = _read_bytes_or_refuse(
        candidate_path, "candidate manifest")
    candidate_sha = hashlib.sha256(candidate_bytes).hexdigest()

    # Structural validation runs on a PRIVATE staging copy of those
    # bytes (the same file that is later os.replace'd in). The staging
    # file is EXCLUSIVELY created with a unique name (codex #391 r8:
    # a predictable `.swap` path would let two concurrent executes
    # overwrite each other's staging between validation and the final
    # replace — installing bytes whose digest was never adjudicated).
    # All fallible checks happen BEFORE any production write — a
    # post-swap refusal would contradict the zero-writes contract.
    import tempfile

    fd, tmp_name = tempfile.mkstemp(
        prefix=manifest_path.name + ".swap.",
        dir=str(manifest_path.parent))
    tmp = Path(tmp_name)
    try:
        # Staging bytes are written (and the fd CLOSED) before the
        # lock: a lock-acquisition refusal must leave no open handle
        # on the temp file, or the cleanup unlink fails on Windows.
        with os.fdopen(fd, "wb") as fh:
            fh.write(candidate_bytes)
        # The exclusive manifest lock spans the ENTIRE adjudication —
        # snapshot, gate checks, backup, recheck AND the final
        # os.replace (codex #391 r13): the recheck alone left a window
        # between check and replace; under the lock a concurrent
        # execute refuses at acquisition instead of racing it.
        with _ManifestLock(manifest_path):
            # Mirror the LIVE manifest's permission bits AND (on
            # POSIX) its owner+group onto the staged file (codex #391
            # r10/r11/r14): mkstemp creates 0600 owned by the
            # EXECUTOR, and os.replace would install that identity
            # over production — an owner-only 0600 manifest owned by
            # the serving account (rotated by root) or a group-
            # readable 0640 one would lose readability even though the
            # bits look right. When ownership cannot be preserved
            # (non-root executor rotating another account's manifest),
            # fail closed. (Windows has no chown; there the staged
            # file inherits the directory ACL, so readability is not
            # narrowed by the swap.)
            try:
                shutil.copymode(manifest_path, tmp)
                if hasattr(os, "chown"):
                    live_stat = os.stat(manifest_path)
                    os.chown(tmp, live_stat.st_uid, live_stat.st_gid)
            except OSError as exc:
                raise RotationRefusal(
                    f"cannot mirror live-manifest permissions/owner/"
                    f"group onto the staged file: {exc} — the "
                    "installed manifest could lose readability for "
                    "the serving account, refusing") from exc
            try:
                staged_members, _staged_sha = load_ensemble_manifest(tmp)
            except EnsembleServingError as exc:
                raise RotationRefusal(
                    f"candidate manifest refused by the serving "
                    f"loader: {exc}") from exc

            # 3. Gate artifacts, bound to what is actually rotating.
            # The LIVE manifest is snapshot ONCE (codex #391 r12): the
            # same bytes serve as plan-integrity baseline and as the
            # backup payload, and the live digest is re-checked before
            # the swap — under the lock this closes executor-vs-
            # executor races entirely, and the recheck still catches
            # out-of-band manual edits made before the lock was taken.
            live_bytes = _read_bytes_or_refuse(manifest_path,
                                               "live manifest")
            live_sha = hashlib.sha256(live_bytes).hexdigest()
            current = _members_from_bytes(live_bytes,
                                          str(manifest_path))
            candidate = json.loads(
                candidate_bytes.decode("utf-8"))["members"]
            new_member = candidate[-1]
            member_gate = _load_json(Path(args.member_gate),
                                     "member gate artifact")
            check_gate_artifact(
                member_gate, scope=SCOPE_MEMBER,
                expected_subject_sha=str(new_member.get("pkl_sha256")),
                # The trainer-integrity gate judged the SIDECAR — bind
                # it too, or a regenerated sidecar under the same
                # pickle would ride an old artifact into production.
                expected_meta_sha=str(new_member.get("meta_sha256")),
                # Serving derives the inference normalization window
                # from the newest member's dates (codex #391 r12) —
                # the gate must have evaluated the SAME window the
                # candidate installs.
                expected_fit_window=(str(new_member.get("fit_start")),
                                     str(new_member.get("fit_end"))),
                # ...and the gates must have been MEASURED on this
                # member's valid window, recently (codex #391 r19).
                member_fit_end=str(new_member.get("fit_end")),
                now_iso=now_iso)
            ensemble_gate = _load_json(Path(args.ensemble_gate),
                                       "ensemble gate artifact")
            check_gate_artifact(ensemble_gate, scope=SCOPE_ENSEMBLE,
                                expected_subject_sha=candidate_sha,
                                now_iso=now_iso)

            # 4. Plan integrity: candidate == current[1:] + [gated
            # member].
            expected = plan_rotated_members(current, new_member)
            if candidate != expected:
                raise RotationRefusal(
                    "candidate manifest does not equal the rotation "
                    "plan (current minus oldest plus the gated "
                    "member) — refusing")

            # 5. Member-chain re-validation at EXECUTE time (codex
            # #391 r9): the gate artifacts prove the members were
            # valid when the gates RAN — a pkl/sidecar deleted or
            # replaced since then would make production serving refuse
            # the freshly installed manifest at the next morning run.
            # Re-run the STRICT serving loader (existence + digest
            # chain + sidecar version guard + unpickle + .predict)
            # over the staged members so what we install is what
            # serving will accept.
            try:
                load_member_models(staged_members)
            except EnsembleServingError as exc:
                raise RotationRefusal(
                    f"member chain re-validation failed: {exc} — the "
                    "rotated manifest would be refused by serving; "
                    "not installing") from exc

            # 6. Backup — writes the SNAPSHOT bytes the whole
            # adjudication ran against (single-read discipline), then
            # the live digest is re-checked as the last fallible step
            # before the swap.
            stamp = datetime.now(tz=timezone.utc).strftime(
                "%Y%m%dT%H%M%SZ")
            backup = manifest_path.with_name(
                manifest_path.name + f".pre_rotation_{stamp}")
            # The backup is born 0600 via an exclusive descriptor
            # (codex #391 r16): Path.write_bytes would create it at
            # 0666&umask for a window before copymode/chown runs — a
            # privileged rotation of a restricted manifest must never
            # expose the rollback bytes, even transiently. O_EXCL also
            # makes the already-exists check race-free.
            try:
                bfd = os.open(str(backup),
                              os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                              0o600)
            except OSError as exc:
                raise RotationRefusal(
                    f"cannot create backup {backup}: {exc}") from exc
            # The write itself is fallible too (ENOSPC/quota/NFS,
            # codex #391 r17) — a failed backup write unlinks the
            # partial rollback file and refuses through the
            # classified path, zero residue.
            try:
                with os.fdopen(bfd, "wb") as bfh:
                    bfh.write(live_bytes)
            except OSError as exc:
                residue = _remove_or_note(backup)
                raise RotationRefusal(
                    f"backup write failed: {backup} ({exc}) — "
                    + (f"the partial rollback file SURVIVES at "
                       f"{residue} and must be removed by hand"
                       if residue else
                       "partial rollback file removed")
                    + ", refusing") from exc
            try:
                # The backup IS the advertised single-step rollback
                # artifact (codex #391 r15): mirror the live
                # manifest's mode/owner/group onto it too, or a
                # privileged rotation of a 0600/0640 serving-owned
                # manifest would leave a broader-readable or
                # wrong-owned rollback file — and restoring it would
                # reinstall those wrong permissions.
                try:
                    shutil.copymode(manifest_path, backup)
                    if hasattr(os, "chown"):
                        bstat = os.stat(manifest_path)
                        os.chown(backup, bstat.st_uid, bstat.st_gid)
                except OSError as exc:
                    raise RotationRefusal(
                        f"cannot mirror live-manifest permissions/"
                        f"owner/group onto the backup: {exc} — the "
                        "rollback artifact would carry wrong "
                        "permissions, refusing") from exc
                recheck = _read_bytes_or_refuse(
                    manifest_path, "live manifest (pre-swap recheck)")
                if hashlib.sha256(recheck).hexdigest() != live_sha:
                    raise RotationRefusal(
                        "live manifest changed since this execution "
                        "adjudicated it (out-of-band manual edit) — "
                        "refusing to clobber the intervening update; "
                        "re-run against the current state")
            except BaseException as exc:
                # A refusal after the backup write must not leave a
                # stray backup either — refusal means zero residue. If
                # the cleanup ITSELF fails (read-only backup on
                # Windows, codex #391 r19) the classified refusal
                # still stands and names the surviving file.
                residue = _remove_or_note(backup)
                if residue and isinstance(exc, RotationRefusal):
                    raise RotationRefusal(
                        f"{exc}; additionally the pre-rotation backup "
                        f"could not be removed and SURVIVES at "
                        f"{residue}") from exc
                raise

            # 7. Atomic install of the VERIFIED buffer, still under
            # the lock (codex #391 r13). The replace is atomic — it
            # either happened or it did not — but it can still FAIL
            # (Windows/NFS lock, permission drift, I/O; codex #391
            # r18). A failure means the manifest was NOT rotated, so
            # the just-written backup is meaningless residue: remove
            # it and refuse through the classified path rather than
            # letting a raw OSError escape with a stray
            # `.pre_rotation_*` file implying a rotation happened.
            try:
                os.replace(tmp, manifest_path)
            except OSError as exc:
                # The cleanup may itself fail (read-only backup on
                # Windows, codex #391 r19) — the refusal stays
                # classified either way and says which files remain.
                residue = _remove_or_note(backup)
                raise RotationRefusal(
                    f"manifest install failed: {exc} — the live "
                    f"manifest is UNCHANGED (no rotation occurred); "
                    + (f"the pre-rotation backup could not be removed "
                       f"and SURVIVES at {residue} — remove it by hand "
                       "before re-running"
                       if residue else
                       "the pre-rotation backup was removed")
                    + "; resolve the filesystem condition and re-run"
                ) from exc
    except BaseException as exc:
        residue = _remove_or_note(tmp)
        if residue and isinstance(exc, RotationRefusal):
            raise RotationRefusal(
                f"{exc}; additionally the staging file could not be "
                f"removed and SURVIVES at {residue}") from exc
        raise
    print(f"[rotate] pre-rotation backup: {backup}")
    print(f"[rotate] manifest rotated: {manifest_path}")
    print(f"[rotate] manifest sha256: {candidate_sha}")
    print("[rotate] rollback (single step): restore the backup file "
          "over the manifest path.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="Build the candidate manifest.")
    plan.add_argument("--manifest", required=True,
                      help="Current production manifest.")
    plan.add_argument("--new-pkl", required=True)
    plan.add_argument("--new-meta", required=True)
    plan.add_argument("--fit-start", required=True)
    plan.add_argument("--fit-end", required=True)
    plan.add_argument("--out", required=True,
                      help="Candidate manifest output path.")

    execute = sub.add_parser("execute", help="Perform the rotation.")
    execute.add_argument("--manifest", required=True,
                         help="Current production manifest (the file "
                              "that will be swapped).")
    execute.add_argument("--candidate", required=True,
                         help="Candidate manifest from `plan`.")
    execute.add_argument("--member-gate", required=True,
                         help="Member-scope gate artifact (PASS).")
    execute.add_argument("--ensemble-gate", required=True,
                         help="Ensemble-scope gate artifact (PASS).")
    execute.add_argument("--repo", default=None,
                         help="Repo root for git reads (default: this "
                              "checkout).")
    execute.add_argument("--now", default=None,
                         help="Injectable evaluation instant (ISO, "
                              "tz-aware) — tests only.")

    args = p.parse_args(argv)
    try:
        if args.command == "plan":
            return cmd_plan(args)
        return cmd_execute(args)
    except RotationRefusal as exc:
        print(f"[rotate] REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
