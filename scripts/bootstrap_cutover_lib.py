"""Pure bootstrap-cutover logic (PR-C' of
2026-07-20-csi800-n5-production-promotion).

The FIRST production switch is a PROMOTION path, not the quarterly
maintenance path (codex #389 r8): its preconditions are the full
promotion gate set —

  1. campaign eligibility — the committed verdict sidecar passes
     ``csi800_campaign_certify.py --verify`` and carries
     ``promotion_eligible: true``;
  2. iso_week re-check anchor — the committed re-check evidence, read
     from the mainline at a pinned revision, binds the committed
     iso_week preset and re-derives a POSITIVE full-window net
     excess;
  3. bootstrap gates — three member-scope artifacts (one per
     staggered member) and one ensemble-scope artifact, every one
     PASS and bound to what it gated;
  4. rollback kit — pre-promote backup of the incumbent + a committed
     baseline record.

Any failure = the switch does not execute, the incumbent canonical
and its serving semantics stay unchanged, and the failure is filed
(R1-DP-C: the bootstrap has no "keep the old ensemble" branch — that
action belongs to the quarterly maintenance path).

This module is PURE stdlib: the executor
(``scripts/bootstrap_ensemble_cutover.py``) wires git, the filesystem
and the serving loader around these decisions so every rule is
unit-testable without a bundle.
"""

from __future__ import annotations

import json
import math
from typing import Any

__all__ = [
    "BOOTSTRAP_MEMBER_COUNT",
    "RECERT_STATUS_SCHEMA_VERSION",
    "BASELINE_SCHEMA_VERSION",
    "CutoverRefusal",
    "check_campaign_eligibility",
    "check_isoweek_anchor",
    "build_initial_status",
    "build_baseline_record",
    "build_inference_meta",
]

BOOTSTRAP_MEMBER_COUNT = 3
# Written by THIS path only (first write; the quarterly executor reads
# it and never writes it — R1-DP-D).
RECERT_STATUS_SCHEMA_VERSION = "csi800_recert_status_v1"
BASELINE_SCHEMA_VERSION = "csi800_n5_bootstrap_baseline_v1"

_VERDICT_SCHEMA_VERSION = "csi800_cadence_verdict_v1"


class CutoverRefusal(RuntimeError):
    """A promotion precondition failed — ZERO production writes."""


def _finite(value: Any) -> bool:
    return (not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(float(value)))


