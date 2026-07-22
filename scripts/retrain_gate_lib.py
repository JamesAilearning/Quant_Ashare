"""Pure per-retrain gate logic (PR-B' of
2026-07-20-csi800-n5-production-promotion, R1-DP-B).

Five light gates guard every member/ensemble change to production —
NO net-return gate (R1: performance authority = the certified campaign
evidence + annual re-certification, never a single-quarter number):

  member scope   (a) trainer integrity — best_iteration / final valid
                     loss finite AND best_iteration != num_boost_round
                     (early stopping never fired = training budget
                     exhausted, a boundary anomaly not a convergence
                     signal, codex #389 r12). BOTH values come ONLY
                     from the trainer sidecar; a sidecar missing
                     ``num_boost_round`` (including every legacy
                     sidecar) FAILS the gate — fail-closed, never a
                     fallback to preset defaults (codex #389 r18).
                 (d) IC direction — valid-window IC(1d) > 0.
  ensemble scope (b) degeneracy — 0 degenerate / 0 straddle days on
                     the trailing-quarter EXECUTABLE stamps;
                 (c) constraint dry-run — campaign_v1 RAISE zero
                     triggers on the trailing-quarter N5 backtest;
                 (e) serving veto faces — veto2/5 attribution numbers
                     as-is (<80% / <75% / <10%), veto3 = dry-run daily
                     one-way turnover <= anchored iso_week re-check
                     mean x1.5.

This module is PURE stdlib (the eval_profiles precedent): governance
and acceptance tests import it without dragging qlib/pandas onto the
path. The qlib-bound measurement runner is ``scripts/retrain_gate.py``;
it produces numbers, THIS module turns numbers into verdicts, so every
refusal state is unit-testable without a backtest.

Verdict discipline: every non-finite / missing / wrong-typed input
FAILS its gate (``nan > threshold`` is always False — corrupted
measurements must never read as favorable; campaign precedent codex
#373 r7). The single deliberate cannot-trigger case: veto2's share is
undefined when the gross effect sum is <= 0 (a negative-effect quarter
is a legitimate market outcome, not corruption) — same semantics as
``csi800_campaign_attach_vetoes``.
"""

from __future__ import annotations

import math
from typing import Any

GATE_SCHEMA_VERSION = "csi800_n5_retrain_gate_v1"

SCOPE_MEMBER = "member"
SCOPE_ENSEMBLE = "ensemble"

# Veto thresholds — the campaign attach numbers verbatim (R1-DP-B:
# "数字原样"). Cross-pinned against
# scripts/research/csi800_campaign_attach_vetoes.py in governance.
CSI500_DEPENDENCE_THRESHOLD = 0.80   # veto2: share >= 0.80 fails
TURNOVER_RATIO_THRESHOLD = 1.5       # veto3: dry/anchor > 1.5 fails
CSI500_WEIGHT_THRESHOLD = 0.75       # veto5: weight > 0.75 fails
UNKNOWN_WEIGHT_THRESHOLD = 0.10      # veto5: unknown > 0.10 fails

PASS = "PASS"
FAIL = "FAIL"


