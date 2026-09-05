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
    BOOTSTRAP_PRESET_PATHS,
    CutoverRefusal,
    _canonicalize_provider_uri,
    _expand_registered_default,
    build_baseline_record,
    build_inference_meta,
    build_initial_status,
    check_campaign_eligibility,
    check_cutover_paths,
    check_evidence_provenance,
    check_isoweek_anchor,
    check_member_source_provenance,
    check_member_training_config,
    check_preregistered_gate_windows,
    check_preregistered_windows,
    check_run_config_provenance,
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
# The PRE-REGISTERED ensemble dry-run window (trailing quarter, fully
# out of sample for all three members: m3's training ends 2026-04-01).
# Frozen here with the presets and pinned by governance — the runbook
# quotes the same pair. Re-anchored with the trio (RA-DP-2 of
# 2026-08-04-csi800-n5-bootstrap-reanchor). The end sits one session
# BEFORE the bundle tail 2026-08-03: the qlib backtest needs a T+1
# settlement session after the window end — v1's windows were pinned
# to the tail itself and were physically unbacktestable (codex #393
# r1).
BOOTSTRAP_DRYRUN_WINDOW = ("2026-05-06", "2026-07-31")
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


def _member_run_config(pkl_path: Path) -> tuple[Any, str] | None:
    """Compatibility adapter for the shared exact-layout producer reader."""
    from src.data.model_training_provenance import read_member_run_config

    return read_member_run_config(pkl_path)


# `--now` exists for deterministic tests. In a real promotion the
# injected instant must sit NEAR the true present, or it becomes an
# evidence-recency bypass (codex #392 r15): pinning it beside the
# frozen dry-run window would admit stale gate artifacts years later.
_MAX_NOW_SKEW_SECONDS = 24 * 3600


def _validate_injected_now(now_iso: str, wall_now: datetime) -> None:
    try:
        injected = datetime.fromisoformat(now_iso)
    except ValueError as exc:
        raise CutoverRefusal(
            f"--now is not ISO-8601: {now_iso!r}") from exc
    if injected.tzinfo is None:
        raise CutoverRefusal(
            f"--now must be timezone-aware: {now_iso!r}")
    skew = abs((injected - wall_now).total_seconds())
    if skew > _MAX_NOW_SKEW_SECONDS:
        raise CutoverRefusal(
            f"--now {now_iso} deviates {skew / 3600.0:.1f}h from the "
            "wall clock — the injected instant exists for test "
            "determinism, not evidence time travel; the recency gates "
            "must measure the true present. Refusing.")


def _require_registered_commit(repo: Path, commit: str, rev: str,
                               label: str) -> None:
    """The member's training code must be REGISTERED history (codex
    #392 r15): an ancestor of (or equal to) the pinned mainline
    revision every other gate reads. A clean tree at an unmerged,
    rewritten, or unknown commit is still unregistered implementation
    semantics — refuse."""
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, rev],
        cwd=repo, capture_output=True, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr.decode(errors="replace").strip()
                  or "not in mainline history")
        raise CutoverRefusal(
            f"{label}: training commit {commit[:12]} is not an "
            f"ancestor of the pinned mainline {rev[:12]} ({detail}) — "
            "the member was trained from unregistered source, "
            "refusing")


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


def _load_json_with_digest(path: Path, what: str) -> tuple[Any, str]:
    """Single read: the parsed artifact and the digest come from the
    SAME bytes, so what the baseline attests (codex #392 r13) is
    byte-identical to what the gate check adjudicated."""
    if not path.is_file():
        raise CutoverRefusal(f"{what} not found: {path}")
    data = _read_bytes_or_refuse(path, what)
    try:
        return (json.loads(data.decode("utf-8")),
                hashlib.sha256(data).hexdigest())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CutoverRefusal(f"{what} unreadable: {path} ({exc})") from exc


