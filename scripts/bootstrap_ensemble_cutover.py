#!/usr/bin/env python3
"""First production switch to the certified N5 ensemble — the PR-C'
BOOTSTRAP CUTOVER of 2026-07-20-csi800-n5-production-promotion.

This is the PROMOTION path (codex #389 r8), not the quarterly
maintenance path, so it runs the FULL promotion gate set before it
writes anything:

  1. campaign eligibility  — the committed verdict sidecar passes
     ``csi800_campaign_certify.py --verify`` AND grants
     ``promotion_eligible``;
  2. iso_week anchor       — the committed re-check evidence, read
     from the mainline at ONE pinned revision, binds the committed
     iso_week preset (semantic-key equality + content digest) and
     re-derives a POSITIVE full-window net excess;
  3. bootstrap gates       — three member-scope gate artifacts (one
     per staggered member, R1-DP-C) plus one ensemble-scope artifact,
     each PASS and bound to what it gated. Member windows are NOT
     recency-bound here: the bootstrap members are staggered into the
     past by protocol (T-6m/T-3m/T);
  4. serving validity      — the manifest passes the STRICT serving
     loader and every member's chain loads (what we install is what
     serving will accept).

Only then does it write, in order: the incumbent backup, the three
members' inference metas, the serving manifest, the baseline record,
and the INITIAL certification-status artifact (its first write ever —
R1-DP-D; writing it earlier would start the 15-month clock before
production actually switched).

Any gate failure = zero production writes, classified refusal, and
the incumbent keeps serving. There is no "keep the old ensemble"
branch: at bootstrap there is no old ensemble (R1-DP-C).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.bootstrap_cutover_lib import (  # noqa: E402
    BOOTSTRAP_MEMBER_COUNT,
    CutoverRefusal,
    build_baseline_record,
    build_inference_meta,
    build_initial_status,
    check_campaign_eligibility,
    check_cutover_paths,
    check_evidence_provenance,
    check_isoweek_anchor,
    check_preregistered_windows,
    check_write_targets,
)
from scripts.retrain_gate_lib import (  # noqa: E402
    SCOPE_ENSEMBLE,
    SCOPE_MEMBER,
)
from scripts.rotation_lib import (  # noqa: E402
    RECERT_STATUS_PATH,
    RotationRefusal,
    check_gate_artifact,
)
from src.inference.ensemble_serving import (  # noqa: E402
    EnsembleServingError,
    load_ensemble_manifest,
    load_member_models,
)

VERDICT_SIDECAR_PATH = "docs/research/csi800_cadence_verdict.json"
ISOWEEK_EVIDENCE_DIR = (
    "docs/research/evidence/csi800_n5_runs/"
    "csi800_cadence5_conservative_isoweek")
ISOWEEK_PRESET_PATH = (
    "config/presets/csi800_cadence5_conservative_isoweek.yaml")
# The PRE-REGISTERED bootstrap trio (R1-DP-C, windows frozen before
# ignition). Read at the pinned mainline revision: a locally edited
# preset must not be able to authorize a differently-windowed trio.
BOOTSTRAP_PRESET_PATHS = (
    "config/presets/csi800_n5_bootstrap_m1.yaml",
    "config/presets/csi800_n5_bootstrap_m2.yaml",
    "config/presets/csi800_n5_bootstrap_m3.yaml",
)
BASELINE_PATH = "docs/promotion/csi800_n5_bootstrap_baseline.json"
_MAINLINE = "origin/main"

# The semantic keys the anchored re-check run's embedded config must
# match against the committed preset — the same set the two-level
# binding chain governs (tests/governance/
# test_csi800_n5_production_serving.py).
_BINDING_KEYS = (
    "instruments", "benchmark_code", "attribution_sleeve_grouping",
    "risk_constraints_enabled", "risk_constraints_calibration",
    "slippage_bps", "rebalance_cadence_days", "rebalance_phase",
    "rebalance_anchor", "risk_constraint_scope", "topk",
    "train_months", "valid_months", "step_months",
)


def _git(cmd: list[str], repo: Path) -> bytes:
    proc = subprocess.run(cmd, cwd=repo, capture_output=True, check=False)
    if proc.returncode != 0:
        raise CutoverRefusal(
            f"{' '.join(cmd)} failed: "
            f"{proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def _resolve_mainline(repo: Path) -> str:
    rev = _git(["git", "rev-parse", f"{_MAINLINE}^{{commit}}"],
               repo).decode("utf-8", errors="replace").strip()
    if not rev:
        raise CutoverRefusal(
            "cannot resolve the mainline to a commit — no anchored "
            "evidence readable, refusing")
    return rev


def _show(repo: Path, rev: str, relpath: str) -> bytes:
    return _git(["git", "show", f"{rev}:{relpath}"], repo)


def _binding_subset(config: Any, what: str) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise CutoverRefusal(f"{what} config block is not an object")
    missing = [k for k in _BINDING_KEYS if k not in config]
    if missing:
        raise CutoverRefusal(
            f"{what} is missing binding keys {missing} — cannot bind "
            "the certified serving semantics, refusing")
    return {k: config[k] for k in _BINDING_KEYS}


def _canonical_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()


def _read_bytes_or_refuse(path: Path, what: str) -> bytes:
    """Operator-supplied reads fail as CLASSIFIED refusals — a typo'd
    path must surface as `[cutover] REFUSED`, never a raw traceback
    out of the zero-write path (the PR-B' discipline)."""
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CutoverRefusal(
            f"{what} unreadable: {path} ({exc})") from exc


def _load_json(path: Path, what: str) -> Any:
    if not path.is_file():
        raise CutoverRefusal(f"{what} not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CutoverRefusal(f"{what} unreadable: {path} ({exc})") from exc


def _certify_verify(repo: Path) -> None:
    """Gate 1a: the sidecar's own ``--verify`` (anchor + byte
    re-validation) must pass in THIS repo."""
    proc = subprocess.run(
        [sys.executable, "scripts/research/csi800_campaign_certify.py",
         "--verify", VERDICT_SIDECAR_PATH],
        cwd=repo, capture_output=True, check=False)
    if proc.returncode != 0:
        raise CutoverRefusal(
            "campaign certify --verify FAILED: "
            f"{proc.stdout.decode(errors='replace').strip()} "
            f"{proc.stderr.decode(errors='replace').strip()}")


def _gate_promotion(args: argparse.Namespace, repo: Path,
                    now_iso: str) -> dict[str, Any]:
    """Run every promotion gate; return the evidence block for the
    baseline record. Raises :class:`CutoverRefusal` on any failure —
    before a single production byte is written."""
    # ── 0. path preconditions (adversarial self-review) ─────────
    # Adjudicated WITH the gates so --dry-run covers them and a
    # refusal stays zero-write.
    status_path = repo / RECERT_STATUS_PATH
    check_cutover_paths(
        incumbent_exists=Path(args.incumbent).is_file(),
        manifest_out_exists=Path(args.manifest_out).exists(),
        status_exists=status_path.exists(),
        incumbent=str(args.incumbent),
        manifest_out=str(args.manifest_out),
        status_path=str(status_path))

    # ── 1. campaign eligibility, at ONE pinned mainline revision ──
    # The sidecar bytes adjudicated here MUST be the same bytes
    # `--verify` validated: certify reads the sidecar THROUGH the
    # mainline anchor on purpose ("an unmerged local sidecar must
    # never verify as a promotion verdict"), so reading the working
    # tree here would adjudicate — and freeze into the 15-month
    # status artifact — bytes nobody verified (adversarial
    # self-review).
    rev = _resolve_mainline(repo)
    _certify_verify(repo)
    # A fetch landing between certify's own resolution and ours would
    # split the two reads; re-resolve and refuse on movement.
    if _resolve_mainline(repo) != rev:
        raise CutoverRefusal(
            "the mainline moved while the campaign verification ran — "
            "re-run so every gate reads ONE revision")
    sidecar_bytes = _show(repo, rev, VERDICT_SIDECAR_PATH)
    sidecar = check_campaign_eligibility(
        sidecar_bytes.decode("utf-8"))
    campaign = {
        "verdict_sidecar_path": VERDICT_SIDECAR_PATH,
        "read_at_rev": rev,
        "verdict_sidecar_sha256": hashlib.sha256(
            sidecar_bytes).hexdigest(),
        "evidence_anchor_commit": sidecar["anchors"]["evidence_anchor"],
        "conservative_net_annualized":
            sidecar["verdict"]["conservative_net_annualized"],
        "gross_retention": sidecar["verdict"].get("gross_retention"),
    }

    # ── 2. iso_week anchor (SAME pinned revision) ───────────────
    aggregate = json.loads(_show(
        repo, rev,
        f"{ISOWEEK_EVIDENCE_DIR}/walk_forward_report.json"
    ).decode("utf-8"))
    check_evidence_provenance(aggregate)
    preset = yaml.safe_load(
        _show(repo, rev, ISOWEEK_PRESET_PATH).decode("utf-8"))
    base = yaml.safe_load(
        _show(repo, rev, "config_walk.yaml").decode("utf-8"))
    resolved = {**(base or {}), **(preset or {})}
    run_subset = _binding_subset(aggregate.get("config"),
                                 "iso_week re-check run")
    preset_subset = _binding_subset(resolved,
                                    "committed iso_week preset")
    isoweek = check_isoweek_anchor(
        aggregate,
        expected_config_sha256=_canonical_digest(preset_subset),
        actual_config_sha256=_canonical_digest(run_subset))
    isoweek.update({
        "evidence_dir": ISOWEEK_EVIDENCE_DIR,
        "rev": rev,
        "preset_path": ISOWEEK_PRESET_PATH,
        "config_binding_sha256": _canonical_digest(preset_subset),
    })

    # ── 3. bootstrap gate artifacts ─────────────────────────────
    manifest_path = Path(args.manifest)
    manifest_bytes = _read_bytes_or_refuse(
        manifest_path, "candidate manifest")
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    try:
        members, loader_sha = load_ensemble_manifest(manifest_path)
    except EnsembleServingError as exc:
        raise CutoverRefusal(
            f"candidate manifest refused by the serving loader: "
            f"{exc}") from exc
    if loader_sha != manifest_sha:
        raise CutoverRefusal(
            "manifest digest disagreement between the loader and this "
            "executor — refusing")
    if len(args.member_gate) != BOOTSTRAP_MEMBER_COUNT:
        raise CutoverRefusal(
            f"expected {BOOTSTRAP_MEMBER_COUNT} --member-gate "
            f"artifacts (oldest->newest), got "
            f"{len(args.member_gate)}")
    gate_paths: dict[str, str] = {}
    try:
        for i, (member, gate_path) in enumerate(
                zip(members, args.member_gate, strict=True)):
            artifact = _load_json(Path(gate_path),
                                  f"member[{i}] gate artifact")
            check_gate_artifact(
                artifact, scope=SCOPE_MEMBER,
                expected_subject_sha=member.pkl_sha256,
                expected_meta_sha=member.meta_sha256,
                expected_fit_window=(member.fit_start, member.fit_end),
                member_fit_end=member.fit_end,
                now_iso=now_iso,
                # R1-DP-C: the bootstrap members are staggered into
                # the past ON PURPOSE (T-6m/T-3m/T), so the
                # maintenance path's recency bound does not apply to
                # them. Every other binding still does.
                enforce_recency=False)
            gate_paths[f"member[{i}]"] = str(gate_path)
        ensemble_artifact = _load_json(Path(args.ensemble_gate),
                                       "ensemble gate artifact")
        check_gate_artifact(
            ensemble_artifact, scope=SCOPE_ENSEMBLE,
            expected_subject_sha=manifest_sha,
            # The trailing-quarter dry run DOES have to describe the
            # present, so its recency bound stays.
            now_iso=now_iso)
        gate_paths["ensemble"] = str(args.ensemble_gate)
    except RotationRefusal as exc:
        raise CutoverRefusal(f"bootstrap gate refused: {exc}") from exc

    # ── 3a. the trio must be the PRE-REGISTERED one (codex #392 r6)
    preset_windows: list[tuple[str, str]] = []
    for preset_path in BOOTSTRAP_PRESET_PATHS:
        cfg = yaml.safe_load(
            _show(repo, rev, preset_path).decode("utf-8"))
        if not isinstance(cfg, dict):
            raise CutoverRefusal(
                f"{preset_path} at {rev[:12]} is not a mapping")
        try:
            preset_windows.append(
                (str(cfg["train_start"]), str(cfg["train_end"])))
        except KeyError as exc:
            raise CutoverRefusal(
                f"{preset_path} declares no {exc} — cannot bind the "
                "pre-registered windows") from exc
    check_preregistered_windows(
        [(m.fit_start, m.fit_end) for m in members], preset_windows)

    # ── 3b. every write target must own its path (codex #392 r4) ─
    targets = {
        "manifest_out": str(Path(args.manifest_out).resolve()),
        "status_artifact": str(status_path.resolve()),
        "baseline": str((repo / BASELINE_PATH).resolve()),
        "incumbent": str(Path(args.incumbent).resolve()),
    }
    for i, member in enumerate(members):
        targets[f"member[{i}] inference meta"] = str(
            Path(member.pkl_path).with_suffix(".meta.json").resolve())
        targets[f"member[{i}] pkl"] = str(
            Path(member.pkl_path).resolve())
        # The TRAINER sidecar is read and hash-validated, never
        # written — including it here means nothing we write may land
        # on it (codex #392 r5: the `model.pkl.meta.json` vs
        # `model.meta.json` confusion would otherwise let the
        # inference-meta write clobber a validated sidecar and break
        # the manifest's meta chain on the next serving load).
        targets[f"member[{i}] trainer sidecar"] = str(
            Path(member.meta_path).resolve())
    check_write_targets(targets)

    # ── 4. serving validity of what we are about to install ─────
    try:
        load_member_models(members)
    except EnsembleServingError as exc:
        raise CutoverRefusal(
            f"member chain validation failed: {exc} — the manifest "
            "would be refused by serving; not switching") from exc

    return {
        "campaign": campaign,
        "isoweek": isoweek,
        "gate_artifacts": gate_paths,
        "members": members,
        "manifest_sha256": manifest_sha,
        "manifest_bytes": manifest_bytes,
    }


def _backup_incumbent(incumbent: Path, stamp: str) -> dict[str, str]:
    """DP-4 rollback kit: copy the incumbent canonical pkl and its
    metas beside themselves, timestamped. Missing metas are recorded
    honestly rather than fabricated."""
    record: dict[str, str] = {}
    targets = [incumbent,
               incumbent.with_suffix(".meta.json"),
               incumbent.with_name(incumbent.name + ".meta.json")]
    for src in targets:
        if not src.is_file():
            record[src.name] = "absent"
            continue
        dst = src.with_name(src.name + f".pre_bootstrap_{stamp}")
        if dst.exists():
            raise CutoverRefusal(f"backup path already exists: {dst}")
        shutil.copy2(src, dst)
        record[src.name] = str(dst)
    return record


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", required=True,
                   help="Candidate serving manifest (from the "
                        "bootstrap plan) — installed on success.")
    p.add_argument("--member-gate", action="append", default=[],
                   help="Member-scope gate artifact, repeated ONCE "
                        "PER MEMBER in manifest order (oldest->newest).")
    p.add_argument("--ensemble-gate", required=True)
    p.add_argument("--incumbent", required=True,
                   help="Incumbent canonical pkl (backed up before the "
                        "switch).")
    p.add_argument("--manifest-out", required=True,
                   help="Production serving manifest path to CREATE.")
    p.add_argument("--repo", default=None)
    p.add_argument("--now", default=None,
                   help="Injectable instant (ISO, tz-aware) — tests.")
    p.add_argument("--dry-run", action="store_true",
                   help="Run every gate and report, writing NOTHING.")
    args = p.parse_args(argv)

    repo = Path(args.repo or PROJECT_ROOT)
    now_iso = args.now or datetime.now(tz=timezone.utc).isoformat()
    try:
        evidence = _gate_promotion(args, repo, now_iso)
    except (CutoverRefusal, RotationRefusal) as exc:
        print(f"[cutover] REFUSED: {exc}", file=sys.stderr)
        return 1

    print("[cutover] all promotion gates PASS")
    print(f"[cutover]   campaign net "
          f"{evidence['campaign']['conservative_net_annualized']:.4%}, "
          f"retention {evidence['campaign']['gross_retention']}")
    print(f"[cutover]   iso_week anchor net "
          f"{evidence['isoweek']['net_annualized']:.4%} "
          f"({evidence['isoweek']['num_folds']} folds, "
          f"rev {evidence['isoweek']['rev'][:12]})")
    if args.dry_run:
        print("[cutover] --dry-run: no production writes performed.")
        return 0

    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    members = evidence["members"]
    try:
        backup = _backup_incumbent(Path(args.incumbent), stamp)
        member_records: list[dict[str, Any]] = []
        for member in members:
            meta_path = Path(member.pkl_path).with_suffix(".meta.json")
            meta = build_inference_meta(
                model_path=member.pkl_path,
                fit_start=member.fit_start, fit_end=member.fit_end,
                model_type="LGBModel", promoted_at=now_iso)
            meta_path.write_text(
                json.dumps(meta, indent=2, ensure_ascii=False),
                encoding="utf-8")
            member_records.append({
                "pkl_path": member.pkl_path,
                "pkl_sha256": member.pkl_sha256,
                "inference_meta_path": str(meta_path),
                "fit_start": member.fit_start,
                "fit_end": member.fit_end,
            })
        out = Path(args.manifest_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        # EXCLUSIVE unique staging (codex #392 r1, the rotation
        # executor's pattern): a predictable `<manifest>.install` that
        # already exists as a symlink/hardlink would be followed and
        # truncated before the replace.
        import tempfile

        fd, tmp_name = tempfile.mkstemp(
            prefix=out.name + ".install.", dir=str(out.parent))
        tmp = Path(tmp_name)
        try:
            # The staging WRITE is fallible too (ENOSPC/quota/handle
            # errors, codex #392 r4) — its own failure must not leave
            # an `.install.*` file in the production directory.
            with os.fdopen(fd, "wb") as fh:
                fh.write(evidence["manifest_bytes"])
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
        try:
            # mkstemp creates 0600 owned by the EXECUTOR. The
            # bootstrap CREATES the production manifest, so there is
            # no live file to mirror (the rotation executor's case) —
            # the closest true statement of "what the serving account
            # can read" is the INCUMBENT canonical, the artifact
            # production reads today. Mirror its mode, and its
            # owner/group on POSIX; a failure to preserve ownership
            # refuses rather than installing a manifest the morning
            # run cannot open (codex #392 r2).
            incumbent_stat = os.stat(args.incumbent)
            manifest_mode = stat.S_IMODE(incumbent_stat.st_mode)
            os.chmod(tmp, manifest_mode)
            if hasattr(os, "chown"):
                os.chown(tmp, incumbent_stat.st_uid,
                         incumbent_stat.st_gid)
            os.replace(tmp, out)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise CutoverRefusal(
                f"cannot install the production manifest with the "
                f"incumbent's readability ({exc}) — the morning run "
                "could not open it; nothing installed") from exc

        baseline = build_baseline_record(
            manifest_path=str(out),
            manifest_mode=oct(manifest_mode),
            manifest_sha256=evidence["manifest_sha256"],
            members=member_records, incumbent_backup=backup,
            campaign=evidence["campaign"], isoweek=evidence["isoweek"],
            gate_artifacts=evidence["gate_artifacts"],
            generated_at=now_iso)
        baseline_path = repo / BASELINE_PATH
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(baseline, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")

        status = build_initial_status(
            verdict_sidecar_path=VERDICT_SIDECAR_PATH,
            verdict_sidecar_sha256=evidence["campaign"][
                "verdict_sidecar_sha256"],
            evidence_anchor_commit=evidence["campaign"][
                "evidence_anchor_commit"],
            note=("initial bootstrap WIN — first production switch to "
                  "the certified csi800 N5 quarterly-retrain ensemble "
                  f"(3 staggered members, manifest "
                  f"{evidence['manifest_sha256'][:12]})"))
        # Existence was adjudicated in the gate phase; the WRITE
        # stays last so the 15-month validity clock only starts once
        # production has actually switched (R1-DP-D).
        status_path = repo / RECERT_STATUS_PATH
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(
            json.dumps(status, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
    except (CutoverRefusal, OSError) as exc:
        print(f"[cutover] WRITE FAILED after gates passed: {exc}",
              file=sys.stderr)
        print("[cutover] restore the incumbent from the backup above "
              "and re-run once resolved.", file=sys.stderr)
        return 1

    print(f"[cutover] incumbent backup: {backup}")
    print(f"[cutover] serving manifest installed: {out}")
    print(f"[cutover] baseline record: {baseline_path}")
    print(f"[cutover] initial status artifact: {status_path}")
    print("[cutover] NEXT: commit the baseline + status artifacts, then "
          "switch the morning run to "
          "`--ensemble-manifest <manifest>`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
