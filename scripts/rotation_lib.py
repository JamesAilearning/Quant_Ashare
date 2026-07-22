"""Pure rotation-executor logic (PR-B' of
2026-07-20-csi800-n5-production-promotion, R1 quarterly maintenance
path).

The quarterly member rotation is a MAINTENANCE path (codex #389 r1):
its preconditions are exactly (a) the standing campaign certification
is valid, (b) the new member passed the per-retrain light gates,
(c) a pre-rotation manifest backup exists. This module holds the pure
decision logic; ``scripts/rotate_ensemble_member.py`` wires git and
the filesystem around it.

Certification state (codex #389 r2/r3/r4/r5) is read from the SINGLE
monotonic status artifact ``docs/promotion/csi800_recert_status.json``
as it exists on ``origin/main`` — the verdict comes from the FILE
CONTENT (never from cross-path date/topology inference), and the
15-month validity window is anchored on the status-artifact PATH's
tip commit committer date on the mainline (a non-recert touch of the
verdict SIDECAR path never moves it). ``verdict: LOSE`` freezes
rotation until a new WIN state merges.

This module deliberately does NOT write the status artifact: its first
write belongs to the PR-C' bootstrap cutover (writing it earlier would
start the 15-month clock and hand the executor a valid state before
production actually switched).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from scripts.retrain_gate_lib import (
    FAIL,
    GATE_PROFILE,
    GATE_SCHEMA_VERSION,
    PASS,
    SCOPE_ENSEMBLE,
    SCOPE_MEMBER,
    expected_gates,
)

__all__ = [
    "RECERT_STATUS_PATH",
    "RECERT_STATUS_SCHEMA_VERSION",
    "VALIDITY_MONTHS",
    "RotationRefusal",
    "git_show_status_cmd",
    "git_status_tip_cmd",
    "parse_recert_status",
    "recert_validity",
    "check_gate_artifact",
    "check_gate_window",
    "plan_rotated_members",
]

# The single monotonic certification-state artifact (codex #389 r3/r4;
# r9: docs/promotion/ — production promotion state, not research).
RECERT_STATUS_PATH = "docs/promotion/csi800_recert_status.json"
RECERT_STATUS_SCHEMA_VERSION = "csi800_recert_status_v1"
# 12-month re-certification cycle + 3-month execution grace.
VALIDITY_MONTHS = 15

# ── gate-window mechanization (codex #391 r19) ──────────────────────
# The spec defines the member IC gate over the member's VALID window
# and the ensemble gates over the TRAILING QUARTER. Binding only the
# digests would let a PASS artifact measured on an arbitrary (easier
# or stale) period authorize a rotation, so the executor also binds
# WHEN the gates were measured. Per-scope window keys as the runner
# writes them:
GATE_WINDOW_KEYS = {
    SCOPE_MEMBER: ("valid_start", "valid_end"),
    SCOPE_ENSEMBLE: ("window_start", "window_end"),
}
# A member's valid window SHALL follow its training window promptly
# (embargo gap) and strictly out of sample.
MEMBER_VALID_GAP_DAYS_MAX = 45
# Both scopes: a quarter to a half-year of measured days.
GATE_WINDOW_SPAN_DAYS_MIN = 60
GATE_WINDOW_SPAN_DAYS_MAX = 200
# "Trailing"/current means the measured window ENDS near the rotation
# instant — two quarters of slack, well beyond any legitimate lag.
GATE_WINDOW_RECENCY_DAYS_MAX = 180
# Small grace for a window ending on a quarter boundary the gate ran
# a few days ahead of; beyond it the artifact claims the future.
GATE_WINDOW_FUTURE_GRACE_DAYS = 7

_MAINLINE = "origin/main"


class RotationRefusal(RuntimeError):
    """A precondition failed — the executor refuses with ZERO writes."""


def git_show_status_cmd() -> list[str]:
    """The exact argv that reads the status artifact CONTENT from the
    mainline — pinned so tests can assert the executor never reads the
    working tree or another path."""
    return ["git", "show", f"{_MAINLINE}:{RECERT_STATUS_PATH}"]


def git_status_tip_cmd() -> list[str]:
    """The exact argv for the validity anchor: tip commit committer
    date of the STATUS-ARTIFACT PATH on the mainline (codex #389 r5 —
    never the sidecar path, whose non-recert touches would drift the
    validity window)."""
    return ["git", "log", "-1", "--format=%cI", _MAINLINE, "--",
            RECERT_STATUS_PATH]


def parse_recert_status(text: str) -> dict[str, Any]:
    """Parse + schema-validate the status artifact, fail-closed.

    Required always: ``schema_version`` (exact), ``verdict`` (WIN|LOSE),
    ``evidence_anchor_commit`` (40-hex), ``note`` (the adjudication
    statement). WIN additionally requires ``verdict_sidecar_path`` and
    ``verdict_sidecar_sha256`` (64-hex content hash of the verdict
    sidecar the WIN refers to). Anything malformed refuses — an
    unparseable certification state can never authorize a rotation."""
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RotationRefusal(
            f"recert status artifact is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RotationRefusal(
            "recert status artifact top level is not an object")
    if payload.get("schema_version") != RECERT_STATUS_SCHEMA_VERSION:
        raise RotationRefusal(
            f"recert status schema {payload.get('schema_version')!r} != "
            f"{RECERT_STATUS_SCHEMA_VERSION!r}")
    verdict = payload.get("verdict")
    if verdict not in ("WIN", "LOSE"):
        raise RotationRefusal(
            f"recert status verdict {verdict!r} is not WIN|LOSE")
    anchor = payload.get("evidence_anchor_commit")
    if (not isinstance(anchor, str) or len(anchor) != 40
            or any(c not in "0123456789abcdef" for c in anchor.lower())):
        raise RotationRefusal(
            f"recert status evidence_anchor_commit {anchor!r} is not a "
            "40-hex commit id")
    note = payload.get("note")
    if not isinstance(note, str) or not note.strip():
        raise RotationRefusal(
            "recert status note (adjudication statement) is missing")
    if verdict == "WIN":
        sidecar_path = payload.get("verdict_sidecar_path")
        sidecar_sha = payload.get("verdict_sidecar_sha256")
        if not isinstance(sidecar_path, str) or not sidecar_path:
            raise RotationRefusal(
                "WIN status must reference verdict_sidecar_path")
        if (not isinstance(sidecar_sha, str) or len(sidecar_sha) != 64
                or any(c not in "0123456789abcdef"
                       for c in sidecar_sha.lower())):
            raise RotationRefusal(
                f"WIN status verdict_sidecar_sha256 {sidecar_sha!r} is "
                "not a 64-hex content hash")
    return payload


def _add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    # Clamp the day-of-month (e.g. Jan 31 + 1mo -> Feb 28/29). Month-
    # level granularity is the spec's stated horizon.
    dim = [31, 29 if (year % 4 == 0 and (year % 100 != 0
                                         or year % 400 == 0)) else 28,
           31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(day.day, dim))


def recert_validity(
    status: dict[str, Any], status_tip_iso: str, now_iso: str,
) -> tuple[bool, str]:
    """(rotation_allowed, reason). LOSE freezes; a WIN older than
    15 months (anchored on the status-path mainline tip committer
    date) freezes; otherwise rotation is allowed.

    ``status_tip_iso`` is the ``%cI`` committer date of the status
    artifact path's tip commit on the mainline; ``now_iso`` is the
    evaluation instant (injected — the executor passes wall clock,
    tests pass fixtures)."""
    if status.get("verdict") == "LOSE":
        return False, (
            "certification state is LOSE — rotation frozen until a new "
            "WIN status merges (operator decision point)")
    try:
        tip = datetime.fromisoformat(status_tip_iso)
        now = datetime.fromisoformat(now_iso)
    except (TypeError, ValueError) as exc:
        return False, (
            f"cannot parse validity anchor dates "
            f"(tip={status_tip_iso!r}, now={now_iso!r}): {exc}")
    if now.tzinfo is None or tip.tzinfo is None:
        return False, (
            "validity anchor dates must be timezone-aware ISO "
            f"timestamps (tip={status_tip_iso!r}, now={now_iso!r})")
    expiry_day = _add_months(tip.date(), VALIDITY_MONTHS)
    if now.date() > expiry_day:
        return False, (
            f"certification WIN expired: status tip {tip.date()} + "
            f"{VALIDITY_MONTHS} months = {expiry_day} < today "
            f"{now.date()} — annual re-certification is due; rotation "
            "frozen")
    return True, (
        f"certification WIN valid until {expiry_day} (status tip "
        f"{tip.date()})")


def _parse_day(value: Any, what: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise RotationRefusal(
            f"{what} {value!r} is not a YYYY-MM-DD date") from exc


def check_gate_window(
    artifact: Any, *, scope: str, member_fit_end: str | None,
    now_iso: str,
) -> None:
    """Bind WHEN the gates were measured (codex #391 r19).

    Digest bindings prove WHICH artifacts were gated; without this a
    PASS artifact measured on a 1900 (or simply stale/easier) period
    still authorizes the rotation. Per scope:

    * member — the IC gate's valid window SHALL start strictly after
      the member's training window (out of sample) and promptly after
      it (embargo gap, not an arbitrary later period);
    * ensemble — the trailing-quarter dry run legitimately overlaps
      the newest member's training data (its purpose is behavioral,
      not performance — R1: no net gate), so only recency and span
      are bound there.

    Both scopes: a quarter-to-half-year span, ending near the rotation
    instant and not in the future.
    """
    keys = GATE_WINDOW_KEYS[scope]
    window = artifact.get("window")
    if not isinstance(window, dict):
        raise RotationRefusal(
            f"{scope} gate artifact carries no window block — the "
            "measured period is unbound, refusing")
    start = _parse_day(window.get(keys[0]),
                       f"{scope} gate window {keys[0]}")
    end = _parse_day(window.get(keys[1]),
                     f"{scope} gate window {keys[1]}")
    now = _parse_day(now_iso[:10], "rotation instant")
    span = (end - start).days
    if not (GATE_WINDOW_SPAN_DAYS_MIN <= span
            <= GATE_WINDOW_SPAN_DAYS_MAX):
        raise RotationRefusal(
            f"{scope} gate measured window {start}..{end} spans "
            f"{span}d, outside the pinned "
            f"[{GATE_WINDOW_SPAN_DAYS_MIN}, "
            f"{GATE_WINDOW_SPAN_DAYS_MAX}] — refusing")
    if (now - end).days > GATE_WINDOW_RECENCY_DAYS_MAX:
        raise RotationRefusal(
            f"{scope} gate measured window ends {end}, "
            f"{(now - end).days}d before the rotation instant {now} "
            f"(max {GATE_WINDOW_RECENCY_DAYS_MAX}d) — a stale gate "
            "cannot authorize this rotation")
    if (end - now).days > GATE_WINDOW_FUTURE_GRACE_DAYS:
        raise RotationRefusal(
            f"{scope} gate measured window ends {end}, in the future "
            f"relative to the rotation instant {now} — refusing")
    if scope == SCOPE_MEMBER:
        fit_end = _parse_day(member_fit_end,
                             "candidate member fit_end")
        if start <= fit_end:
            raise RotationRefusal(
                f"member gate valid window starts {start}, not after "
                f"the member's training window end {fit_end} — the IC "
                "gate must be out of sample, refusing")
        if (start - fit_end).days > MEMBER_VALID_GAP_DAYS_MAX:
            raise RotationRefusal(
                f"member gate valid window starts {start}, "
                f"{(start - fit_end).days}d after the training window "
                f"end {fit_end} (max {MEMBER_VALID_GAP_DAYS_MAX}d) — "
                "that is not this member's valid window, refusing")


def check_gate_artifact(
    artifact: Any, *, scope: str, expected_subject_sha: str,
    expected_meta_sha: str | None = None,
    expected_fit_window: tuple[str, str] | None = None,
    member_fit_end: str | None = None,
    now_iso: str | None = None,
) -> None:
    """Admit a gate artifact for rotation, fail-closed (codex #389
    r11: a gate the tool FAILED — or that never ran — must be a closed
    channel; the executor refuses on absence AND on FAIL).

    Binding: member scope binds ``subject.pkl_sha256`` to the incoming
    member's pickle digest AND — when ``expected_meta_sha`` is given —
    ``subject.meta_sha256`` to its sidecar digest (the trainer-
    integrity gate judged the SIDECAR: a regenerated sidecar under the
    same pickle must invalidate the artifact); ensemble scope binds
    ``subject.manifest_sha256`` to the CANDIDATE manifest digest.

    The verdict is RE-DERIVED from the per-gate blocks (every expected
    gate present, each ``verdict: PASS``) and must AGREE with the
    ``overall`` field — a hand-edited artifact whose ``overall`` says
    PASS over failing/absent gates is refused."""
    if scope not in (SCOPE_MEMBER, SCOPE_ENSEMBLE):
        raise ValueError(f"unknown gate scope {scope!r}")
    if not isinstance(artifact, dict):
        raise RotationRefusal(
            f"{scope} gate artifact is not an object")
    if artifact.get("schema_version") != GATE_SCHEMA_VERSION:
        raise RotationRefusal(
            f"{scope} gate artifact schema "
            f"{artifact.get('schema_version')!r} != "
            f"{GATE_SCHEMA_VERSION!r}")
    if artifact.get("profile") != GATE_PROFILE:
        # codex #391 r12: a gate measured under different semantics
        # (e.g. csi300_daily) must never authorize a csi800_n5
        # rotation — the artifact stamps its profile and this consumer
        # refuses anything else.
        raise RotationRefusal(
            f"{scope} gate artifact profile "
            f"{artifact.get('profile')!r} != {GATE_PROFILE!r} — gates "
            "measured under different semantics cannot authorize this "
            "rotation")
    if artifact.get("scope") != scope:
        raise RotationRefusal(
            f"gate artifact scope {artifact.get('scope')!r} != "
            f"expected {scope!r}")
    overall = artifact.get("overall")
    if overall == FAIL:
        raise RotationRefusal(
            f"{scope} gate artifact records overall FAIL — the member "
            "does not enter the ensemble (rotation refused; the gate "
            "verdict stands)")
    if overall != PASS:
        raise RotationRefusal(
            f"{scope} gate artifact overall {overall!r} is not PASS")
    gates = artifact.get("gates")
    if not isinstance(gates, dict):
        raise RotationRefusal(
            f"{scope} gate artifact carries no gates block — a bare "
            "overall field is not admissible evidence")
    if set(gates) != set(expected_gates(scope)):
        # codex #391 r7: the gate set must match EXACTLY — an extra
        # block (e.g. a hand-edit, or a future producer adding a gate
        # without updating this consumer) could carry a FAIL the
        # expected-names loop would silently ignore.
        raise RotationRefusal(
            f"{scope} gate artifact gate set {sorted(gates)} != "
            f"expected {sorted(expected_gates(scope))} — refusing an "
            "artifact whose gate set this executor does not fully "
            "adjudicate")
    for name in expected_gates(scope):
        block = gates.get(name)
        if not isinstance(block, dict) or block.get("verdict") != PASS:
            raise RotationRefusal(
                f"{scope} gate artifact gate {name!r} is absent or not "
                "PASS — the overall field disagrees with the per-gate "
                "verdicts, refusing")
    subject = artifact.get("subject")
    if not isinstance(subject, dict):
        raise RotationRefusal(
            f"{scope} gate artifact carries no subject binding")
    key = ("pkl_sha256" if scope == SCOPE_MEMBER else "manifest_sha256")
    actual = subject.get(key)
    if actual != expected_subject_sha:
        raise RotationRefusal(
            f"{scope} gate artifact subject.{key} {actual!r} does not "
            f"bind to the expected digest {expected_subject_sha} — the "
            "artifact gates something else, refusing")
    if expected_meta_sha is not None:
        actual_meta = subject.get("meta_sha256")
        if actual_meta != expected_meta_sha:
            raise RotationRefusal(
                f"{scope} gate artifact subject.meta_sha256 "
                f"{actual_meta!r} does not bind to the expected sidecar "
                f"digest {expected_meta_sha} — the trainer-integrity "
                "verdict belongs to a different sidecar, refusing")
    if expected_fit_window is not None:
        # codex #391 r12: serving derives the inference normalization
        # window from the manifest's newest-member dates — a candidate
        # whose fit window differs from what the member gate evaluated
        # would install dates the IC gate never judged.
        actual_window = (subject.get("fit_start"),
                         subject.get("fit_end"))
        if actual_window != expected_fit_window:
            raise RotationRefusal(
                f"{scope} gate artifact subject fit window "
                f"{actual_window!r} != the candidate member's "
                f"{expected_fit_window!r} — the gate evaluated a "
                "different window, refusing")
    if now_iso is not None:
        # codex #391 r19: bind WHEN the gates were measured, not only
        # WHICH artifacts they gated.
        check_gate_window(artifact, scope=scope,
                          member_fit_end=member_fit_end,
                          now_iso=now_iso)


def plan_rotated_members(
    current_members: list[dict[str, Any]], new_member: dict[str, Any],
) -> list[dict[str, Any]]:
    """The rotation plan: drop the OLDEST member (index 0 of the
    oldest->newest manifest order), append the new member. Structural
    validation (spacing/window/duplicate-identity pins) is delegated to
    the serving loader on the planned manifest — one validator, no
    drift."""
    if not isinstance(current_members, list) or len(current_members) != 3:
        raise RotationRefusal(
            "current manifest must carry exactly 3 members to rotate")
    required = ("pkl_path", "pkl_sha256", "meta_path", "meta_sha256",
                "fit_start", "fit_end")
    missing = [k for k in required if not new_member.get(k)]
    if missing:
        raise RotationRefusal(
            f"new member is missing fields: {missing}")
    return [dict(m) for m in current_members[1:]] + [dict(new_member)]