def _certify_verify(repo: Path, rev: str) -> None:
    """Gate 1a: the sidecar's own ``--verify`` (anchor + byte
    re-validation) — executed from a PINNED-REVISION worktree.

    The verifier owns the recomputation logic and thresholds (codex
    #392 r22): a locally modified working-tree copy could return
    success under semantics that do not exist at ``rev``, while every
    OTHER gate reads mainline bytes. A temporary detached worktree at
    ``rev`` shares the repo's object store and remotes, so the
    verifier's own mainline-anchored reads resolve identically — but
    the CODE that runs is the registered code."""
    import tempfile

    wt = Path(tempfile.mkdtemp(prefix="cutover_certify_"))
    try:
        _git(["git", "worktree", "add", "--detach", str(wt), rev],
             repo)
        proc = subprocess.run(
            [sys.executable,
             "scripts/research/csi800_campaign_certify.py",
             "--verify", VERDICT_SIDECAR_PATH],
            cwd=wt, capture_output=True, check=False)
        if proc.returncode != 0:
            raise CutoverRefusal(
                f"campaign certify --verify FAILED (pinned worktree "
                f"{rev[:12]}): "
                f"{proc.stdout.decode(errors='replace').strip()} "
                f"{proc.stderr.decode(errors='replace').strip()}")
    finally:
        # Best-effort cleanup — a refusal above must surface, not a
        # cleanup hiccup. `worktree remove` unregisters and deletes;
        # `prune` sweeps anything a failed remove left registered.
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt)],
            cwd=repo, capture_output=True, check=False)
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=repo, capture_output=True, check=False)


