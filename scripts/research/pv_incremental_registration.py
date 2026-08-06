"""Registration binding shared by the pv_incremental_v1 consumers.

The registrar freezes a batch by writing ``candidates.json`` plus a
provenance sidecar recording the manifest's digest and the digests of
the GP inputs (pool, expressions, baseline). Recording is not
enforcement: without this module the OOS evaluator would happily
preflight an edited manifest, and it would score against whatever
``--baseline-preds`` it was handed — so a batch could be bred against
baseline A and adjudicated for incrementality against baseline B, and
the "frozen" family could change between registration and adjudication
without anything noticing (codex #402 r6).

Both consumers load the registration through here and refuse on any
mismatch. There is deliberately no opt-out: an unregistered manifest
cannot be evaluated or adjudicated, because the freeze is the thing
that makes the one-shot OOS window meaningful.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

PROTOCOL_ID = "pv_incremental_v1"
SIDECAR_SUFFIX = ".provenance.json"


class PVRegistrationError(RuntimeError):
    """The manifest is not the registered one (or is unregistered)."""


def sidecar_path_for(manifest_path: Path) -> Path:
    """The registrar writes ``<manifest><SIDECAR_SUFFIX>`` beside it."""
    return manifest_path.with_name(manifest_path.name + SIDECAR_SUFFIX)


def load_registration(manifest_path: Path) -> dict[str, Any]:
    """Load + verify the registration that froze this manifest.

    Refuses when the sidecar is absent (an unregistered manifest is not
    a batch), carries a foreign protocol, lacks the digest, or when the
    manifest's CURRENT bytes disagree with the digest recorded at
    registration — the last one is the whole point: exclusive creation
    stops a second registrar, not a later edit.
    """
    sidecar = sidecar_path_for(manifest_path)
    if not sidecar.is_file():
        raise PVRegistrationError(
            f"{manifest_path.name} has no registration sidecar "
            f"({sidecar.name}) — only a REGISTERED batch may be "
            "evaluated or adjudicated; run "
            "scripts/research/pv_incremental_register_candidates.py "
            "over the GP pool; refusing.")
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PVRegistrationError(
            f"{sidecar.name} is not a JSON object; refusing.")
    if payload.get("protocol_id") != PROTOCOL_ID:
        raise PVRegistrationError(
            f"{sidecar.name} carries protocol_id "
            f"{payload.get('protocol_id')!r} — foreign registration; "
            "refusing.")
    recorded = payload.get("manifest_sha256")
    if not isinstance(recorded, str) or not re.fullmatch(
            r"[0-9a-f]{64}", recorded):
        raise PVRegistrationError(
            f"{sidecar.name} records no valid manifest_sha256 "
            f"({recorded!r}); refusing.")
    actual = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if actual != recorded:
        raise PVRegistrationError(
            f"{manifest_path.name} digests to {actual[:12]}… but the "
            f"registration recorded {recorded[:12]}… — the manifest "
            "was modified after registration; the family is no longer "
            "the frozen one; refusing.")
    return payload


def assert_baseline_matches_registration(
        registration: dict[str, Any], baseline_sha256: str) -> str:
    """The baseline scored against MUST be the one bred against.

    Two provenance-valid exports of the same frozen baseline model are
    both individually legitimate, which is exactly why identity has to
    be checked by digest: breeding against A and adjudicating
    incrementality against B would silently compare a factor to a
    baseline it never competed with.
    """
    digests = registration.get("gp_input_sha256")
    if not isinstance(digests, dict):
        raise PVRegistrationError(
            "the registration records no gp_input_sha256 — the "
            "baseline that shaped the candidates cannot be "
            "identified; re-register with the current registrar; "
            "refusing.")
    recorded = digests.get("baseline_preds.parquet")
    if not isinstance(recorded, str) or not re.fullmatch(
            r"[0-9a-f]{64}", recorded):
        raise PVRegistrationError(
            f"the registration records no valid baseline digest "
            f"({recorded!r}); refusing.")
    if baseline_sha256 != recorded:
        raise PVRegistrationError(
            f"the supplied baseline digests to {baseline_sha256[:12]}… "
            f"but the candidates were bred against {recorded[:12]}… — "
            "scoring incrementality against a different baseline than "
            "the one the GP competed with; refusing.")
    return recorded
