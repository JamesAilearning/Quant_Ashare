"""CSI800 cadence-campaign CERTIFY step — the sole promotion authority.

attach (working tree) never mints eligibility (#374 r4): its in-artifact
``promotion_eligible`` is always false. THIS tool grants it, and only in
the combined form the spec requires — a verdict SIDECAR whose inputs are
all read THROUGH mainline anchors:

1. every input (pair v3, the N5 three-run source evidence, the N1 v2
   pair + N1 source fold reports) is read via ``git show <anchor>:<path>``
   where ``<anchor>`` is the current ``origin/main`` tip — working-tree
   or feature-branch bytes can never certify (#374 r3/r6/r9/r10/r14);
2. the anchored evidence is MATERIALIZED into a temp directory and the
   attach computation re-runs on it end-to-end (bindings, digest chain,
   five vetoes) — the anchored pair's stored checklist must equal the
   recomputation byte-for-value (#374 r7: downstream never trusts a
   sidecar/artifact assertion it can recompute);
3. the digest chain must be COMPLETE: every fold report of all three N5
   runs must carry ``positions_sha256`` (PR-A producer) and every
   anchored positions series must hash to it — this is what
   ``producer_digest_certified`` means;
4. the pre-registered primary criteria are evaluated
   (conservative-to-conservative, DP-3): N5 conservative net > 0 AND
   N5 gross retention >= 50% of the N1 gross (N1 read ONLY from the
   anchored N1 pair pins + anchored N1 source fold reports), with the
   <=5% base/conservative gross-divergence guard on BOTH pairs;
5. only if EVERYTHING passes is the verdict sidecar written (it never
   rewrites any anchored artifact, #374 r4); the sidecar records the
   pair/evidence/N1 anchor commit ids and is itself then committed and
   reviewed — ``--verify`` re-runs this whole computation from a
   sidecar's recorded anchors and compares (deterministic; no
   timestamps in the sidecar).

LOSE / veto-triggered / incomplete-chain inputs REFUSE without writing a
sidecar: a losing campaign is archived through the brief + pair
artifact, not through a verdict sidecar.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.research.csi800_campaign_attach_vetoes import (  # noqa: E402
    PROMOTION_QUALIFYING_REF_BINDING,
    attach,
)

MAINLINE_REF = "origin/main"
SIDECAR_SCHEMA = "csi800_cadence_verdict_v1"
# DP-3 pre-registered numbers (governance-pinned in PR-C):
NET_MIN = 0.0                 # conservative net must be strictly above
GROSS_RETENTION_MIN = 0.50    # N5 gross >= 50% of N1 gross
ARM_DIVERGENCE_MAX = 0.05     # base-vs-conservative gross sanity guard


class CertifyError(SystemExit):
    """Loud refusal — no sidecar is written."""

    def __init__(self, message: str) -> None:
        super().__init__(f"CERTIFY REFUSED: {message}")


def _git(repo: Path, *args: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise CertifyError(
            f"git {' '.join(args)} failed: "
            f"{proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout


def _anchor_commit(repo: Path) -> str:
    return _git(repo, "rev-parse", MAINLINE_REF).decode("ascii").strip()


def _require_mainline_reachable(repo: Path, commit: str) -> None:
    proc = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor",
         commit, MAINLINE_REF],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise CertifyError(
            f"anchor {commit} is not reachable from {MAINLINE_REF} — "
            "unanchored evidence cannot certify.")


def _show(repo: Path, anchor: str, relpath: str) -> bytes:
    return _git(repo, "show", f"{anchor}:{PurePosixPath(relpath)}")


def _ls_tree(repo: Path, anchor: str, reldir: str) -> list[str]:
    out = _git(repo, "ls-tree", "-r", "--name-only", anchor,
               str(PurePosixPath(reldir)))
    files = [line for line in out.decode("utf-8").splitlines() if line]
    if not files:
        raise CertifyError(
            f"no files under {reldir!r} at anchor — the evidence must be "
            "merged to the mainline BEFORE certification (#374 r10).")
    return files


def _materialize_dir(repo: Path, anchor: str, reldir: str,
                     dest: Path) -> None:
    for rel in _ls_tree(repo, anchor, reldir):
        target = dest / PurePosixPath(rel).relative_to(
            PurePosixPath(reldir))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_show(repo, anchor, rel))


def _mean(values: list[float], label: str) -> float:
    if not values:
        raise CertifyError(f"{label}: empty gross series.")
    return sum(values) / len(values)


def _arm_divergence(base_mean: float, cons_mean: float) -> float:
    denom = max(abs(base_mean), abs(cons_mean))
    return 0.0 if denom == 0 else abs(base_mean - cons_mean) / denom


def _n1_gross_means(repo: Path, anchor: str, n1_pair_path: str,
                    n1_evidence_dir: str) -> dict[str, float]:
    """N1 gross means read ONLY from the anchored N1 pair's pinned fold
    hashes + the anchored N1 source fold reports (#374 r1/r8/r14)."""
    n1_pair = json.loads(_show(repo, anchor, n1_pair_path))
    means: dict[str, float] = {}
    for side in ("base", "conservative"):
        pinned: dict[str, str] = n1_pair[side]["fold_report_sha256"]
        num_folds = n1_pair[side]["num_folds"]
        # exact expected key set — alias keys like "0"/"00" must not
        # satisfy the count while omitting a fold (codex #376 r6 P2).
        expected_keys = {str(i) for i in range(num_folds)}
        if set(pinned) != expected_keys:
            raise CertifyError(
                f"N1 {side}: pinned fold keys {sorted(pinned)} != "
                f"expected exact set 0..{num_folds - 1} — incomplete or "
                "aliased baseline, refusing.")
        gross: list[float] = []
        for idx_s, expected in sorted(pinned.items()):
            rel = (f"{n1_evidence_dir}/{side}/"
                   f"fold_{int(idx_s):02d}_report.json")
            raw = _show(repo, anchor, rel)
            actual = hashlib.sha256(raw).hexdigest()
            if actual != expected:
                raise CertifyError(
                    f"N1 {side} fold {idx_s}: anchored source report "
                    f"sha256 {actual} != N1-pair-pinned {expected} — "
                    "baseline evidence broken.")
            payload = json.loads(raw)
            value = float(
                payload["backtest"]["risk_analysis"]
                ["excess_return_without_cost"]["annualized_return"])
            # NaN/Infinity survive json.loads and make every threshold
            # comparison False (codex #376 r2) — fail closed instead of
            # minting a NaN retention.
            if not math.isfinite(value):
                raise CertifyError(
                    f"N1 {side} fold {idx_s}: gross value {value!r} is "
                    "non-finite — malformed baseline evidence, refusing.")
            gross.append(value)
        means[side] = _mean(gross, f"N1 {side}")
    return means


def _digest_chain_complete(run_dir: Path, expected_folds: int) -> None:
    """certified grade requires EVERY DECLARED fold to carry the
    producer attestation digest — counting only the files that exist
    would let a run with failed/absent folds read as complete (codex
    #376 r6 P1)."""
    reports = sorted(run_dir.glob("fold_*_report.json"))
    if len(reports) != expected_folds:
        raise CertifyError(
            f"{run_dir}: {len(reports)}/{expected_folds} fold reports "
            "materialized — promotion-grade coverage requires the full "
            "declared fold set, refusing.")
    for rep_p in reports:
        payload = json.loads(rep_p.read_text(encoding="utf-8"))
        if not payload.get("positions_sha256"):
            raise CertifyError(
                f"{rep_p.name}: fold report carries no positions_sha256 "
                "— the digest chain is incomplete; only PR-A attestation "
                "producers can reach producer_digest_certified.")


def certify(repo: Path, pair_path: str, base_dir: str, cons_dir: str,
            ref_dir: str, n1_pair_path: str, n1_evidence_dir: str,
            out_path: Path, anchor: str | None = None) -> dict[str, Any]:
    """Run the full certification from mainline-anchored bytes; write
    the verdict sidecar to ``out_path`` ONLY if everything passes."""
    if anchor is None:
        anchor = _anchor_commit(repo)
    _require_mainline_reachable(repo, anchor)

    anchored_pair = json.loads(_show(repo, anchor, pair_path))
    pair_sha = hashlib.sha256(_show(repo, anchor, pair_path)).hexdigest()
    n1_pair_sha = hashlib.sha256(
        _show(repo, anchor, n1_pair_path)).hexdigest()

    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        # Materialize the anchored bytes and RE-RUN the attach
        # computation end-to-end on them (#374 r7: recompute, never
        # trust assertions). Directory names must equal the certified
        # run_ids (attach binds run_id == dir name).
        # Promotion-grade coverage: a reference with documented FAILED
        # folds cannot certify — the "all three runs, all folds" digest
        # claim would be hollow (codex #376 r6 P1). Vetoed/diagnostic
        # archiving of such campaigns stays available via the brief.
        if anchored_pair["reference"].get("ref_failed_folds"):
            raise CertifyError(
                "reference run has documented failed folds "
                f"{anchored_pair['reference']['ref_failed_folds']} — "
                "promotion-grade certification requires a fully "
                "official reference; refusing.")
        runs: dict[str, Path] = {}
        for key, reldir in (("base", base_dir),
                            ("conservative", cons_dir),
                            ("reference", ref_dir)):
            run_id = anchored_pair[key]["run_id"]
            dest = tmp / run_id
            _materialize_dir(repo, anchor, reldir, dest)
            runs[key] = dest
            _digest_chain_complete(
                dest, int(anchored_pair[key]["num_folds"]))
        tmp_pair = tmp / "pair.json"
        tmp_pair.write_bytes(_show(repo, anchor, pair_path))
        recomputed = attach(tmp_pair, runs["base"], runs["conservative"],
                            runs["reference"])
        # N5 gross RE-DERIVED from the anchored (hash-verified) fold
        # reports — never trusted from the pair JSON fields (codex #376
        # r1: an edited per_fold_gross_annualized series could otherwise
        # pass the 50% retention with the sources showing a collapse).
        # attach just re-proved every fold report hashes to the pair's
        # pinned digest, so these materialized bytes ARE the certified
        # sources.
        gross_series: dict[str, list[float]] = {}
        for key in ("base", "conservative"):
            series: list[float] = []
            pinned = anchored_pair[key]["fold_report_sha256"]
            # exact key set, same rule as the N1 side (codex #376 r6+r7):
            # alias keys ("0"/"00") would double-count a favorable fold
            # while the materialized-file count still matches.
            n_folds = int(anchored_pair[key]["num_folds"])
            if set(pinned) != {str(i) for i in range(n_folds)}:
                raise CertifyError(
                    f"N5 {key}: pinned fold keys {sorted(pinned)} != "
                    f"expected exact set 0..{n_folds - 1} — aliased or "
                    "incomplete pair map, refusing.")
            for idx_s in sorted(pinned, key=int):
                rep_p = (runs[key]
                         / f"fold_{int(idx_s):02d}_report.json")
                payload = json.loads(rep_p.read_text(encoding="utf-8"))
                value = float(
                    payload["backtest"]["risk_analysis"]
                    ["excess_return_without_cost"]["annualized_return"])
                # same fail-closed rule as the N1 path (codex #376 r4):
                # a consistently-edited pair could carry inf==inf past
                # the equality check while NaN divergence bypasses the
                # guard.
                if not math.isfinite(value):
                    raise CertifyError(
                        f"N5 {key} fold {idx_s}: gross value {value!r} "
                        "is non-finite — malformed evidence, refusing.")
                series.append(value)
            stored = [float(x) for x in
                      anchored_pair[key]["per_fold_gross_annualized"]]
            if series != stored:
                raise CertifyError(
                    f"N5 {key}: pair-stored per_fold_gross_annualized "
                    "does not equal the series re-derived from the "
                    "anchored fold reports — the pair's gross fields "
                    "were edited; refusing.")
            gross_series[key] = series

    if recomputed["veto_checklist"] != anchored_pair.get("veto_checklist"):
        raise CertifyError(
            "recomputed veto checklist differs from the anchored pair "
            "artifact's stored checklist — the committed claims do not "
            "reproduce from the anchored evidence.")
    if not recomputed.get("checks_all_pass"):
        triggered = [k for k, v in recomputed["veto_checklist"].items()
                     if isinstance(v, dict) and v.get("veto_triggered")]
        raise CertifyError(
            f"veto checklist does not pass (triggered={triggered}, "
            f"incomplete={recomputed.get('incomplete_checks')}) — a "
            "vetoed campaign is archived via the brief, not certified.")

    # DP-3 primary criteria (conservative-to-conservative, #374 r7 P2).
    cons_net = float(recomputed["veto_checklist"]
                     ["1_conservative_net_excess"]["value_annualized"])
    if not cons_net > NET_MIN:
        raise CertifyError(
            f"primary criterion 1 fails: conservative net {cons_net!r} "
            f"is not > {NET_MIN} — LOSE; no sidecar.")
    n5_base_mean = _mean(gross_series["base"], "N5 base")
    n5_cons_mean = _mean(gross_series["conservative"], "N5 conservative")
    n1_means = _n1_gross_means(repo, anchor, n1_pair_path,
                               n1_evidence_dir)
    for label, div in (
        ("N5", _arm_divergence(n5_base_mean, n5_cons_mean)),
        ("N1", _arm_divergence(n1_means["base"],
                               n1_means["conservative"])),
    ):
        if div > ARM_DIVERGENCE_MAX:
            raise CertifyError(
                f"{label} base/conservative gross means diverge "
                f"{div:.4f} > {ARM_DIVERGENCE_MAX} — gross is "
                "cost-independent; divergence is evidence anomaly "
                "(fail closed).")
    if n1_means["conservative"] <= 0:
        raise CertifyError(
            "N1 conservative gross mean is not positive — the 50% "
            "retention ratio is ill-defined; refusing.")
    retention = n5_cons_mean / n1_means["conservative"]
    if retention < GROSS_RETENTION_MIN:
        raise CertifyError(
            f"primary criterion 2 fails: gross retention {retention:.4f}"
            f" < {GROSS_RETENTION_MIN} — cost savings bought by killing "
            "alpha; LOSE, no sidecar.")

    sidecar: dict[str, Any] = {
        "schema_version": SIDECAR_SCHEMA,
        "anchors": {
            "pair_anchor": anchor,
            "evidence_anchor": anchor,
            "n1_anchor": anchor,
            "mainline_ref": MAINLINE_REF,
        },
        "inputs": {
            "pair_path": pair_path, "pair_sha256": pair_sha,
            "base_evidence": base_dir, "conservative_evidence": cons_dir,
            "reference_evidence": ref_dir,
            "n1_pair_path": n1_pair_path, "n1_pair_sha256": n1_pair_sha,
            "n1_evidence_dir": n1_evidence_dir,
        },
        "verdict": {
            "reference_content_binding": PROMOTION_QUALIFYING_REF_BINDING,
            "promotion_eligible": True,
            "conservative_net_annualized": cons_net,
            "n5_gross_mean": {"base": n5_base_mean,
                              "conservative": n5_cons_mean},
            "n1_gross_mean": n1_means,
            "gross_retention": retention,
            "thresholds": {"net_min": NET_MIN,
                           "gross_retention_min": GROSS_RETENTION_MIN,
                           "arm_divergence_max": ARM_DIVERGENCE_MAX},
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    print("verdict sidecar written: "
          f"{out_path} (anchor {anchor[:12]}, retention {retention:.4f})")
    print("sidecar sha256 (compare against committed bytes): "
          + hashlib.sha256(out_path.read_bytes()).hexdigest())
    return sidecar


def verify(repo: Path, sidecar_relpath: str) -> None:
    """Downstream re-verification (#374 r7): the sidecar itself is read
    THROUGH the mainline anchor (codex #376 r5 — an unmerged local
    sidecar must never verify as a promotion verdict; the sidecar
    review/merge step is part of the required sequence), then the whole
    certification is recomputed from its recorded anchors and the
    produced verdict must equal the anchored bytes — never trust
    assertions."""
    tip = _anchor_commit(repo)
    stored = json.loads(_show(repo, tip, sidecar_relpath))
    anchor = stored["anchors"]["pair_anchor"]
    inputs = stored["inputs"]
    with tempfile.TemporaryDirectory() as t:
        out = Path(t) / "recomputed_sidecar.json"
        recomputed = certify(
            repo, inputs["pair_path"], inputs["base_evidence"],
            inputs["conservative_evidence"], inputs["reference_evidence"],
            inputs["n1_pair_path"], inputs["n1_evidence_dir"],
            out, anchor=anchor)
    if recomputed != stored:
        raise CertifyError(
            "sidecar contents do not reproduce from its recorded "
            "anchors — broken chain, promotion verdict invalid.")
    print(f"sidecar verified OK against anchor {anchor[:12]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", type=Path, default=_REPO_ROOT)
    ap.add_argument("--pair", default="docs/research/"
                    "csi800_cadence_pair_report.json")
    ap.add_argument("--base-evidence", default="docs/research/evidence/"
                    "csi800_n5_runs/csi800_cadence5_base")
    ap.add_argument("--conservative-evidence", default="docs/research/"
                    "evidence/csi800_n5_runs/csi800_cadence5_conservative")
    ap.add_argument("--reference-evidence", default="docs/research/"
                    "evidence/csi800_n5_runs/csi300_cadence5_reference")
    ap.add_argument("--n1-pair", default="docs/research/"
                    "csi800_campaign_pair_report.json")
    ap.add_argument("--n1-evidence", default="docs/research/evidence/"
                    "csi800_n1_folds")
    ap.add_argument("--out", type=Path, default=Path("docs/research/"
                    "csi800_cadence_verdict.json"))
    ap.add_argument("--verify", default=None,
                    help="repo-relative path of a MERGED sidecar to "
                         "verify (read through the mainline anchor) "
                         "instead of certifying")
    args = ap.parse_args()
    if args.verify is not None:
        verify(args.repo, args.verify)
        return 0
    certify(args.repo, args.pair, args.base_evidence,
            args.conservative_evidence, args.reference_evidence,
            args.n1_pair, args.n1_evidence, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