def _gate_promotion(args: argparse.Namespace, repo: Path,
                    now_iso: str) -> dict[str, Any]:
    """Run every promotion gate; return the evidence block for the
    baseline record. Raises :class:`CutoverRefusal` on any failure —
    before a single production byte is written."""
    # ── 0. path preconditions (adversarial self-review) ─────────
    # Adjudicated WITH the gates so --dry-run covers them and a
    # refusal stays zero-write.
    if args.now:
        _validate_injected_now(now_iso,
                               datetime.now(tz=timezone.utc))
    status_path = repo / RECERT_STATUS_PATH
    baseline_target = repo / BASELINE_PATH
    check_cutover_paths(
        incumbent_exists=Path(args.incumbent).is_file(),
        manifest_out_exists=Path(args.manifest_out).exists(),
        status_exists=status_path.exists(),
        baseline_exists=baseline_target.exists(),
        incumbent=str(args.incumbent),
        manifest_out=str(args.manifest_out),
        status_path=str(status_path),
        baseline=str(baseline_target))
    # The manifest DIRECTORY must be pre-provisioned (codex #392 r10):
    # creating it here under a restrictive umask (e.g. 077) would
    # yield operator-owned 0700 directories — mirroring the file mode
    # cannot restore traversal for the serving account.
    out_parent = Path(args.manifest_out).parent
    if not out_parent.is_dir():
        raise CutoverRefusal(
            f"the production manifest directory does not exist: "
            f"{out_parent} — pre-provision it with the serving "
            "account's access (the cutover will not create it with "
            "umask-dependent permissions)")

    # ── 1. campaign eligibility, at ONE pinned mainline revision ──
    # The sidecar bytes adjudicated here MUST be the same bytes
    # `--verify` validated: certify reads the sidecar THROUGH the
    # mainline anchor on purpose ("an unmerged local sidecar must
    # never verify as a promotion verdict"), so reading the working
    # tree here would adjudicate — and freeze into the 15-month
    # status artifact — bytes nobody verified (adversarial
    # self-review).
    rev = _resolve_mainline(repo)
    _certify_verify(repo, rev)
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
    gate_records: dict[str, dict[str, str]] = {}
    member_gate_windows: list[tuple[Any, Any]] = []
    try:
        for i, (member, gate_path) in enumerate(
                zip(members, args.member_gate, strict=True)):
            artifact, gate_sha = _load_json_with_digest(
                Path(gate_path), f"member[{i}] gate artifact")
            window = artifact.get("window") or {}
            member_gate_windows.append(
                (window.get("valid_start"), window.get("valid_end")))
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
            # Path AND content digest (codex #392 r13): the baseline
            # must bind the exact bytes that authorized production —
            # the local gate file is mutable after the cutover.
            gate_records[f"member[{i}]"] = {
                "path": str(gate_path), "sha256": gate_sha}
        ensemble_artifact, ensemble_gate_sha = _load_json_with_digest(
            Path(args.ensemble_gate), "ensemble gate artifact")
        check_gate_artifact(
            ensemble_artifact, scope=SCOPE_ENSEMBLE,
            expected_subject_sha=manifest_sha,
            # The trailing-quarter dry run DOES have to describe the
            # present, so its recency bound stays.
            now_iso=now_iso)
        gate_records["ensemble"] = {
            "path": str(args.ensemble_gate), "sha256": ensemble_gate_sha}
    except RotationRefusal as exc:
        raise CutoverRefusal(f"bootstrap gate refused: {exc}") from exc
    # ── 3a. the trio must be the PRE-REGISTERED one (codex #392 r6)
    preset_windows: list[tuple[str, str]] = []
    preset_valid_windows: list[tuple[str, str]] = []
    preset_declared: list[dict[str, Any]] = []
    base_config = yaml.safe_load(
        _show(repo, rev, "config.yaml").decode("utf-8"))
    if not isinstance(base_config, dict):
        raise CutoverRefusal(
            f"config.yaml at {rev[:12]} is not a mapping")
    # The base config's provider_uri is an ENV-VAR TEMPLATE
    # (`${QUANT_PROVIDER_URI:-...}`); the run config persists the
    # RESOLVED path. The expected value comes from the COMMITTED
    # default at the pinned revision, never from the live environment
    # (codex #392 r15): expanding the env var here would let the same
    # wrong QUANT_PROVIDER_URI that mis-trained a member fabricate
    # the expected value too. The data-configuration binding (codex
    # #392 r12) then compares the member's actual trained path
    # against the pre-registered identity.
    raw_provider = base_config.get("provider_uri")
    if isinstance(raw_provider, str):
        base_config["provider_uri"] = _expand_registered_default(
            raw_provider, f"config.yaml@{rev[:12]}")
    _canonicalize_provider_uri(base_config)
    for preset_path in BOOTSTRAP_PRESET_PATHS:
        cfg = yaml.safe_load(
            _show(repo, rev, preset_path).decode("utf-8"))
        if not isinstance(cfg, dict):
            raise CutoverRefusal(
                f"{preset_path} at {rev[:12]} is not a mapping")
        preset_declared.append(cfg)
        try:
            preset_windows.append(
                (str(cfg["train_start"]), str(cfg["train_end"])))
            preset_valid_windows.append(
                (str(cfg["valid_start"]), str(cfg["valid_end"])))
        except KeyError as exc:
            raise CutoverRefusal(
                f"{preset_path} declares no {exc} — cannot bind the "
                "pre-registered windows") from exc
    check_preregistered_windows(
        [(m.fit_start, m.fit_end) for m in members], preset_windows)
    # ...and the gates must have been MEASURED on the pre-registered
    # windows, not merely on span/gap-legal ones (codex #392 r7).
    ens_window = ensemble_artifact.get("window") or {}
    check_preregistered_gate_windows(
        member_gate_windows, preset_valid_windows,
        (ens_window.get("window_start"), ens_window.get("window_end")),
        BOOTSTRAP_DRYRUN_WINDOW)
    # ...and each member must have been TRAINED under that frozen
    # configuration, not merely on its dates (codex #392 r8). Every
    # run persists its resolved config beside the model.
    for i, (member, declared) in enumerate(
            zip(members, preset_declared, strict=True)):
        resolved_run = _member_run_config(Path(member.pkl_path))
        run_config: Any = None
        if resolved_run is not None:
            run_config, run_config_sha = resolved_run
            # The run config is a mutable, uncommitted file — bind it
            # to the digest chain the gates verified (codex #392 r14):
            # the trainer sidecar (whose sha256 the manifest declares
            # and the member gate re-validated) carries the digest of
            # the config the RUN persisted. Read the sidecar ONCE,
            # verify it is the manifest-bound bytes, then require the
            # config digest to match — a post-training YAML edit
            # cannot re-authorize a retuned member.
            sidecar_bytes = _read_bytes_or_refuse(
                Path(member.meta_path), f"member[{i}] trainer sidecar")
            if (hashlib.sha256(sidecar_bytes).hexdigest()
                    != member.meta_sha256):
                raise CutoverRefusal(
                    f"member[{i}] trainer sidecar on disk does not "
                    "match the manifest's declared sha256 — refusing")
            try:
                sidecar_payload = json.loads(
                    sidecar_bytes.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise CutoverRefusal(
                    f"member[{i}] trainer sidecar unreadable: "
                    f"{exc}") from exc
            check_run_config_provenance(
                f"member[{i}]", run_config_sha256=run_config_sha,
                sidecar=sidecar_payload)
            # ...and the CODE that trained it must be registered,
            # clean source (codex #392 r15): explicitly clean tree,
            # at a commit that is mainline ancestry under the same
            # pinned revision every other gate reads.
            member_commit = check_member_source_provenance(
                f"member[{i}]", sidecar_payload)
            _require_registered_commit(
                repo, member_commit, rev, f"member[{i}]")
        check_member_training_config(
            f"member[{i}]", _canonicalize_provider_uri(run_config),
            declared, base_config)

    # ── 3b. every write target must own its path (codex #392 r4) ─
    targets = {
        "manifest_out": str(Path(args.manifest_out).resolve()),
        "status_artifact": str(status_path.resolve()),
        "baseline": str((repo / BASELINE_PATH).resolve()),
        "incumbent": str(Path(args.incumbent).resolve()),
    }
    for i, member in enumerate(members):
        meta_target = Path(member.pkl_path).with_suffix(".meta.json")
        # The inference meta must be OURS to create (codex #392 r13):
        # a file already at this path belongs to another serving
        # setup, or survived an aborted run whose rollback could not
        # complete. Writing would truncate it, and a later rollback
        # would DELETE it — refuse here, still zero-write, and let
        # the operator move it aside explicitly if it is stale.
        if meta_target.exists():
            raise CutoverRefusal(
                f"member[{i}] inference meta target already exists: "
                f"{meta_target} — this run does not own it; move it "
                "aside explicitly if it is stale")
        targets[f"member[{i}] inference meta"] = str(
            meta_target.resolve())
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
        "gate_artifacts": gate_records,
        "members": members,
        "manifest_sha256": manifest_sha,
        "manifest_bytes": manifest_bytes,
    }