def check_campaign_eligibility(sidecar_text: str) -> dict[str, Any]:
    """Gate 1: the committed verdict sidecar must be a well-formed
    ``csi800_cadence_verdict_v1`` granting ``promotion_eligible``.

    ``--verify`` (byte/anchor re-validation) runs in the executor
    against the real repo; this is the CONTENT half — a sidecar that
    verifies but does not grant eligibility must not promote."""
    try:
        payload = json.loads(sidecar_text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CutoverRefusal(
            f"verdict sidecar is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CutoverRefusal("verdict sidecar top level is not an object")
    if payload.get("schema_version") != _VERDICT_SCHEMA_VERSION:
        raise CutoverRefusal(
            f"verdict sidecar schema "
            f"{payload.get('schema_version')!r} != "
            f"{_VERDICT_SCHEMA_VERSION!r}")
    verdict = payload.get("verdict")
    if not isinstance(verdict, dict):
        raise CutoverRefusal("verdict sidecar carries no verdict block")
    if verdict.get("promotion_eligible") is not True:
        raise CutoverRefusal(
            f"verdict sidecar promotion_eligible is "
            f"{verdict.get('promotion_eligible')!r}, not true — the "
            "campaign did not grant promotion eligibility")
    net = verdict.get("conservative_net_annualized")
    if not _finite(net):
        raise CutoverRefusal(
            f"verdict sidecar conservative_net_annualized {net!r} is "
            "not a finite number")
    anchors = payload.get("anchors")
    if not isinstance(anchors, dict):
        raise CutoverRefusal("verdict sidecar carries no anchors block")
    for key in ("pair_anchor", "evidence_anchor", "n1_anchor"):
        value = anchors.get(key)
        if (not isinstance(value, str) or len(value) != 40
                or any(c not in "0123456789abcdef" for c in value.lower())):
            raise CutoverRefusal(
                f"verdict sidecar anchors.{key} {value!r} is not a "
                "40-hex commit id")
    return payload


def check_isoweek_anchor(
    aggregate: Any, *, expected_config_sha256: str,
    actual_config_sha256: str,
) -> dict[str, Any]:
    """Gate 2: the ANCHORED iso_week re-check evidence.

    Two independent bindings (spec: 晋升门第 2 条):

    * the run's embedded config must be the committed iso_week
      re-check preset (content hash equality — the caller supplies
      both digests so this stays pure);
    * the full-window net excess must be RE-DERIVED positive from the
      anchored aggregate, never taken from an operator assertion.
    """
    if actual_config_sha256 != expected_config_sha256:
        raise CutoverRefusal(
            f"iso_week re-check run's embedded config sha256 "
            f"{actual_config_sha256} != the committed preset "
            f"{expected_config_sha256} — the anchored evidence does "
            "not bind the certified serving semantics, refusing")
    if not isinstance(aggregate, dict):
        raise CutoverRefusal(
            "iso_week aggregate report is not an object")
    metrics = aggregate.get("aggregate_metrics")
    if not isinstance(metrics, dict):
        raise CutoverRefusal(
            "iso_week aggregate carries no aggregate_metrics block")
    raw_net = metrics.get("mean_annualized_return")
    if not _finite(raw_net):
        raise CutoverRefusal(
            f"iso_week aggregate mean_annualized_return {raw_net!r} is "
            "not a finite number — corrupted anchor evidence")
    net = float(raw_net)  # type: ignore[arg-type]  # _finite narrows
    if net <= 0.0:
        raise CutoverRefusal(
            f"iso_week re-check net excess {net:.4%} <= 0 — the "
            "production anchor (iso-week) does not reproduce the "
            "certified winner's edge, refusing to switch")
    return {"net_annualized": net,
            "num_folds": aggregate.get("num_folds")}


def build_initial_status(
    *, verdict_sidecar_path: str, verdict_sidecar_sha256: str,
    evidence_anchor_commit: str, note: str,
) -> dict[str, Any]:
    """The FIRST write of the single monotonic certification-state
    artifact (R1-DP-D / codex #389 r7). Its absence is why the
    quarterly executor would freeze the first rotation; its presence
    starts the 15-month validity window — which is exactly why it is
    written HERE, at the cutover, and nowhere earlier."""
    if (not isinstance(verdict_sidecar_sha256, str)
            or len(verdict_sidecar_sha256) != 64):
        raise CutoverRefusal(
            "initial status needs the verdict sidecar's 64-hex content "
            "hash")
    if (not isinstance(evidence_anchor_commit, str)
            or len(evidence_anchor_commit) != 40):
        raise CutoverRefusal(
            "initial status needs a 40-hex evidence anchor commit")
    if not isinstance(note, str) or not note.strip():
        raise CutoverRefusal("initial status needs an adjudication note")
    return {
        "schema_version": RECERT_STATUS_SCHEMA_VERSION,
        "verdict": "WIN",
        "verdict_sidecar_path": verdict_sidecar_path,
        "verdict_sidecar_sha256": verdict_sidecar_sha256,
        "evidence_anchor_commit": evidence_anchor_commit,
        "note": note.strip(),
    }


def build_baseline_record(
    *, manifest_path: str, manifest_sha256: str,
    members: list[dict[str, Any]], incumbent_backup: dict[str, str],
    campaign: dict[str, Any], isoweek: dict[str, Any],
    gate_artifacts: dict[str, str], generated_at: str,
) -> dict[str, Any]:
    """The committed rollback/baseline record (DP-4, ④ precedent):
    what production was BEFORE, what it became, and every piece of
    evidence that authorized the change."""
    if len(members) != BOOTSTRAP_MEMBER_COUNT:
        raise CutoverRefusal(
            f"baseline needs exactly {BOOTSTRAP_MEMBER_COUNT} members")
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "serving": {
            "mode": "ensemble_manifest",
            "manifest_path": manifest_path,
            "manifest_sha256": manifest_sha256,
            "members": members,
        },
        "incumbent_backup": dict(incumbent_backup),
        "authorized_by": {
            "campaign": dict(campaign),
            "isoweek_recheck": dict(isoweek),
            "gate_artifacts": dict(gate_artifacts),
        },
    }


def build_inference_meta(
    *, model_path: str, fit_start: str, fit_end: str,
    model_type: str, promoted_at: str,
) -> dict[str, Any]:
    """Per-member inference meta (``<model>.meta.json``, ④ precedent).

    Serving derives its normalization window from these values, so the
    fit window written here is the member's TRAINING window verbatim —
    the same pair the manifest declares and the member gate bound."""
    for name, value in (("fit_start", fit_start), ("fit_end", fit_end),
                        ("model_type", model_type),
                        ("promoted_at", promoted_at)):
        if not isinstance(value, str) or not value.strip():
            raise CutoverRefusal(
                f"inference meta needs a non-empty {name}")
    return {
        "model_path": model_path,
        "model_type": model_type,
        "fit_start_for_inference": fit_start,
        "fit_end_for_inference": fit_end,
        "train_window": f"{fit_start}..{fit_end}",
        "promoted_at": promoted_at,
    }
