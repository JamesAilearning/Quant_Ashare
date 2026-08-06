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


_REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = _REPO_ROOT / "docs" / "prereg" / "pv_incremental_ledger.yaml"


def _committed_ledger_bytes(ledger_path: Path) -> bytes:
    """Read the ledger AS COMMITTED, not as it sits in the tree.

    A writable checkout defeats a working-tree read (codex #403 r4):
    append a forged digest, run the evaluator, revert — no commit, no
    review, and the "append-only authority" is just another local
    file. Reading ``git show HEAD:<path>`` makes the authority what
    was actually recorded; an uncommitted modification refuses with
    the remedy (commit the ledger entry first), which is exactly the
    act that makes a registration reviewable.
    """
    import subprocess

    try:
        rel = ledger_path.resolve().relative_to(_REPO_ROOT)
    except ValueError as exc:
        raise PVRegistrationError(
            f"ledger {ledger_path} is outside the repository — its "
            "committed state cannot be established; refusing."
        ) from exc
    rel_posix = str(rel).replace("\\", "/")
    try:
        committed = subprocess.run(
            ["git", "show", f"HEAD:{rel_posix}"],
            cwd=str(_REPO_ROOT), capture_output=True, check=False)
    except OSError as exc:
        raise PVRegistrationError(
            f"cannot invoke git to read the committed ledger ({exc}) "
            "— the registration authority cannot be established; "
            "refusing.") from exc
    if committed.returncode != 0:
        raise PVRegistrationError(
            f"{rel_posix} is not committed at HEAD "
            f"({committed.stderr.decode('utf-8', 'replace').strip()}) "
            "— a registration is only real once the ledger entry is "
            "committed; commit it and re-run; refusing.")
    on_disk = ledger_path.read_bytes()
    crlf, lf = bytes((13, 10)), bytes((10,))
    if on_disk.replace(crlf, lf) != committed.stdout.replace(crlf, lf):
        raise PVRegistrationError(
            f"{rel_posix} differs from its committed state — an "
            "uncommitted ledger edit is not a registration; commit "
            "the entry (append-only) and re-run; refusing.")
    return committed.stdout


def _ledger_entries(ledger_path: Path) -> list[dict[str, Any]]:
    """Parsed entries of the COMMITTED ledger."""
    import yaml

    if not ledger_path.is_file():
        raise PVRegistrationError(
            f"campaign ledger {ledger_path} not found — the "
            "registration cannot be authenticated; refusing.")
    raw = _committed_ledger_bytes(ledger_path)
    doc = yaml.safe_load(raw.decode("utf-8"))
    if not isinstance(doc, dict) or doc.get("protocol_id") != PROTOCOL_ID:
        raise PVRegistrationError(
            f"campaign ledger {ledger_path.name} is not the "
            f"{PROTOCOL_ID} ledger; refusing.")
    return [e for e in (doc.get("entries") or []) if isinstance(e, dict)]


def ledger_entry_for(manifest_sha256: str,
                     ledger_path: Path) -> dict[str, Any]:
    """The COMMITTED ledger entry that registered this manifest.

    Returns the entry's own ``gp_provenance`` — the authoritative
    record. Taking only the manifest digest from the ledger and then
    trusting the adjacent sidecar for everything else (codex #403 r4)
    would let an attacker keep a legitimately-registered manifest
    while swapping the sidecar's baseline digest, so the incremental
    comparison would run against a baseline the GP never competed
    with.
    """
    for entry in _ledger_entries(ledger_path):
        prov = entry.get("gp_provenance")
        recorded = (prov or {}).get("manifest_sha256")             if isinstance(prov, dict) else None
        artifacts = [a for a in (entry.get("artifacts") or [])
                     if isinstance(a, str) and "#sha256=" in a]
        artifact_digests = {a.split("#sha256=", 1)[1].strip()
                            for a in artifacts}
        if recorded == manifest_sha256 or manifest_sha256 in artifact_digests:
            if not isinstance(prov, dict):
                raise PVRegistrationError(
                    f"the ledger entry registering {manifest_sha256[:12]}"
                    "… carries no gp_provenance — it cannot authorise "
                    "the batch's inputs; refusing.")
            return prov
    raise PVRegistrationError(
        f"manifest digest {manifest_sha256[:12]}… is not recorded in "
        f"the committed campaign ledger ({ledger_path.name}) — a "
        "sidecar can be written by anyone, so the ledger is what "
        "makes a registration real; append the registrar's "
        "ledger_entry.yaml, COMMIT it (append-only), and re-run; "
        "refusing.")


def load_registration(manifest_path: Path,
                      ledger_path: Path | None = None) -> dict[str, Any]:
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
    # Self-consistency between two colocated, equally-writable files
    # is not authentication (codex #403 r3). The append-only campaign
    # ledger — in git, reviewed, never rewritten — is the independent
    # authority that this digest was actually registered.
    ledger = ledger_path if ledger_path is not None else LEDGER_PATH
    authoritative = ledger_entry_for(actual, ledger)
    # The LEDGER's provenance is what the consumers act on; the
    # sidecar is a convenience copy that must agree with it (codex
    # #403 r4). Returning the ledger's record means a doctored
    # sidecar changes nothing downstream.
    sidecar_inputs = payload.get("gp_input_sha256")
    ledger_inputs = authoritative.get("gp_input_sha256")
    if isinstance(sidecar_inputs, dict) and isinstance(ledger_inputs, dict):
        drift = {k: {"sidecar": v, "ledger": ledger_inputs.get(k)}
                 for k, v in sidecar_inputs.items()
                 if ledger_inputs.get(k) != v}
        if drift:
            raise PVRegistrationError(
                f"the sidecar's input digests disagree with the "
                f"committed ledger entry: {drift} — refusing.")
    return dict(authoritative)


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