def _recheck_members(members: list[Any], moment: str) -> None:
    """Refuse unless every member's pkl + trainer sidecar still hash
    to the manifest's declared digests (codex #392 r22/r23, the
    rotation executor's pre-swap discipline). Called BEFORE the
    manifest link (no-churn refusal: the production path is never
    created) and again AFTER it — the post-link pass runs after the
    install sequence's last mutation point, so "success" certifies
    the members were byte-stable at a moment strictly after the
    manifest became visible. Drift after that is post-install drift,
    which any path-referencing manifest is exposed to for its whole
    life and the serving loader's fail-loud chain refuses before a
    single recommendation is produced."""
    for i, member in enumerate(members):
        for what, spath, expected in (
                ("pkl", Path(member.pkl_path), member.pkl_sha256),
                ("trainer sidecar", Path(member.meta_path),
                 member.meta_sha256)):
            data = _read_bytes_or_refuse(
                spath, f"member[{i}] {what} ({moment})")
            if hashlib.sha256(data).hexdigest() != expected:
                raise CutoverRefusal(
                    f"member[{i}] {what} changed since the gate "
                    f"phase: {spath} — the manifest's hashes would "
                    "not match disk and serving would refuse the "
                    f"ensemble ({moment}). Refusing.")


def _backup_incumbent(incumbent: Path, stamp: str,
                      created: list[Path]) -> dict[str, str]:
    """DP-4 rollback kit: copy the incumbent canonical pkl and its
    metas beside themselves, timestamped. Missing metas are recorded
    honestly rather than fabricated. Every destination is born via an
    exclusive descriptor (codex #392 r14) and registered in
    ``created`` before its first byte (codex #392 r12), so a copy
    that fails partway leaves its partial file inside the rollback
    set — and a file that already exists is someone else's, refused
    and never registered."""
    record: dict[str, str] = {}
    targets = [incumbent,
               incumbent.with_suffix(".meta.json"),
               incumbent.with_name(incumbent.name + ".meta.json")]
    for src in targets:
        if not src.is_file():
            record[src.name] = "absent"
            continue
        dst = src.with_name(src.name + f".pre_bootstrap_{stamp}")
        # Race-free exclusive birth (codex #392 r14, the rotation
        # executor's convention): two overlapping cutovers inside the
        # same timestamp second would both pass an exists() check —
        # copy2 would then truncate the shared destination and the
        # losing run's rollback would unlink the WINNING run's only
        # rollback kit. O_EXCL makes creation itself the
        # adjudication; born 0600, then the source's mode (and
        # owner/group on POSIX) is mirrored once the bytes are in
        # place. Registration sits between the successful create and
        # the first byte (r12) — a foreign file is never registered.
        try:
            bfd = os.open(str(dst),
                          os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise CutoverRefusal(
                f"backup path already exists: {dst} — an overlapping "
                "cutover owns it; refusing") from exc
        except OSError as exc:
            raise CutoverRefusal(
                f"cannot create backup {dst}: {exc}") from exc
        created.append(dst)
        with os.fdopen(bfd, "wb") as bf, open(src, "rb") as sf:
            shutil.copyfileobj(sf, bf)
        shutil.copymode(src, dst)
        if hasattr(os, "chown"):
            src_stat = os.stat(src)
            os.chown(dst, src_stat.st_uid, src_stat.st_gid)
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
                   help="Injectable instant (ISO, tz-aware) — test "
                        "determinism only; must sit within 24h of the "
                        "wall clock (codex #392 r15).")
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
    # Every path this run CREATES, in write order (codex #392 r11).
    # The incumbent canonical is never modified — the backup is a
    # copy, everything else is a new file — so the correct treatment
    # of a mid-write failure is to DELETE what this run created,
    # restoring the exact pre-run state, and to REPORT that state
    # accurately (production keeps serving the incumbent; nothing
    # points at the manifest until the operator switches the morning
    # command).
    created: list[Path] = []
    residue_notes: list[str] = []
    try:
        backup = _backup_incumbent(Path(args.incumbent), stamp, created)
        member_records: list[dict[str, Any]] = []
        for member in members:
            meta_path = Path(member.pkl_path).with_suffix(".meta.json")
            meta = build_inference_meta(
                model_path=member.pkl_path,
                fit_start=member.fit_start, fit_end=member.fit_end,
                model_type="LGBModel", promoted_at=now_iso)
            # EXCLUSIVE create (codex #392 r13): existence was
            # adjudicated in the gate phase, but a file APPEARING
            # since (an overlapping cutover, another serving setup)
            # must refuse — write_text would truncate the foreign
            # artifact and the rollback would then DELETE it.
            # Registration sits between the successful create and the
            # first byte (r12): a failed write leaves the partial
            # file inside the rollback set, while a FileExistsError
            # never reaches the registration line.
            try:
                with open(meta_path, "x", encoding="utf-8") as fh:
                    created.append(meta_path)
                    fh.write(json.dumps(meta, indent=2,
                                        ensure_ascii=False))
            except FileExistsError as exc:
                raise CutoverRefusal(
                    f"member inference meta APPEARED after the gate "
                    f"phase: {meta_path} — another process owns it; "
                    "refusing to clobber") from exc
            member_records.append({
                "pkl_path": member.pkl_path,
                "pkl_sha256": member.pkl_sha256,
                "inference_meta_path": str(meta_path),
                "fit_start": member.fit_start,
                "fit_end": member.fit_end,
            })
        # Members must still be the gated bytes before we touch the
        # production manifest path at all (codex #392 r22) — a
        # refusal here is zero-churn.
        _recheck_members(members, "pre-install recheck")
        out = Path(args.manifest_out)
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
            # NO-CLOBBER install (codex #392 r10): os.replace would
            # overwrite a manifest that APPEARED after the gate-phase
            # existence check (an overlapping cutover). A hard link
            # from the fully-written staging file is atomic AND fails
            # with FileExistsError if the destination exists — same
            # volume by construction (mkstemp in out.parent).
            os.link(tmp, out)
            created.append(out)
        except FileExistsError as exc:
            tmp.unlink(missing_ok=True)
            raise CutoverRefusal(
                f"the production manifest APPEARED after the gate "
                f"phase: {out} — an overlapping cutover or another "
                "process owns it now; refusing to clobber") from exc
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise CutoverRefusal(
                f"cannot install the production manifest with the "
                f"incumbent's readability ({exc}) — the morning run "
                "could not open it; nothing installed") from exc
        # Post-link staging cleanup is SEPARATE from the install
        # (codex #392 r11): the manifest is already in place, so a
        # failing unlink must not raise into a path that reports
        # "nothing installed" — it is benign residue, noted honestly.
        try:
            tmp.unlink(missing_ok=True)
        except OSError as exc:
            residue_notes.append(
                f"staging file survives at {tmp} ({exc}) — remove by "
                "hand; the installed manifest is unaffected")
        # AUTHORITATIVE recheck (codex #392 r23): the pre-link pass
        # reads members sequentially, so an early member could drift
        # while later (large) files were still being hashed. This
        # pass runs after the install sequence's last mutation point
        # — a failure here declares the install FAILED, the rollback
        # removes the just-linked manifest (it is in `created`), and
        # production stays on the incumbent.
        _recheck_members(members, "post-install recheck")

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
        # EXCLUSIVE create (codex #392 r14): existence was adjudicated
        # in the gate phase, but a baseline APPEARING since must
        # refuse — write_text would truncate a canonical record this
        # run does not own, and the rollback would then DELETE it
        # (O_EXCL also refuses a symlink planted at this path).
        # Registration sits between the successful create and the
        # first byte (r12).
        try:
            with open(baseline_path, "x", encoding="utf-8") as fh:
                created.append(baseline_path)
                fh.write(json.dumps(baseline, indent=2,
                                    ensure_ascii=False) + "\n")
        except FileExistsError as exc:
            raise CutoverRefusal(
                f"baseline record APPEARED after the gate phase: "
                f"{baseline_path} — an overlapping cutover owns it; "
                "refusing to clobber") from exc

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
        # Exclusive create — the once-only status must never clobber a
        # state that appeared since the gate phase (same TOCTOU class
        # as the manifest, codex #392 r10).
        with open(status_path, "x", encoding="utf-8") as fh:
            # Registered the moment the exclusive create SUCCEEDS
            # (codex #392 r12): if the write/close then fails
            # (ENOSPC, quota, delayed I/O), the partial status is in
            # the rollback set instead of surviving as an
            # apparently-valid WIN that blocks retries. A
            # FileExistsError above never reaches this line, so a
            # FOREIGN status is never registered and never rolled
            # back.
            created.append(status_path)
            fh.write(json.dumps(status, indent=2,
                                ensure_ascii=False) + "\n")
    except (CutoverRefusal, OSError) as exc:
        # ROLLBACK (codex #392 r11): delete everything THIS RUN
        # created, newest first — the incumbent canonical was never
        # modified, so removing our artifacts restores the exact
        # pre-run state and the report below matches reality
        # (production keeps serving the incumbent; the morning
        # command was never switched).
        survivors: list[str] = []
        for path in reversed(created):
            try:
                path.unlink(missing_ok=True)
            except OSError as rb_exc:
                survivors.append(f"{path} ({rb_exc})")
        print(f"[cutover] WRITE FAILED after gates passed: {exc}",
              file=sys.stderr)
        print(f"[cutover] rolled back {len(created) - len(survivors)} "
              f"artifact(s) created by this run; production is "
              "UNCHANGED (the incumbent canonical was never "
              "modified).", file=sys.stderr)
        if survivors:
            print("[cutover] could NOT remove (delete by hand): "
                  + "; ".join(survivors), file=sys.stderr)
        return 1

    for note in residue_notes:
        print(f"[cutover] NOTE: {note}")
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
