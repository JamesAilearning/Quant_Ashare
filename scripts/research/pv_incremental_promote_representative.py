"""pv_incremental_v1 PV-DP-7 step 2 — representative promotion bundle.

Extracts the ONE operator-chosen survivor from a completed GP run into a
single-entry factor pool that the ``Alpha158PlusMined`` handler binds for
the paired walk-forward comparison (steps 3-4).

The promotion criterion here is EXACTLY ONE thing: the candidate id
appears in the FWER verdict's survivor list. That verdict (ledger E007)
already applied the frozen dual threshold and the incremental
orthogonality band; re-deriving acceptance from pool metrics would put a
second, unsigned judge in the path.

This is why the module deliberately does NOT use
``src.factor_mining.promote`` — that is the v1/D4 manual-gate flow whose
``ValidationCriteria`` (``min_oos_ir 0.3`` and friends) would REJECT a
family the campaign's own pre-registered mechanism just certified. Both
paths stay intact; they simply never mix.

Identity discipline (why the matching works the way it does):

* the manifest's ``expression`` string is the registration's own record
  of what was adjudicated, so the pool entry is located by that string,
  NOT by ``expr_hash`` — the pool's hash is Python's randomised
  ``hash()`` and is not comparable across processes;
* the candidate id is RE-DERIVED with the registrar's own
  ``candidate_id_for`` and must reproduce the manifest's id, so a
  hand-edited manifest cannot smuggle a different expression under a
  surviving id;
* the verdict file's own sha256 must match the digest the COMMITTED
  ledger records for E007 — the authority is the ledger, never a value
  handed over by the same invocation that uses it, so a locally
  regenerated verdict (plus its own freshly computed sha) cannot
  authorize a promotion. ``--expect-verdict-sha256`` remains available
  as a cross-check and is verified against the ledger too.

Ignition (operator): after the FWER verdict is adjudicated and its
ledger entry is merged. Not auto-run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PROTOCOL_ID = "pv_incremental_v1"
PLAN_PATH = _REPO_ROOT / "docs" / "prereg" / "pv_incremental.yaml"
LEDGER_PATH = _REPO_ROOT / "docs" / "prereg" / "pv_incremental_ledger.yaml"
# The ledger entry whose recorded verdict digest authorises promotion.
VERDICT_LEDGER_ENTRY = "E007"


class PVPromoteError(RuntimeError):
    """Domain error: representative promotion refuses."""


def load_frozen_plan(path: Path = PLAN_PATH) -> dict[str, Any]:
    """Load + verify the frozen plan (same discipline as the rest of
    the campaign tooling): foreign protocol or an unblinded holdout
    refuses."""
    plan = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise PVPromoteError(f"frozen plan {path} is not a mapping.")
    if plan.get("protocol_id") != PROTOCOL_ID:
        raise PVPromoteError(
            f"frozen plan carries protocol_id {plan.get('protocol_id')!r} "
            "— refusing.")
    if plan.get("holdout_unblinded") is not False:
        raise PVPromoteError(
            "frozen plan holdout_unblinded is not False — the promotion "
            "bundle must be built under a BLINDED holdout (single-sided "
            "unblinding belongs to the operator's final signature step); "
            "refusing.")
    return plan


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_verdict(path: Path, expect_sha256: str | None,
                 *, ledger_path: Path = LEDGER_PATH,
                 entry_id: str = VERDICT_LEDGER_ENTRY) -> dict[str, Any]:
    """Load the FWER verdict, anchored to the COMMITTED ledger.

    The authority is the ledger entry's recorded digest, never a value
    from this invocation (codex #422 r2): an operator could otherwise
    regenerate an arbitrary ``survivors`` JSON, compute its own sha,
    pass both, and authorize the bundle exactly as before the pin
    existed. ``expect_sha256`` is still accepted and still checked —
    as a cross-check against the ledger, not as the source of truth.
    """
    from src.factor_mining.promotion_binding import (  # noqa: PLC0415
        PromotionBindingError,
        verify_verdict_against_ledger,
    )
    try:
        actual = verify_verdict_against_ledger(
            path, ledger_path, entry_id=entry_id,
            expect_sha256=expect_sha256)
    except PromotionBindingError as exc:
        raise PVPromoteError(str(exc)) from exc
    verdict = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(verdict, dict):
        raise PVPromoteError(f"{path} is not a JSON object; refusing.")
    if verdict.get("protocol_id") != PROTOCOL_ID:
        raise PVPromoteError(
            f"verdict carries protocol_id {verdict.get('protocol_id')!r} "
            "— refusing.")
    if verdict.get("verdict") != "survivors":
        raise PVPromoteError(
            f"verdict is {verdict.get('verdict')!r}, not 'survivors' — "
            "only a survivors adjudication opens the promotion gate; "
            "refusing.")
    survivors = verdict.get("survivors")
    if not isinstance(survivors, list) or not survivors:
        raise PVPromoteError(
            "verdict carries no survivor list; refusing.")
    verdict["_actual_sha256"] = actual
    return verdict


def select_registered_candidate(
    candidates_path: Path,
    candidate_id: str,
    survivors: list[Any],
    manifest_sha_in_verdict: str | None,
) -> dict[str, Any]:
    """Locate the operator-chosen candidate in the FROZEN manifest.

    Refuses unless the id survived, the manifest is the very one the
    adjudication consumed, and the id re-derives from its own
    expression under the registrar's rule.
    """
    if not candidates_path.is_file():
        raise PVPromoteError(
            f"candidates manifest not found: {candidates_path}; refusing.")
    manifest_sha = _sha256_file(candidates_path)
    if manifest_sha_in_verdict and manifest_sha != manifest_sha_in_verdict:
        raise PVPromoteError(
            f"manifest {candidates_path} digests to {manifest_sha} but the "
            f"verdict was adjudicated against {manifest_sha_in_verdict} — "
            "promoting from a different registration than the one that was "
            "judged; refusing.")
    manifest = json.loads(candidates_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, list):
        raise PVPromoteError(
            f"{candidates_path} is not the registered array shape; refusing.")
    if candidate_id not in {str(s) for s in survivors}:
        raise PVPromoteError(
            f"candidate {candidate_id!r} is not in the verdict's survivor "
            "list — survival under the frozen FWER mechanism is the ONLY "
            "promotion criterion; refusing.")
    matches = [c for c in manifest
               if isinstance(c, dict) and c.get("candidate_id") == candidate_id]
    if len(matches) != 1:
        raise PVPromoteError(
            f"candidate {candidate_id!r} appears {len(matches)} times in "
            f"{candidates_path} (expected exactly 1); refusing.")
    entry = matches[0]
    expression = entry.get("expression")
    orientation = entry.get("orientation")
    if not isinstance(expression, str) or not expression:
        raise PVPromoteError(
            f"candidate {candidate_id!r} carries no expression; refusing.")
    if isinstance(orientation, bool) or orientation not in (1, -1):
        raise PVPromoteError(
            f"candidate {candidate_id!r} carries orientation "
            f"{orientation!r} outside the ±1 domain; refusing.")
    # Re-derive the id from its own expression using the REGISTRAR's rule
    # (shared function, so the two cannot drift): a hand-edited manifest
    # that swaps an expression under a surviving id fails here.
    from scripts.research.pv_incremental_register_candidates import (
        candidate_id_for,
    )
    index_text = candidate_id.split("_", 1)[0].removeprefix("pv")
    if not index_text.isdigit():
        raise PVPromoteError(
            f"candidate id {candidate_id!r} does not carry the registrar's "
            "pvNNN_ prefix; refusing.")
    rederived = candidate_id_for(int(index_text), expression)
    if rederived != candidate_id:
        raise PVPromoteError(
            f"candidate id {candidate_id!r} does not match the id derived "
            f"from its own expression ({rederived!r}) — the manifest was "
            "edited after registration; refusing.")
    return {"candidate_id": candidate_id,
            "expression": expression,
            "orientation": int(orientation),
            "manifest_sha256": manifest_sha}


def locate_pool_entry(run_dir: Path, expression: str) -> Any:
    """Find the pool entry whose serialization IS the registered string.

    Matching is by ``to_qlib_string()`` — the same serialization the
    registrar wrote into the manifest — never by ``expr_hash``, which
    is Python's randomised ``hash()`` and differs across processes.
    """
    from src.factor_mining.factor_pool import FactorPool

    for name in ("factor_pool.parquet", "factor_expressions.json"):
        if not (run_dir / name).is_file():
            raise PVPromoteError(
                f"{run_dir / name} not found — point --gp-run-dir at the "
                "COMPLETED miner run that produced the registration; "
                "refusing.")
    pool = FactorPool.load(run_dir)
    matches = [e for e in pool.all_entries()
               if e.expr.to_qlib_string() == expression]
    if len(matches) != 1:
        raise PVPromoteError(
            f"expression {expression!r} matches {len(matches)} pool entries "
            f"in {run_dir} (expected exactly 1) — the run directory is not "
            "the one the registration was drawn from; refusing.")
    return matches[0]


def build_provenance(*, plan: dict[str, Any], run_dir: Path,
                     verdict: dict[str, Any], verdict_path: Path,
                     selection: dict[str, Any], entry: Any,
                     pool_sha256: str) -> dict[str, Any]:
    """Everything an auditor needs to rebuild this bundle's authority."""
    gp_inputs: dict[str, str] = {}
    for name in ("factor_pool.parquet", "factor_expressions.json"):
        gp_inputs[name] = _sha256_file(run_dir / name)
    cfg_path = run_dir / "config.yaml"
    return {
        "protocol_id": PROTOCOL_ID,
        "promotion_step": "phase6_handler",
        "criterion": "fwer_survivor",
        "candidate_id": selection["candidate_id"],
        "expression": selection["expression"],
        # Recorded, NOT applied to the feature values: the treatment arm
        # hands the raw factor to a tree model, whose split direction is
        # sign-agnostic, so negating would change nothing about what can
        # be learned. The evaluation side (E007) applied the IS sign to
        # its own signed-IC test; the two semantics stay separate.
        "orientation_is_recorded_not_applied": True,
        "orientation": selection["orientation"],
        "is_fitness": float(entry.fitness),
        "is_rank_ic_mean": float(entry.rank_ic_mean),
        "expr_size": int(entry.expr_size),
        "fwer_verdict_path": str(verdict_path),
        "fwer_verdict_sha256": verdict["_actual_sha256"],
        "registration_manifest_sha256": selection["manifest_sha256"],
        "gp_run_dir": str(run_dir),
        "gp_config_sha256": (
            _sha256_file(cfg_path) if cfg_path.is_file() else None),
        "gp_input_sha256": gp_inputs,
        "promoted_pool_sha256": pool_sha256,
        "plan_windows": plan["windows"],
    }


def main(argv: list[str] | None = None) -> int:
    from scripts.research.pv_incremental_console import make_console_safe
    make_console_safe()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--gp-run-dir", required=True,
                   help="Completed GP miner run directory.")
    p.add_argument("--candidates", required=True,
                   help="The FROZEN registration manifest (candidates.json).")
    p.add_argument("--verdict", required=True,
                   help="The FWER adjudication verdict json.")
    p.add_argument("--candidate-id", required=True,
                   help="Operator-chosen representative (must be a survivor).")
    p.add_argument("--expect-verdict-sha256", default=None,
                   help="Optional CROSS-CHECK against the ledger's recorded "
                        "digest. The authority is the committed ledger entry "
                        f"({VERDICT_LEDGER_ENTRY}), which is consulted "
                        "whether or not this is passed.")
    p.add_argument("--out-dir", required=True,
                   help="Destination for the single-entry promotion bundle.")
    args = p.parse_args(argv)

    try:
        from src.factor_mining.factor_pool import FactorPool

        plan = load_frozen_plan()
        run_dir = Path(args.gp_run_dir)
        verdict_path = Path(args.verdict)
        verdict = load_verdict(verdict_path, args.expect_verdict_sha256)
        selection = select_registered_candidate(
            Path(args.candidates), args.candidate_id,
            list(verdict["survivors"]),
            verdict.get("registration_manifest_sha256"),
        )
        entry = locate_pool_entry(run_dir, selection["expression"])

        out_dir = Path(args.out_dir)
        if out_dir.exists() and any(out_dir.iterdir()):
            raise PVPromoteError(
                f"{out_dir} already exists and is not empty — a promotion "
                "bundle is immutable once written (rebinding a handler to a "
                "silently-replaced pool is exactly what the cache identity "
                "exists to prevent); use a fresh --out-dir; refusing.")
        out_dir.mkdir(parents=True, exist_ok=True)
        bundle = FactorPool()
        bundle.add(entry)
        bundle.save(out_dir)
        pool_sha = _sha256_file(out_dir / "factor_pool.parquet")
        provenance = build_provenance(
            plan=plan, run_dir=run_dir, verdict=verdict,
            verdict_path=verdict_path, selection=selection, entry=entry,
            pool_sha256=pool_sha)
        (out_dir / "promotion_provenance.json").write_text(
            json.dumps(provenance, indent=2, ensure_ascii=False) + chr(10),
            encoding="utf-8", newline="")
    except PVPromoteError as exc:
        print(f"[pv-promote] REFUSED: {exc}", file=sys.stderr)
        return 2

    print(f"[pv-promote] wrote {out_dir / 'factor_pool.parquet'}")
    print(f"[pv-promote] candidate={selection['candidate_id']} "
          f"expr={selection['expression']} orientation="
          f"{selection['orientation']} (recorded, not applied)")
    print(f"[pv-promote] pool_sha256={pool_sha}")
    print("[pv-promote] bind this dir as the Alpha158PlusMined bundle for "
          "the paired treatment arm; register E008 BEFORE igniting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
