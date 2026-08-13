"""Campaign-ledger binding for promoted factor bundles.

The promotion chain's authority has to live in git, not in whatever the
invocation happens to hand over. Two places consume it:

* the promotion tool, which turns an FWER survivor into a single-entry
  pool;
* the walk-forward runner, which binds that pool as the paired
  comparison's treatment arm.

Before this module both accepted their authority from the same
invocation that used it (codex #422 r1/r2): a locally regenerated
verdict plus its own digest, or any valid FactorPool on disk, would
authorize a decision-grade run whose report recorded nothing but a
generic handler name. The ledger is committed, so anchoring to it is
what makes the chain checkable by someone else, later.

Pure ``yaml``/``json``/``hashlib`` — no qlib, no ``src.pit``, so the D5
gate is untouched and the runner can import it as freely as the CLI.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

PROMOTION_PROVENANCE_FILENAME = "promotion_provenance.json"
# The ledger entry whose recorded verdict authorises the promotion the
# paired treatment arm binds. One name, imported by both consumers.
PROMOTION_LEDGER_ENTRY = "E007"
# The ledger entry that PRE-REGISTERS which survivor the paired
# comparison binds. E007 says who MAY be promoted (50 survivors); this
# says who WAS registered as the treatment variant — the ruler judges
# the plan's single variant name, so binding any other survivor would
# earn a decision-grade verdict under someone else's identity.
REPRESENTATIVE_LEDGER_ENTRY = "E008"
_VERDICT_ARTIFACT_RE = re.compile(
    r"fwer_verdict\.json#sha256=([0-9a-f]{64})")


class PromotionBindingError(RuntimeError):
    """The promotion chain cannot be tied back to the ledger."""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ledger_verdict_sha256(ledger_path: Path, *, entry_id: str) -> str:
    """The FWER verdict digest THE LEDGER records for ``entry_id``.

    This is the anchor: the ledger is committed, so a caller cannot
    supply its own notion of which adjudication authorised a promotion.
    """
    if not ledger_path.is_file():
        raise PromotionBindingError(
            f"campaign ledger not found: {ledger_path}; refusing.")
    ledger = yaml.safe_load(ledger_path.read_text(encoding="utf-8"))
    entries = (ledger or {}).get("entries") or []
    matches = [e for e in entries
               if isinstance(e, dict) and e.get("id") == entry_id]
    if len(matches) != 1:
        raise PromotionBindingError(
            f"ledger {ledger_path} carries {len(matches)} entries with id "
            f"{entry_id!r} (expected exactly 1); refusing.")
    digests = {
        m.group(1)
        for artifact in matches[0].get("artifacts") or []
        if (m := _VERDICT_ARTIFACT_RE.search(str(artifact)))
    }
    if len(digests) != 1:
        raise PromotionBindingError(
            f"ledger entry {entry_id} records {len(digests)} distinct "
            "fwer_verdict.json sha256 artifacts (expected exactly 1) — the "
            "adjudication this promotion claims cannot be identified; "
            "refusing.")
    return digests.pop()


def ledger_representative(
    ledger_path: Path, *, entry_id: str = REPRESENTATIVE_LEDGER_ENTRY,
) -> dict[str, str]:
    """The candidate id + expression the ledger pre-registered."""
    if not ledger_path.is_file():
        raise PromotionBindingError(
            f"campaign ledger not found: {ledger_path}; refusing.")
    ledger = yaml.safe_load(ledger_path.read_text(encoding="utf-8"))
    entries = (ledger or {}).get("entries") or []
    matches = [e for e in entries
               if isinstance(e, dict) and e.get("id") == entry_id]
    if len(matches) != 1:
        raise PromotionBindingError(
            f"ledger {ledger_path} carries {len(matches)} entries with id "
            f"{entry_id!r} (expected exactly 1); refusing.")
    numbers = matches[0].get("numbers") or {}
    candidate_id = str(numbers.get("representative") or "")
    expression = str(numbers.get("representative_expression") or "")
    if not candidate_id or not expression:
        raise PromotionBindingError(
            f"ledger entry {entry_id} records no representative / "
            "representative_expression — the registered treatment cannot be "
            "identified, so no bundle can be shown to BE it; refusing.")
    return {"candidate_id": candidate_id, "expression": expression}


def verify_verdict_against_ledger(
    verdict_path: Path, ledger_path: Path, *, entry_id: str,
    expect_sha256: str | None = None,
) -> str:
    """Digest ``verdict_path`` and require the LEDGER to vouch for it.

    ``expect_sha256`` (when given) must agree too — a caller-supplied
    value is accepted as a cross-check, never as the authority.
    """
    if not verdict_path.is_file():
        raise PromotionBindingError(
            f"verdict file not found: {verdict_path}; refusing.")
    actual = _sha256_file(verdict_path)
    recorded = ledger_verdict_sha256(ledger_path, entry_id=entry_id)
    if actual != recorded:
        raise PromotionBindingError(
            f"verdict {verdict_path} digests to {actual} but ledger entry "
            f"{entry_id} records {recorded} — this is not the adjudication "
            "the campaign signed; refusing.")
    if expect_sha256 is not None:
        want = expect_sha256.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", want):
            raise PromotionBindingError(
                f"--expect-verdict-sha256 {expect_sha256!r} is not a 64-hex "
                "sha256; refusing.")
        if want != recorded:
            raise PromotionBindingError(
                f"the supplied digest {want} disagrees with ledger entry "
                f"{entry_id}'s {recorded}; refusing.")
    return actual


def load_promotion_provenance(pool_dir: Path) -> dict[str, Any]:
    """Read a promoted bundle's sidecar, or refuse."""
    sidecar = Path(pool_dir) / PROMOTION_PROVENANCE_FILENAME
    if not sidecar.is_file():
        raise PromotionBindingError(
            f"{sidecar} not found — a bundle without its promotion "
            "provenance cannot be tied to any adjudication, so a run bound "
            "to it could not be adjudicated as the registered variant; "
            "point the bind at a bundle produced by "
            "scripts/research/pv_incremental_promote_representative.py; "
            "refusing.")
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise PromotionBindingError(f"{sidecar} is not a JSON object; refusing.")
    return data


