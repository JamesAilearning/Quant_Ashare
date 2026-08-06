"""pv_incremental_v1 candidate registration (GP pool → OOS manifest).

Turns a COMPLETED GP mining run into the registered batch manifest the
OOS evaluator and the FWER adjudicator both consume:

* ``<out>/candidates.json`` — the manifest itself, the exact array
  shape those two tools read: ``[{candidate_id, expression,
  orientation}, ...]``;
* ``<out>/candidates.json.provenance.json`` — which GP run produced
  it, under which config, with which pool digests.

Two disciplines this enforces rather than assumes:

* **the manifest is the registration** — once written it is what the
  OOS run is adjudicated against, so ids must be stable and unique,
  expressions verbatim from the pool, and the IS orientation carried
  through (a sign-blind breeding criterion + a one-sided positive OOS
  threshold means an un-oriented candidate is tested backwards);
* **self-verification before writing** — the manifest is run through
  the evaluator's own ``preflight_candidates`` (seven-field whitelist,
  frozen-grammar parse, CSF/PURE root, id slug + uniqueness,
  orientation domain), so a manifest that the evaluator would refuse
  is never produced in the first place.

This script only READS the pool and WRITES a manifest; it does not
touch the evaluator or the adjudicator.

Ignition (operator): run the GP batch first, then this. Neither is
auto-run.
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


class PVRegisterError(RuntimeError):
    """Domain error: registration refuses."""


def load_frozen_plan(path: Path = PLAN_PATH) -> dict[str, Any]:
    """Load + verify the frozen plan (same discipline as the other
    two pieces): foreign protocol or an unblinded holdout refuses."""
    plan = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise PVRegisterError(f"frozen plan {path} is not a mapping.")
    if plan.get("protocol_id") != PROTOCOL_ID:
        raise PVRegisterError(
            f"frozen plan carries protocol_id "
            f"{plan.get('protocol_id')!r} — refusing.")
    if plan.get("holdout_unblinded") is not False:
        raise PVRegisterError(
            "frozen plan holdout_unblinded is not False — candidates "
            "must be registered under a BLINDED holdout; refusing.")
    return plan


def check_run_config(plan: dict[str, Any],
                     run_dir: Path) -> dict[str, Any]:
    """The GP run must be THIS campaign's run.

    ``run_mining`` dumps the resolved config next to the pool. A run
    bred on a different universe, window, field set or fitness
    criterion produces candidates that were never selected under the
    frozen protocol — registering them would submit trials the
    pre-registration does not cover.
    """
    cfg_path = run_dir / "config.yaml"
    if not cfg_path.is_file():
        raise PVRegisterError(
            f"{cfg_path} not found — point --run-dir at a COMPLETED "
            "miner run directory (it dumps the resolved config next "
            "to the pool); refusing.")
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise PVRegisterError(f"{cfg_path} is not a mapping; refusing.")
    data = cfg.get("data") or {}
    fit = cfg.get("fitness") or {}
    gp = cfg.get("gp") or {}
    w = plan["windows"]
    expected: dict[str, tuple[Any, Any]] = {
        # Every field that changes what was BRED (codex #402 r1):
        # matching universe/window strings are not enough — a
        # synthetic panel, a different holding horizon, another
        # baseline model or looser depth bounds all produce candidates
        # that were never selected under the frozen protocol.
        "mode": (data.get("mode"), "pit"),
        "forward_horizon": (data.get("forward_horizon"),
                            plan["metric"]["signal_to_execution_lag"]),
        "baseline_model": (data.get("baseline_model"),
                           plan["fitness"]["baseline"]["model"]),
        "gp.max_depth": (gp.get("max_depth"), plan["gp"]["max_depth"]),
        "gp.min_depth": (gp.get("min_depth"), plan["gp"]["min_depth"]),
        "universe_name": (data.get("universe_name"),
                          plan["universe"]["instruments"]),
        "start_date": (data.get("start_date"), w["is_start"]),
        "end_date": (data.get("end_date"), w["is_end"]),
        "forward_return_price": (data.get("forward_return_price"),
                                 "close"),
        "ic_term": (fit.get("ic_term"), plan["fitness"]["ic_term"]),
        "min_names_per_day": (fit.get("min_names_per_day"),
                              plan["metric"]["min_names_per_day"]),
        "w_complexity": (fit.get("w_complexity"),
                         plan["fitness"]["parsimony_lambda_per_node"]),
        "w_orthogonality": (
            fit.get("w_orthogonality"),
            plan["fitness"]["orthogonality"]["fitness_penalty_weight"]),
        "orthogonality_band": (
            fit.get("orthogonality_band"),
            plan["fitness"]["orthogonality"]["fitness_band_abs_rho"]),
    }
    drift = {k: {"run": got, "frozen": want}
             for k, (got, want) in expected.items() if got != want}
    if drift:
        raise PVRegisterError(
            f"the GP run's config does not match the frozen protocol: "
            f"{drift} — these candidates were not bred under the "
            "pre-registered criterion; refusing.")
    fields = [str(f) for f in (data.get("fields") or [])]
    want_fields = sorted(f"${f}" for f in plan["fields"])
    if sorted(fields) != want_fields:
        raise PVRegisterError(
            f"the GP run used fields {sorted(fields)} but the frozen "
            f"protocol admits exactly {want_fields} — refusing.")
    baseline_path = str(data.get("baseline_preds_path") or "")
    if not baseline_path:
        raise PVRegisterError(
            "the GP run carries no baseline_preds_path — it bred "
            "without the orthogonality penalty, so its candidates "
            "were not selected on the incremental criterion; "
            "refusing.")
    # Digest the INPUTS, not just their paths (codex #402 r1): a
    # pool or baseline replaced in place leaves identical
    # path-and-config provenance while neither the top-K selection
    # nor the incremental-fitness input can be reconstructed. The
    # module docstring promised pool digests; this makes it true.
    digests: dict[str, str] = {}
    for name in ("factor_pool.parquet", "factor_expressions.json"):
        f = run_dir / name
        if not f.is_file():
            raise PVRegisterError(
                f"{f} not found — the run directory is not a complete "
                "miner run; refusing.")
        digests[name] = hashlib.sha256(f.read_bytes()).hexdigest()
    baseline_file = Path(baseline_path)
    if baseline_file.is_file():
        digests["baseline_preds.parquet"] = hashlib.sha256(
            baseline_file.read_bytes()).hexdigest()
    else:
        # Recorded honestly rather than silently omitted: the baseline
        # may live on another machine by registration time, and the
        # reader must be able to tell "absent" from "unhashed".
        digests["baseline_preds.parquet"] = "ABSENT_AT_REGISTRATION"
    return {
        "gp_run_dir": str(run_dir),
        "gp_config_sha256": hashlib.sha256(
            cfg_path.read_bytes()).hexdigest(),
        "gp_baseline_preds_path": baseline_path,
        "gp_run_id": cfg.get("run_id"),
        "gp_input_sha256": digests,
    }


def assert_pool_records_orientation(run_dir: Path) -> None:
    """The POOL must have recorded the sign — never the loader default.

    ``FactorPool.load`` supplies ``orientation=1`` for parquets that
    predate the column (legacy compatibility, correct at that
    boundary). But a campaign pool without the column means the GP
    never recorded which way each factor points, and defaulting it
    would send every negative-IS candidate into the one-shot OOS run
    backwards (codex #402 r2). Check the SOURCE parquet, not the
    loaded objects.
    """
    import pandas as pd

    parquet = run_dir / "factor_pool.parquet"
    frame = pd.read_parquet(parquet)
    if "orientation" not in frame.columns:
        raise PVRegisterError(
            f"{parquet.name} has no `orientation` column — this pool "
            "predates orientation recording, so the GP's IS sign for "
            "each factor is unknown and the loader's +1 default would "
            "test negative candidates backwards; re-run the GP batch "
            "with the current engine; refusing.")
    if frame.empty:
        return
    values = frame["orientation"].tolist()
    bad = sorted({v for v in values
                  if isinstance(v, bool) or v not in (1, -1)})
    if bad:
        raise PVRegisterError(
            f"{parquet.name} carries orientation values {bad} outside "
            "the ±1 domain — corrupt pool; refusing.")


def candidate_id_for(index: int, expression: str) -> str:
    """Stable, safe, content-derived id.

    NOT derived from ``expr_hash``: the pool's own docs note it is
    Python's randomised ``hash()`` and is not comparable across
    processes. The rank prefix keeps a registered batch readable in
    fitness order; the content digest makes the same expression
    register under the same id on a re-run of the same pool.
    """
    digest = hashlib.sha256(expression.encode("utf-8")).hexdigest()[:8]
    return f"pv{index:03d}_{digest}"


def select_candidates(pool: Any, top_k: int) -> list[Any]:
    """Top-K pool entries by fitness, ties broken deterministically.

    Non-finite fitness (an invalid factor the GP scored ``-inf``) is
    excluded: registering it would spend a family slot — and therefore
    raise the FWER bar for every real trial — on something that was
    never a candidate.
    """
    import math

    entries = [e for e in pool.all_entries()
               if math.isfinite(e.fitness)]
    if not entries:
        raise PVRegisterError(
            "the pool holds no entry with finite fitness — nothing to "
            "register; refusing.")
    ordered = sorted(
        entries, key=lambda e: (-e.fitness, e.expr.to_qlib_string()))
    if top_k <= 0:
        raise PVRegisterError(f"--top-k must be >= 1, got {top_k}.")
    return ordered[:top_k]


def build_manifest(entries: list[Any]) -> list[dict[str, Any]]:
    """The array shape the evaluator and adjudicator read."""
    manifest: list[dict[str, Any]] = []
    for i, entry in enumerate(entries, start=1):
        # ``to_qlib_string`` is the PARSEABLE form (``str``/``repr``
        # give the AST constructor form, which the frozen grammar
        # cannot read) — the same serialization gp_engine records for
        # its own best-expression history.
        expression = entry.expr.to_qlib_string()
        orientation = int(getattr(entry, "orientation", 1))
        if orientation not in (1, -1):
            raise PVRegisterError(
                f"pool entry {expression!r} carries orientation "
                f"{orientation!r} — the pool must record the IS sign "
                "as +1/-1; refusing.")
        manifest.append({
            "candidate_id": candidate_id_for(i, expression),
            "expression": expression,
            "orientation": orientation,
        })
    ids = [c["candidate_id"] for c in manifest]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise PVRegisterError(
            f"generated duplicate candidate ids {dupes} — two pool "
            "entries share an expression; refusing.")
    return manifest


def selfcheck_against_evaluator(plan: dict[str, Any],
                                manifest: list[dict[str, Any]]) -> None:
    """Run the EVALUATOR's own preflight over the manifest.

    The registration is worthless if the tool that consumes it would
    refuse it, and discovering that at OOS time would burn the
    one-shot window. Reusing the evaluator's function (rather than
    re-implementing its rules) means the two cannot drift apart.
    """
    from scripts.research.pv_incremental_eval import (
        PVEvalError,
        preflight_candidates,
    )
    try:
        preflight_candidates(manifest, list(plan["fields"]))
    except PVEvalError as exc:
        raise PVRegisterError(
            f"the generated manifest would be REFUSED by the OOS "
            f"evaluator: {exc}") from exc


def ledger_entry(*, when: str, manifest_path: Path,
                 manifest_sha256: str, provenance: dict[str, Any],
                 n_candidates: int, pool_size: int) -> dict[str, Any]:
    """The append-only registration record (paired-entry discipline:
    this is the INTENT side; the OOS result gets its own entry)."""
    return {
        "when": when,
        "kind": "intent",
        "what": (
            f"候选注册（PV-DP-6）：GP run {provenance['gp_run_dir']} 的池"
            f"（{pool_size} 条）按 fitness 取前 {n_candidates} 条注册为"
            "OOS 决策批次；注册后清单冻结，OOS 评估一次性消费。"),
        "artifacts": [f"{manifest_path}#sha256={manifest_sha256}"],
        "gp_provenance": provenance,
        "numbers": {"pool_size": pool_size,
                    "registered": n_candidates},
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-dir", required=True,
                   help="Completed GP miner run directory.")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--top-k", type=int, default=50,
                   help="How many pool entries to register.")
    p.add_argument("--when", required=True,
                   help="ISO date for the ledger entry (explicit so "
                        "the record is reproducible).")
    args = p.parse_args(argv)

    try:
        from src.factor_mining.factor_pool import FactorPool

        plan = load_frozen_plan()
        run_dir = Path(args.run_dir)
        provenance = check_run_config(plan, run_dir)

        assert_pool_records_orientation(run_dir)
        pool = FactorPool.load(run_dir)
        pool_size = len(pool)
        entries = select_candidates(pool, args.top_k)
        manifest = build_manifest(entries)
        selfcheck_against_evaluator(plan, manifest)

        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = out_dir / "candidates.json"
        # EXCLUSIVE create, never check-then-write (codex #402
        # r1/r2): two registrars targeting one fresh dir could
        # both clear an exists() check and the second would
        # truncate the first's frozen registration. Same "x"
        # discipline as the evaluator's artifacts.
        try:
            with open(manifest_path, "x", encoding="utf-8") as fh:
                fh.write(json.dumps(manifest, indent=2,
                                    ensure_ascii=False)
                         + chr(10))
        except FileExistsError as exc:
            raise PVRegisterError(
                f"{manifest_path} already exists — a registration is "
                "frozen once written (re-registering under the same "
                "ids would let a new batch inherit an old batch's "
                "artifacts); use a fresh --out-dir; refusing."
            ) from exc
        manifest_sha = hashlib.sha256(
            manifest_path.read_bytes()).hexdigest()

        for key, value in (
                ("protocol_id", PROTOCOL_ID),
                ("manifest_sha256", manifest_sha),
                ("pool_size", pool_size),
                ("registered", len(manifest))):
            provenance[key] = value
        (out_dir / "candidates.json.provenance.json").write_text(
            json.dumps(provenance, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")

        entry = ledger_entry(
            when=args.when, manifest_path=manifest_path,
            manifest_sha256=manifest_sha, provenance=provenance,
            n_candidates=len(manifest), pool_size=pool_size)
        (out_dir / "ledger_entry.yaml").write_text(
            yaml.safe_dump([entry], sort_keys=False,
                           allow_unicode=True),
            encoding="utf-8")

        print(f"[pv-register] wrote {manifest_path}")
        print(f"[pv-register] pool_size={pool_size} "
              f"registered={len(manifest)} "
              f"negatively_oriented="
              f"{sum(1 for c in manifest if c['orientation'] == -1)}")
        print(f"[pv-register] manifest_sha256={manifest_sha}")
        print("[pv-register] append out-dir/ledger_entry.yaml to "
              "docs/prereg/pv_incremental_ledger.yaml (append-only) "
              "BEFORE running the OOS evaluator.")
    except PVRegisterError as exc:
        print(f"[pv-register] REFUSED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