def _is_num(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _finite(value: Any) -> bool:
    return _is_num(value) and math.isfinite(float(value))


def gate_trainer_integrity(sidecar: Any) -> dict[str, Any]:
    """Gate (a): trainer completeness, from the sidecar ONLY."""
    reasons: list[str] = []
    if not isinstance(sidecar, dict):
        return {
            "verdict": FAIL,
            "best_iteration": None, "num_boost_round": None,
            "final_valid_loss": None,
            "reasons": ["trainer sidecar is not an object — cannot "
                        "evaluate integrity, fail-closed"],
        }
    best_iter = sidecar.get("best_iteration")
    nbr = sidecar.get("num_boost_round")
    loss = sidecar.get("final_valid_loss")
    if not (_is_num(best_iter) and isinstance(best_iter, int)):
        reasons.append(
            f"best_iteration {best_iter!r} is not an int — extraction "
            "failed or sidecar corrupt")
    elif best_iter <= 0:
        reasons.append(f"best_iteration {best_iter} is not positive")
    if not (_is_num(nbr) and isinstance(nbr, int)) or nbr < 1:
        # codex #389 r18: legacy sidecars without the field fail the
        # SAME way — never a fallback to a preset default.
        reasons.append(
            f"num_boost_round {nbr!r} missing/invalid in sidecar — "
            "fail-closed (no preset-default fallback, legacy sidecars "
            "included)")
    if not _finite(loss):
        reasons.append(
            f"final_valid_loss {loss!r} is not a finite number")
    if (isinstance(best_iter, int) and not isinstance(best_iter, bool)
            and isinstance(nbr, int) and not isinstance(nbr, bool)
            and best_iter == nbr):
        # codex #389 r12: early stopping never fired — the model ran
        # out of training budget instead of converging. Checked
        # independently of the other reasons so the report is complete.
        reasons.append(
            f"best_iteration == num_boost_round ({best_iter}) — early "
            "stopping never triggered (training budget exhausted, "
            "boundary anomaly)")
    return {
        "verdict": FAIL if reasons else PASS,
        "best_iteration": best_iter if _is_num(best_iter) else None,
        "num_boost_round": nbr if _is_num(nbr) else None,
        "final_valid_loss": loss if _is_num(loss) else None,
        "reasons": reasons,
    }


def gate_ic_direction(ic_1d: Any) -> dict[str, Any]:
    """Gate (d): valid-window IC(1d) must be finite and > 0."""
    reasons: list[str] = []
    if not _finite(ic_1d):
        reasons.append(
            f"valid-window ic_1d {ic_1d!r} is not a finite number — "
            "corrupted measurement, fail-closed")
    elif float(ic_1d) <= 0.0:
        reasons.append(f"valid-window ic_1d {float(ic_1d):.6f} <= 0")
    return {
        "verdict": FAIL if reasons else PASS,
        "ic_1d": float(ic_1d) if _finite(ic_1d) else None,
        "reasons": reasons,
    }


def gate_degeneracy(n_degenerate_days: Any,
                    n_cutoff_straddle_days: Any) -> dict[str, Any]:
    """Gate (b): 0 degenerate / 0 straddle on executable stamps."""
    reasons: list[str] = []
    for label, value in (("n_degenerate_days", n_degenerate_days),
                         ("n_cutoff_straddle_days",
                          n_cutoff_straddle_days)):
        if not (_is_num(value) and isinstance(value, int)) or value < 0:
            reasons.append(
                f"{label} {value!r} is not a non-negative int — "
                "corrupted scan, fail-closed")
        elif value != 0:
            reasons.append(f"{label} = {value} (required: 0)")
    return {
        "verdict": FAIL if reasons else PASS,
        "n_degenerate_days": (n_degenerate_days
                              if _is_num(n_degenerate_days) else None),
        "n_cutoff_straddle_days": (
            n_cutoff_straddle_days
            if _is_num(n_cutoff_straddle_days) else None),
        "reasons": reasons,
    }


def gate_constraint_dry_run(constraint_veto: Any) -> dict[str, Any]:
    """Gate (c): the campaign_v1 dry-run must have triggered NOTHING.

    ``constraint_veto`` is the runner's veto record — ``None`` means the
    trailing-quarter backtest completed with zero RAISEs; anything else
    (the stringified veto, a dict, even an empty string from a broken
    producer) fails the gate."""
    if constraint_veto is None:
        return {"verdict": PASS, "constraint_veto": None, "reasons": []}
    return {
        "verdict": FAIL,
        "constraint_veto": constraint_veto,
        "reasons": [f"campaign_v1 constraint dry-run triggered: "
                    f"{constraint_veto!r}"],
    }


def gate_serving_veto(
    *,
    csi500_effect_share: Any,
    csi500_weight: Any,
    unknown_weight: Any,
    dryrun_daily_mean_oneway: Any,
    anchor_daily_mean_oneway: Any,
) -> dict[str, Any]:
    """Gate (e): serving veto faces 2/3/5 with the campaign numbers.

    * veto2 — csi500 sleeve share of the gross effect ``>= 0.80`` fails.
      ``None`` share = undefined (gross effect sum <= 0): the dependence
      leg cannot trigger (campaign semantics), recorded with a note.
    * veto3 — dry-run daily one-way turnover vs the ANCHORED iso_week
      re-check mean: ratio ``> 1.5`` fails. The anchor must be a finite
      positive number (a zero/negative/non-finite anchor is corrupted
      evidence — fail, never divide blindly). Daily-mean comparison is
      scale-equivalent to annualized comparison (same annualization
      factor on both sides).
    * veto5 — csi500 time-mean portfolio weight ``> 0.75`` or unknown
      bucket ``> 0.10`` fails.
    """
    reasons: list[str] = []
    notes: list[str] = []
    if csi500_effect_share is None:
        notes.append("veto2 share undefined (gross effect sum <= 0); "
                     "dependence leg cannot trigger")
    elif not _finite(csi500_effect_share):
        reasons.append(
            f"veto2 csi500 effect share {csi500_effect_share!r} is not "
            "finite — corrupted attribution, fail-closed")
    elif float(csi500_effect_share) >= CSI500_DEPENDENCE_THRESHOLD:
        reasons.append(
            f"veto2: csi500 effect share "
            f"{float(csi500_effect_share):.4f} >= "
            f"{CSI500_DEPENDENCE_THRESHOLD}")
    for label, value, threshold in (
            ("veto5 csi500 weight", csi500_weight,
             CSI500_WEIGHT_THRESHOLD),
            ("veto5 unknown weight", unknown_weight,
             UNKNOWN_WEIGHT_THRESHOLD)):
        if not _finite(value):
            reasons.append(
                f"{label} {value!r} is not finite — corrupted "
                "attribution, fail-closed")
        elif float(value) > threshold:
            reasons.append(
                f"{label} {float(value):.4f} > {threshold}")
    ratio: float | None = None
    if not _finite(anchor_daily_mean_oneway) or (
            float(anchor_daily_mean_oneway) <= 0.0):
        reasons.append(
            f"veto3 anchor daily-mean turnover "
            f"{anchor_daily_mean_oneway!r} is not a finite positive "
            "number — corrupted anchor evidence, fail-closed")
    elif not _finite(dryrun_daily_mean_oneway) or (
            float(dryrun_daily_mean_oneway) < 0.0):
        reasons.append(
            f"veto3 dry-run daily-mean turnover "
            f"{dryrun_daily_mean_oneway!r} is not a finite non-negative "
            "number — corrupted measurement, fail-closed")
    else:
        ratio = (float(dryrun_daily_mean_oneway)
                 / float(anchor_daily_mean_oneway))
        if ratio > TURNOVER_RATIO_THRESHOLD:
            reasons.append(
                f"veto3: turnover ratio {ratio:.4f} > "
                f"{TURNOVER_RATIO_THRESHOLD} (dry "
                f"{float(dryrun_daily_mean_oneway):.6f} vs anchor "
                f"{float(anchor_daily_mean_oneway):.6f})")
    return {
        "verdict": FAIL if reasons else PASS,
        "csi500_effect_share": (
            float(csi500_effect_share)
            if _finite(csi500_effect_share) else None),
        "csi500_weight": (float(csi500_weight)
                          if _finite(csi500_weight) else None),
        "unknown_weight": (float(unknown_weight)
                           if _finite(unknown_weight) else None),
        "dryrun_daily_mean_oneway": (
            float(dryrun_daily_mean_oneway)
            if _finite(dryrun_daily_mean_oneway) else None),
        "anchor_daily_mean_oneway": (
            float(anchor_daily_mean_oneway)
            if _finite(anchor_daily_mean_oneway) else None),
        "turnover_ratio": ratio,
        "thresholds": {
            "csi500_effect_share_max_exclusive":
                CSI500_DEPENDENCE_THRESHOLD,
            "turnover_ratio_max": TURNOVER_RATIO_THRESHOLD,
            "csi500_weight_max": CSI500_WEIGHT_THRESHOLD,
            "unknown_weight_max": UNKNOWN_WEIGHT_THRESHOLD,
        },
        "reasons": reasons,
        "notes": notes,
    }


_SCOPE_GATES = {
    SCOPE_MEMBER: ("trainer_integrity", "ic_direction"),
    SCOPE_ENSEMBLE: ("degeneracy", "constraint_dry_run", "serving_veto"),
}


def assemble_gate_artifact(
    *,
    scope: str,
    gates: dict[str, dict[str, Any]],
    subject: dict[str, Any],
    window: dict[str, str] | None,
    anchor: dict[str, Any] | None,
    generated_at: str,
) -> dict[str, Any]:
    """The machine-readable gate artifact — the rotation executor's
    ONLY admissible evidence that the gates ran and passed.

    ``subject`` binds the artifact to what was gated: member scope
    carries the member identity (pkl/meta paths + sha256s), ensemble
    scope carries ``manifest_path``/``manifest_sha256`` of the
    CANDIDATE manifest. ``overall`` is PASS only when every gate of
    the scope is present and PASS — a missing gate is a producer bug
    and fails the artifact (never a silently thinner gate set,
    codex #389 r10)."""
    if scope not in _SCOPE_GATES:
        raise ValueError(f"unknown gate scope {scope!r}")
    expected = _SCOPE_GATES[scope]
    missing = [name for name in expected if name not in gates]
    unexpected = [name for name in gates if name not in expected]
    if unexpected:
        raise ValueError(
            f"gates {unexpected} do not belong to scope {scope!r}")
    verdicts = {name: gates[name].get("verdict") for name in gates}
    overall = PASS if (not missing and all(
        v == PASS for v in verdicts.values())) else FAIL
    artifact: dict[str, Any] = {
        "schema_version": GATE_SCHEMA_VERSION,
        "scope": scope,
        "generated_at": generated_at,
        "subject": dict(subject),
        "window": dict(window) if window is not None else None,
        "anchor": dict(anchor) if anchor is not None else None,
        "gates": {name: dict(block) for name, block in gates.items()},
        "missing_gates": missing,
        "overall": overall,
    }
    return artifact