def verify_promoted_bundle(
    pool_dir: Path, ledger_path: Path, *, entry_id: str,
    representative_entry_id: str = REPRESENTATIVE_LEDGER_ENTRY,
) -> dict[str, str]:
    """Verify a bound pool IS the ledger-authorised, PRE-REGISTERED one.

    Checks, in order: the sidecar exists; the verdict it claims is the
    one the ledger records; BOTH pool artifacts on disk are the bytes
    the sidecar was written for; and the candidate it carries is the
    representative the ledger pre-registered as the treatment variant.
    Returns the identity fields worth stamping into run provenance so
    adjudication can fail closed on them.
    """
    pool_dir = Path(pool_dir)
    prov = load_promotion_provenance(pool_dir)
    claimed_verdict = str(prov.get("fwer_verdict_sha256") or "")
    recorded = ledger_verdict_sha256(ledger_path, entry_id=entry_id)
    if claimed_verdict != recorded:
        raise PromotionBindingError(
            f"the bundle at {pool_dir} was promoted under verdict "
            f"{claimed_verdict or '<none>'} but ledger entry {entry_id} "
            f"records {recorded} — binding it would let an unadjudicated "
            "pool be reported as the registered variant; refusing.")
    # BOTH artifacts, not just the parquet (codex #422 r3): FactorPool
    # takes its EXECUTABLE ast from factor_expressions.json and does not
    # cross-check it against the persisted (randomised) expr_hash, so a
    # swapped JSON alone would evaluate a different expression while the
    # parquet digest and the stamped provenance still looked right.
    digests: dict[str, str] = {}
    for filename, claimed_key in (
            ("factor_pool.parquet", "promoted_pool_sha256"),
            ("factor_expressions.json", "promoted_expressions_sha256")):
        path = pool_dir / filename
        if not path.is_file():
            raise PromotionBindingError(
                f"{path} not found — the bundle is incomplete; refusing.")
        actual = _sha256_file(path)
        claimed = str(prov.get(claimed_key) or "")
        if not claimed:
            raise PromotionBindingError(
                f"{pool_dir / PROMOTION_PROVENANCE_FILENAME} records no "
                f"{claimed_key} — {filename} cannot be shown to be the "
                "promoted bytes; re-run the promotion tool; refusing.")
        if actual != claimed:
            raise PromotionBindingError(
                f"{path} digests to {actual} but its provenance records "
                f"{claimed} — the bundle changed after promotion; refusing.")
        digests[filename] = actual
    actual_pool = digests["factor_pool.parquet"]
    candidate_id = str(prov.get("candidate_id") or "")
    expression = str(prov.get("expression") or "")
    if not candidate_id or not expression:
        raise PromotionBindingError(
            f"{pool_dir / PROMOTION_PROVENANCE_FILENAME} records no "
            "candidate_id/expression — the bound factor cannot be named in "
            "run provenance; refusing.")
    # E007 says who MAY be promoted (any of 50 survivors); the
    # pre-registration says who WAS registered as this comparison's
    # treatment. The ruler judges the plan's single variant name, so a
    # different survivor would collect a decision-grade verdict under
    # another candidate's registered identity (codex #422 r3).
    registered = ledger_representative(
        ledger_path, entry_id=representative_entry_id)
    if (candidate_id, expression) != (registered["candidate_id"],
                                      registered["expression"]):
        raise PromotionBindingError(
            f"the bundle at {pool_dir} carries {candidate_id} "
            f"({expression!r}) but ledger entry {representative_entry_id} "
            f"pre-registered {registered['candidate_id']} "
            f"({registered['expression']!r}) as the treatment variant — "
            "binding it would earn a decision-grade verdict under another "
            "candidate's registered name; refusing.")
    return {
        "candidate_id": candidate_id,
        "expression": expression,
        "pool_sha256": actual_pool,
        "expressions_sha256": digests["factor_expressions.json"],
        "fwer_verdict_sha256": recorded,
        "ledger_entry": entry_id,
        "representative_ledger_entry": representative_entry_id,
    }


def pool_identity_string(identity: dict[str, str]) -> str:
    """Compact, greppable identity for the walk-forward run report.

    The run report is what the ruler and any later auditor read; without
    this the report would say only ``feature_handler:
    "Alpha158PlusMined"`` and a decision-grade verdict could be issued
    for an unprovable input (codex #422 r2).
    """
    return (
        f"{identity['candidate_id']}"
        f"|expr={identity['expression']}"
        f"|pool_sha256={identity['pool_sha256']}"
        f"|expressions_sha256={identity['expressions_sha256']}"
        f"|verdict_sha256={identity['fwer_verdict_sha256']}"
        f"|ledger={identity['ledger_entry']}"
        f"+{identity['representative_ledger_entry']}"
    )
