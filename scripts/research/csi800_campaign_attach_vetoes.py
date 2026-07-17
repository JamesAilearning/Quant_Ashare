"""Attach veto checks (2) (3) (4) (5) to a CSI800 paired campaign report.

Why this tool exists
--------------------
``csi800_campaign_pair_report.py`` (guard-1) proves the base/conservative
pairing and computes veto (1) (conservative net excess) from the official
metrics it already verifies. The remaining four checks of the
v2-csi800-expansion-guards veto table need evidence that lives OUTSIDE the
paired official metrics — sleeve attribution blocks, positions series, the
csi300 reference run, and per-fold risk-constraint provenance — so they are
attached by this second, equally deterministic step ("ignition tooling" in
the guard-1 wording). Each attached entry is a dict with ``veto_triggered``
plus the evidence values, matching ``REQUIRED_VETO_CHECKS`` /
``evaluate_promotion_eligibility`` semantics: promotion eligibility flips
only when ALL FIVE canonical checks are present and none triggered.

Evidence binding (codex #373 r1+r2 P1): the supplied ``--base-run`` /
``--conservative-run`` dirs are re-loaded with the SAME loader guard-1
used (re-proving per-fold official status) and their ``run_id`` +
``config_sha256`` + ``report_sha256`` must MATCH the certified entries in
the pair report — otherwise a caller could pair the certified official
metrics with turnover/sleeve/constraint evidence borrowed from a different
(favorable) run. Fold evidence for checks (2) (4) (5) is enumerated
through the certified aggregate's DECLARED ``folds[].report_path``
entries (confined resolver + fold_index ownership, guard-1's own) — never
by globbing the directory, so a stale/injected extra fold report can
neither satisfy coverage counts nor substitute a certified fold's
evidence — and each resolved fold report's BYTES must hash to the
``fold_report_sha256`` map the pair report pinned at generation time
(schema v2, codex r5): a fold report replaced post-pairing, even
self-consistently with its positions series, refuses. The ``--reference-run`` is bound structurally AND as a
documented run: its embedded config's projected diff against the
certified base config must be EXACTLY the #371-pinned reference fields
(``instruments``/``benchmark_code``/``attribution_sleeve_grouping``),
which also proves slippage parity — that is what "同配置 csi300 参照"
means — and every fold its aggregate documents as completed must resolve
to its own OFFICIAL fold report; a synthetic directory carrying only a
copied config and fabricated low-turnover positions is refused.

Fail-closed coverage (codex #373 r1 P1 x2): checks (2) and (5) require ok
sleeve attribution on EVERY conservative fold — a partial diagnostic
cannot certify a threshold was not crossed, so short coverage triggers the
veto instead of passing it. Check (3) requires a complete positions series
(>= 2 days) for EVERY fold of BOTH csi800 arms; a missing/empty/single-day
positions artifact triggers the veto. Reference folds may lack positions
ONLY where the aggregate documents the fold as failed (``report_path``
null — e.g. a max_per_name RAISE abort); an official reference fold with
missing positions refuses outright, and failed folds are disclosed via
``ref_failed_folds``.

Operationalization (numbers are spec-pinned, see
openspec/changes/2026-07-16-csi800-antiinflation-guards/specs/):

- (2) csi500 dependence: share = sum over folds of csi500_sleeve
  ``total_effect`` / sum over folds of ``sector_effects_sum`` from the
  CONSERVATIVE arm's fold attribution (gross, diagnostic layer). Trigger:
  share >= 0.80 AND conservative net excess <= 0 (the latter read from the
  already-attached check 1), OR incomplete attribution coverage.
- (3) turnover vs csi300 reference: one-way turnover recomputed from the
  persisted per-fold positions of BOTH the csi800 arm and the csi300
  reference with the SAME pure function (``sleeve_turnover`` with an empty
  sleeve map -> single bucket), so the two sides share one formula.
  daily mean = total_oneway / n_transitions pooled across folds;
  annualized = daily mean * 238 (A-share trading days; the veto is a
  RATIO so the constant cancels). Trigger: conservative arm daily mean
  > 1.5x reference daily mean, OR incomplete csi800 positions coverage,
  OR an unusable reference.
- (4) risk constraints recorded: every fold report of BOTH arms must
  carry ``backtest.provenance.config.risk_constraints`` exactly equal to
  campaign_risk_constraints_v1 (max_per_name 0.05, max_per_board 1.0,
  cash_buffer_min 0.0, max_leverage 1.0, mode raise) and both aggregate
  reports must pin ``risk_constraints_enabled: true`` +
  ``risk_constraints_calibration: campaign_v1``. Trigger: any absence or
  mismatch (unrecorded/retuned constraints invalidate the run).
- (5) midcap concentration: time-average (across ALL folds, coverage
  required) of the csi500 sleeve ``portfolio_weight`` > 0.75, or of the
  ``unknown`` bucket > 0.10, in the conservative arm's sleeve attribution.

Trust root (codex #373 r15)
---------------------------
The pair report lives on the SAME mutable filesystem as the run
artifacts: an actor with full write access can rewrite the evidence and
its pinned digests together — including this tool itself. No
local-filesystem scheme can authenticate against that; the trust anchor
for this evidence system is therefore the COMMITTED, REVIEWED pair
report in git (post-pairing edits are visible in history/diff), and the
attach step prints the final artifact's sha256 so operators/CI can
compare it out-of-band against the committed bytes. The binding chain
implemented here protects against accidental divergence and partial
tampering; machine-verifiable immutability requires the producer-side
certified digest that is ALREADY the structural promotion prerequisite
(``PROMOTION_QUALIFYING_REF_BINDING``, r10) — promotion can never be
minted from locally-editable evidence in the meantime, and consumers
must read the committed artifact, never a working-tree copy.

Usage::

    python scripts/research/csi800_campaign_attach_vetoes.py \
      --pair-report docs/research/csi800_campaign_pair_report.json \
      --base-run output/walk_forward/csi800_campaign_base \
      --conservative-run output/walk_forward/csi800_campaign_conservative \
      --reference-run output/walk_forward/csi300_campaign_reference
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# _load_side/_projected_diff/_resolve_fold_report are guard-1's own
# loader/projection/confined-resolver — reused deliberately so binding
# and pairing can never drift apart.
from scripts.research.csi800_campaign_pair_report import (  # noqa: E402
    BASE_SLIPPAGE_BPS,
    _load_side,
    _projected_diff,
    _require_finite_net,
    _resolve_fold_report,
    evaluate_promotion_eligibility,
)
from src.core.attribution_sleeve_loader import sleeve_turnover  # noqa: E402

# Spec-pinned campaign_v1 effective values (veto 4, option-A revision).
CAMPAIGN_V1_EXPECTED: dict[str, Any] = {
    "max_per_name": 0.05,
    "max_per_board": 1.0,
    "cash_buffer_min": 0.0,
    "max_leverage": 1.0,
    "mode": "raise",
}
# #371-pinned reference-vs-base projected config diff (exact key set) and
# reference identity — binds the veto-3 baseline to "同配置 csi300 参照".
REFERENCE_DIFF_FIELDS: frozenset[str] = frozenset(
    {"instruments", "benchmark_code", "attribution_sleeve_grouping"})
REFERENCE_UNIVERSE = "csi300"
REFERENCE_BENCHMARK = "SH000300TR"
# The ONLY reference binding level that may support promotion (codex
# #373 r10): a producer-stamped positions digest that is itself bound to
# an immutable/certified reference artifact. NO current producer emits
# one — reference fold reports are mutable and pinned nowhere, so a
# presence-based "embedded turnover" label is NOT authentication (the
# embedded block can be replaced together with the positions). Until the
# producer feature ships (backlog) and this tool gains its verification
# path, attach() can never emit promotion_eligible=true.
PROMOTION_QUALIFYING_REF_BINDING = "producer_digest_certified"
CSI500_DEPENDENCE_THRESHOLD = 0.80
TURNOVER_RATIO_THRESHOLD = 1.5
CSI500_WEIGHT_THRESHOLD = 0.75
UNKNOWN_WEIGHT_THRESHOLD = 0.10
# A-share trading-day annualization; cancels in the veto-3 ratio.
ANNUALIZATION_DAYS = 238

class AttachError(SystemExit):
    """Loud refusal — evidence binding or artifact integrity failed."""

    def __init__(self, message: str) -> None:
        super().__init__(f"REFUSING: {message}")


def _certified_fold_reports(
        run_dir: Path, aggregate: dict[str, Any],
        expected_hashes: dict[str, str],
) -> list[tuple[int, dict[str, Any]]]:
    """Fold evidence resolved through the certified aggregate's DECLARED
    ``folds[].report_path`` entries — never by globbing the directory
    (codex #373 r2 P1: a stale/injected extra ``fold_XX_report.json``
    must not be able to satisfy count-based coverage while a certified
    fold's evidence is absent or replaced). Every declared fold must
    resolve (confined to the run dir, guard-1's resolver), carry its own
    ``fold_index``, and its BYTES must hash to the pair report's pinned
    ``fold_report_sha256`` entry (codex #373 r5 P1: the aggregate stores
    only report_path, so without this pin a fold report could be
    replaced post-pairing together with its positions in a
    self-consistent way)."""
    out: list[tuple[int, dict[str, Any]]] = []
    seen_indices: set[int] = set()
    seen_paths: set[Path] = set()
    for entry in aggregate.get("folds") or []:
        idx = entry.get("fold_index")
        rp = entry.get("report_path")
        if rp is None:
            raise AttachError(
                f"{run_dir}: aggregate fold {idx!r} has no report_path — "
                "a certified paired side has no failed folds; torn "
                "aggregate, refusing.")
        resolved = _resolve_fold_report(run_dir, str(rp))
        # defense-in-depth mirror of guard-1's duplicate rejection
        # (codex #373 r7): one fold must not stand in for several.
        if int(idx) in seen_indices or resolved in seen_paths:
            raise AttachError(
                f"{run_dir}: duplicate fold entry (index {idx!r} / "
                f"{resolved}) in the aggregate — refusing.")
        seen_indices.add(int(idx))
        seen_paths.add(resolved)
        raw = resolved.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        pinned = expected_hashes.get(str(idx))
        if digest != pinned:
            raise AttachError(
                f"{resolved} sha256 {digest} != pair-report pinned "
                f"{pinned!r} for fold {idx!r} — fold evidence changed "
                "after pairing, refusing.")
        payload: dict[str, Any] = json.loads(raw.decode("utf-8"))
        if payload.get("fold_index") != idx:
            raise AttachError(
                f"{resolved} carries fold_index="
                f"{payload.get('fold_index')!r} but the aggregate entry "
                f"claims {idx!r} — mismatched fold evidence, refusing.")
        out.append((int(idx), payload))
    if not out:
        raise AttachError(f"no folds declared in aggregate under {run_dir}")
    return out


def _bind_paired_side(
        label: str, run_dir: Path, pair_entry: dict[str, Any],
) -> tuple[dict[str, Any], list[tuple[int, dict[str, Any]]]]:
    """Re-load ``run_dir`` with guard-1's loader (re-proving per-fold
    official status) and require identity with the certified pair-report
    entry — checks 2-5 must be computed from THE certified runs, not from
    whatever directory happens to be supplied. Returns the bound side and
    its certified fold-report payloads (aggregate-declared set)."""
    side = _load_side(run_dir)
    for key in ("run_id", "config_sha256", "report_sha256"):
        if side.get(key) != pair_entry.get(key):
            raise AttachError(
                f"--{label}-run {run_dir} does not match the certified "
                f"pair-report {label} entry: {key} "
                f"{side.get(key)!r} != {pair_entry.get(key)!r} — veto "
                "evidence must come from the paired runs themselves.")
    num_folds = side.get("num_folds")
    if not isinstance(num_folds, int) or num_folds <= 0:
        raise AttachError(
            f"--{label}-run {run_dir} is not a walk-forward campaign "
            "artifact (no num_folds) — the attach step only certifies "
            "the campaign shape.")
    expected_hashes = pair_entry.get("fold_report_sha256")
    if not isinstance(expected_hashes, dict) or not expected_hashes:
        raise AttachError(
            f"pair-report {label} entry carries no fold_report_sha256 "
            "map (pre-v2 artifact) — regenerate the pair report with the "
            "current guard-1 tool; unpinned fold evidence cannot be "
            "consumed.")
    # Parse the same bytes the certified hash covers, then enumerate the
    # DECLARED fold set through it.
    raw = (run_dir / "walk_forward_report.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != pair_entry.get("report_sha256"):
        raise AttachError(
            f"--{label}-run {run_dir} aggregate changed between binding "
            "reads — refusing.")
    aggregate: dict[str, Any] = json.loads(raw.decode("utf-8"))
    folds = _certified_fold_reports(run_dir, aggregate, expected_hashes)
    if len(folds) != num_folds:
        raise AttachError(
            f"--{label}-run {run_dir} declares num_folds={num_folds} but "
            f"{len(folds)} fold entries — torn aggregate, refusing.")
    return side, folds


def _bind_reference(
        ref_dir: Path, base_cfg: dict[str, Any],
) -> tuple[dict[str, Any], set[int], list[tuple[int, dict[str, Any]]]]:
    """Bind the veto-3 baseline. Structural binding: the reference's
    embedded config must differ from the certified base config by EXACTLY
    the #371-pinned reference fields (projection already excludes
    run-identity), which also proves slippage parity at 5 bps. Documented
    -run binding (codex #373 r2 P1): every fold the aggregate documents
    as completed must resolve to its own OFFICIAL fold report (confined,
    fold_index-owned) — a synthetic directory carrying only a copied
    config and low-turnover positions is refused; folds with
    ``report_path`` null are the documented failures and are returned
    for disclosure."""
    wf_p = ref_dir / "walk_forward_report.json"
    if not wf_p.is_file():
        raise AttachError(f"reference aggregate missing: {wf_p}")
    report: dict[str, Any] = json.loads(wf_p.read_text(encoding="utf-8"))
    cfg = report.get("config")
    if not isinstance(cfg, dict):
        raise AttachError(f"{wf_p} carries no embedded config mapping.")
    diff = _projected_diff(base_cfg, cfg)
    if set(diff) != REFERENCE_DIFF_FIELDS:
        raise AttachError(
            f"reference config diff vs certified base is {sorted(diff)} — "
            f"expected exactly {sorted(REFERENCE_DIFF_FIELDS)}; a "
            "reference that is not 同配置 cannot serve as the veto-3 "
            "turnover baseline.")
    if (cfg.get("instruments") != REFERENCE_UNIVERSE
            or cfg.get("benchmark_code") != REFERENCE_BENCHMARK
            or cfg.get("slippage_bps") != BASE_SLIPPAGE_BPS):
        raise AttachError(
            "reference identity mismatch: expected "
            f"{REFERENCE_UNIVERSE}/{REFERENCE_BENCHMARK}/"
            f"{BASE_SLIPPAGE_BPS}bps, got "
            f"{cfg.get('instruments')!r}/{cfg.get('benchmark_code')!r}/"
            f"{cfg.get('slippage_bps')!r}.")
    folds = report.get("folds") or []
    num_folds = report.get("num_folds")
    if not isinstance(num_folds, int) or len(folds) != num_folds:
        raise AttachError(
            f"{wf_p}: num_folds={num_folds!r} but {len(folds)} fold "
            "entries — torn aggregate, refusing.")
    failed: set[int] = set()
    payloads: list[tuple[int, dict[str, Any]]] = []
    seen_indices: set[int] = set()
    seen_paths: set[Path] = set()
    for entry in folds:
        idx = int(entry["fold_index"])
        # duplicate entries must refuse (codex #373 r8 P1: a repeated
        # high-turnover reference fold would be pooled twice, inflating
        # the veto-3 denominator; num_folds can be set to match, so the
        # length check alone does not catch it).
        if idx in seen_indices:
            raise AttachError(
                f"reference aggregate declares fold_index {idx} more "
                "than once — duplicate fold entries cannot anchor the "
                "veto-3 baseline, refusing.")
        seen_indices.add(idx)
        rp = entry.get("report_path")
        if rp is None:
            failed.add(idx)
            continue
        resolved = _resolve_fold_report(ref_dir, str(rp))
        if resolved in seen_paths:
            raise AttachError(
                f"reference aggregate declares report path {resolved} "
                "for more than one fold entry — refusing.")
        seen_paths.add(resolved)
        payload: dict[str, Any] = json.loads(
            resolved.read_text(encoding="utf-8"))
        if payload.get("fold_index") != idx:
            raise AttachError(
                f"reference {resolved} carries fold_index="
                f"{payload.get('fold_index')!r} but the aggregate entry "
                f"claims {idx} — mismatched fold evidence, refusing.")
        status = (payload.get("backtest") or {}).get("metric_status")
        if status != "official":
            raise AttachError(
                f"reference fold {idx} metric_status={status!r} yet the "
                "aggregate documents it as completed — not a documented "
                "reference run, refusing.")
        payloads.append((idx, payload))
    return report, failed, payloads


def _sleeve_rows(report: dict[str, Any]) -> dict[str, dict[str, float]]:
    att = report.get("attribution") or {}
    rows = att.get("sector_attribution") or []
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        sector = r["sector"]
        # duplicate sector rows must refuse (codex #373 r8 P1): a dict
        # comprehension would silently keep only the LAST row, letting a
        # trailing low-weight/effect row hide an earlier high csi500
        # contribution from vetoes 2/5. The producer emits exactly one
        # row per sector.
        if sector in out:
            raise AttachError(
                f"attribution carries sector {sector!r} more than once "
                f"(fold_index={report.get('fold_index')!r}) — duplicate "
                "sleeve rows cannot be evidence, refusing.")
        out[sector] = r
    return out


def compute_csi500_dependence(
        cons_folds: list[tuple[int, dict[str, Any]]],
        cons_net_excess: float, expected_folds: int) -> dict[str, Any]:
    effect_csi500 = 0.0
    effect_total = 0.0
    folds_used = 0
    for idx, rep in cons_folds:
        att = rep.get("attribution") or {}
        if att.get("status") != "ok":
            continue
        rows = _sleeve_rows(rep)
        # An "ok" attribution must actually CARRY the evidence (codex
        # #373 r16 P2): an omitted csi500 row / effects sum must refuse,
        # never read as a favorable 0.0.
        row500 = rows.get("csi500_sleeve")
        if (not isinstance(row500, dict) or "total_effect" not in row500
                or "sector_effects_sum" not in att):
            raise AttachError(
                f"fold {idx}: attribution status is ok but "
                "csi500_sleeve.total_effect / sector_effects_sum is "
                "absent — omitted producer fields cannot be read as "
                "favorable zeros, refusing.")
        # NaN/inf must refuse, not slide through comparisons (codex #373
        # r7 P1: ``nan >= 0.80`` is False, so corrupted evidence would
        # read as "not dependent").
        fold_csi500 = float(row500["total_effect"])
        fold_total = float(att["sector_effects_sum"])
        if not (math.isfinite(fold_csi500) and math.isfinite(fold_total)):
            raise AttachError(
                f"fold {idx}: non-finite attribution effects "
                f"(csi500={fold_csi500!r}, sum={fold_total!r}) — "
                "corrupted dependence evidence, refusing.")
        effect_csi500 += fold_csi500
        effect_total += fold_total
        folds_used += 1
    coverage_ok = folds_used == expected_folds
    share = ((effect_csi500 / effect_total)
             if coverage_ok and effect_total > 0 else None)
    dependent = share is not None and share >= CSI500_DEPENDENCE_THRESHOLD
    if not coverage_ok:
        note: str | None = (
            f"only {folds_used}/{expected_folds} folds carry ok sleeve "
            "attribution — partial diagnostics cannot certify the "
            "threshold; fail closed")
    elif share is None:
        note = ("share undefined (gross effect sum <= 0); dependence "
                "leg cannot trigger")
    else:
        note = None
    return {
        "csi500_effect_share_of_gross": share,
        "csi500_effect_sum": effect_csi500,
        "gross_effect_sum": effect_total,
        "folds_used": folds_used,
        "expected_folds": expected_folds,
        "conservative_net_excess": cons_net_excess,
        "threshold_share": CSI500_DEPENDENCE_THRESHOLD,
        "note": note,
        "veto_triggered": bool(
            not coverage_ok or (dependent and cons_net_excess <= 0.0)),
    }


def _resolve_run_artifact(run_dir: Path, path_str: str,
                          what: str) -> Path | None:
    """Resolve a fold-report-declared artifact path, CONFINED to the run
    dir (same discipline as guard-1's ``_resolve_fold_report``: evidence
    must not be borrowable from outside the claimed run). Returns None if
    no candidate exists (caller decides whether that is a coverage
    problem or torn evidence)."""
    run_root = run_dir.resolve()
    rp = Path(path_str)
    candidates = ((rp,) if rp.is_absolute() else (run_dir / rp, _REPO_ROOT / rp))
    for candidate in candidates:
        resolved = candidate.resolve()
        if not resolved.is_file():
            continue
        if not resolved.is_relative_to(run_root):
            raise AttachError(
                f"{what} {path_str!r} resolves OUTSIDE the claimed run "
                f"dir {run_dir} ({resolved}) — borrowed evidence, "
                "refusing.")
        return resolved
    return None


def _run_positions_turnover(
        run_dir: Path, fold_payloads: list[tuple[int, dict[str, Any]]],
        require_embedded_match: bool,
) -> tuple[dict[str, float], list[str]]:
    """One-way turnover pooled over the CERTIFIED fold set (codex #373
    r4 P1: positions are mutable and unauthenticated, so each series is
    bound to its certified fold report as far as the producer's artifacts
    allow):

    - the positions file is resolved via the fold report's DECLARED
      ``positions_path`` (confined to the run dir), never by naming
      convention;
    - the series must match the fold report's documented backtest window
      (``backtest.report``: exact ``positions_days`` count, dates inside
      ``start_date``/``end_date``) — mismatch is torn evidence, refuse;
    - with ``require_embedded_match`` (the csi800 arms, whose pinned
      config embeds a producer-computed ``sleeve_turnover`` block), the
      recomputed per-fold total must equal the embedded total — this is
      a CONTENT binding: a swapped series cannot reproduce it.

    The production reference has no embedded turnover (sleeve grouping
    off per the #371 pin) and the producer emits no positions content
    hash, so a fabricated same-window/same-day-count reference series is
    not detectable by recomputation — such UNAUTHENTICATED reference
    evidence may support a veto but never a promotion: attach() refuses
    to emit promotion_eligible=true on top of it (codex #373 r9), and
    closing the gap for promotions needs the producer to stamp a
    positions hash into reference fold reports (backlog).

    Returns ``(stats, problems)`` — a fold whose positions artifact is
    missing, non-mapping, or has < 2 dates is listed in ``problems`` and
    excluded from the pooled stats; the CALLER decides whether problems
    are fatal. Window/embedded-turnover mismatches refuse outright."""
    total = 0.0
    transitions = 0.0
    folds = 0
    problems: list[str] = []
    for idx, rep in fold_payloads:
        declared = rep.get("positions_path")
        if not isinstance(declared, str) or not declared:
            raise AttachError(
                f"{run_dir}: certified fold {idx} report declares no "
                "positions_path — producer schema mismatch, refusing.")
        resolved = _resolve_run_artifact(run_dir, declared,
                                         f"fold {idx} positions")
        if resolved is None:
            problems.append(f"fold {idx}: positions artifact missing")
            continue
        positions = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(positions, dict) or len(positions) < 2:
            problems.append(
                f"fold {idx}: positions empty or single-day — no "
                "transition to measure")
            continue
        bt_report = ((rep.get("backtest") or {}).get("report")) or {}
        pos_days = bt_report.get("positions_days")
        start = bt_report.get("start_date")
        end = bt_report.get("end_date")
        dates = sorted(positions)
        if (not isinstance(pos_days, int) or not isinstance(start, str)
                or not isinstance(end, str)):
            raise AttachError(
                f"{run_dir}: certified fold {idx} report carries no "
                "backtest.report window (positions_days/start_date/"
                "end_date) — cannot bind the positions series, refusing.")
        if (len(dates) != pos_days or dates[0] < start or dates[-1] > end):
            raise AttachError(
                f"{run_dir}: fold {idx} positions series ({len(dates)} "
                f"days, {dates[0]}..{dates[-1]}) does not match the "
                f"certified fold report window ({pos_days} days inside "
                f"{start}..{end}) — torn/replaced evidence, refusing.")
        # Every daily value must be a mapping of numeric weights BEFORE
        # sleeve_turnover touches it (codex #373 r14 P2: a null/list day
        # or a string weight would raise a raw TypeError instead of the
        # clean malformed-artifact refusal; NaN weights are caught by
        # the isfinite check on the fold total below).
        for day, holdings in positions.items():
            if not isinstance(holdings, dict):
                raise AttachError(
                    f"{run_dir}: fold {idx} positions for {day} is "
                    f"{type(holdings).__name__}, not a holdings mapping "
                    "— malformed evidence, refusing.")
            for inst, weight in holdings.items():
                if isinstance(weight, bool) or not isinstance(
                        weight, (int, float)):
                    raise AttachError(
                        f"{run_dir}: fold {idx} positions {day}/{inst} "
                        f"weight {weight!r} is not numeric — malformed "
                        "evidence, refusing.")
        block = sleeve_turnover(positions, {})  # single honest bucket
        if not block:
            # >=2 dates but every daily map empty: sleeve_turnover has
            # no bucket to report and next(iter(...)) below would raise
            # a bare StopIteration (codex #373 r13 P2) — refuse cleanly.
            raise AttachError(
                f"{run_dir}: fold {idx} positions series has "
                f"{len(dates)} dates but no holdings on any day — "
                "degenerate evidence cannot anchor a turnover check, "
                "refusing.")
        fold_total = sum(row["total_oneway"] for row in block.values())
        if not math.isfinite(fold_total):
            raise AttachError(
                f"{run_dir}: fold {idx} recomputed turnover is "
                f"{fold_total!r} — non-finite weights in the positions "
                "series (``nan > threshold`` is always False, so this "
                "must refuse, not pass); corrupted evidence.")
        if require_embedded_match:
            embedded = rep.get("sleeve_turnover")
            if not isinstance(embedded, dict) or not embedded:
                raise AttachError(
                    f"{run_dir}: certified fold {idx} report has no "
                    "embedded sleeve_turnover block — a campaign arm "
                    "must carry it (guard-2 pin), refusing.")
            embedded_total = sum(float(v["total_oneway"])
                                 for v in embedded.values())
            if not math.isclose(fold_total, embedded_total,
                                rel_tol=1e-9, abs_tol=1e-9):
                raise AttachError(
                    f"{run_dir}: fold {idx} recomputed one-way turnover "
                    f"{fold_total!r} does not match the certified fold "
                    f"report's embedded total {embedded_total!r} — the "
                    "positions series is not the one the run produced, "
                    "refusing.")
        total += fold_total
        # n_transitions is identical across buckets of one fold
        transitions += next(iter(block.values()))["n_transitions"]
        folds += 1
    daily = (total / transitions) if transitions else 0.0
    return ({"total_oneway": total, "n_transitions": transitions,
             "daily_mean_oneway": daily,
             "annualized_oneway": daily * ANNUALIZATION_DAYS,
             "valid_folds": float(folds)}, problems)


def compute_turnover_check(
        cons_dir: Path, base_dir: Path, ref_dir: Path,
        cons_payloads: list[tuple[int, dict[str, Any]]],
        base_payloads: list[tuple[int, dict[str, Any]]],
        ref_payloads: list[tuple[int, dict[str, Any]]],
        documented_failed: set[int]) -> dict[str, Any]:
    cons, cons_problems = _run_positions_turnover(
        cons_dir, cons_payloads, require_embedded_match=True)
    base, base_problems = _run_positions_turnover(
        base_dir, base_payloads, require_embedded_match=True)
    # A documented-failed fold must carry NO positions evidence at all —
    # a stale/injected series for a fold the aggregate aborted would
    # silently enter the reference turnover denominator and could
    # suppress veto 3 (codex #373 r3 P1).
    for idx in sorted(documented_failed):
        stale = ref_dir / f"fold_{idx:02d}_positions.json"
        if stale.exists():
            raise AttachError(
                f"reference fold {idx} is documented FAILED (report_path "
                f"null) yet carries a positions artifact ({stale}) — "
                "stale/injected evidence cannot enter the veto-3 "
                "baseline, refusing.")
    # Reference content binding (codex #373 r9+r10 P1): the producer
    # stamps no positions hash into reference fold reports AND the
    # reference fold reports are themselves mutable (pinned in no
    # certified artifact), so neither recomputation nor a
    # presence-based embedded-turnover label can authenticate the
    # series — positions and embedded turnover can be replaced TOGETHER.
    # When embedded turnover is present we still verify consistency (a
    # cheap tripwire against sloppy tampering), but the binding level
    # remains UNAUTHENTICATED: it may support a veto (withholding
    # promotion is the safe direction; tampering can only help an
    # attacker by making the check PASS), and attach() refuses to emit
    # promotion_eligible=true until a producer-stamped digest bound to a
    # certified reference artifact exists
    # (PROMOTION_QUALIFYING_REF_BINDING, backlog).
    embedded_present = all(
        isinstance(rep.get("sleeve_turnover"), dict)
        and rep.get("sleeve_turnover")
        for _i, rep in ref_payloads)
    ref, ref_problems = _run_positions_turnover(
        ref_dir, ref_payloads, require_embedded_match=embedded_present)

    # Every reference payload is a completed OFFICIAL fold (binding
    # proved it) — missing/unusable positions there is a torn artifact,
    # never a coverage note (documented-failed folds carry no payload).
    if ref_problems:
        raise AttachError(
            "reference torn positions evidence: " + "; ".join(ref_problems)
            + " — completed official folds must retain their positions "
            "series to anchor the veto-3 baseline.")

    coverage_problems = ([f"conservative: {p}" for p in cons_problems]
                         + [f"base: {p}" for p in base_problems])
    ratio = (cons["daily_mean_oneway"] / ref["daily_mean_oneway"]
             if ref["daily_mean_oneway"] > 0 else None)
    base_ratio = (base["daily_mean_oneway"] / ref["daily_mean_oneway"]
                  if ref["daily_mean_oneway"] > 0 else None)
    return {
        "conservative": cons,
        "base": base,
        "csi300_reference": ref,
        "conservative_over_reference_ratio": ratio,
        "base_over_reference_ratio": base_ratio,
        "threshold_ratio": TURNOVER_RATIO_THRESHOLD,
        "annualization_days": ANNUALIZATION_DAYS,
        "ref_valid_folds": ref["valid_folds"],
        "ref_failed_folds": sorted(documented_failed),
        "reference_content_binding": "window_only_unauthenticated",
        "reference_embedded_turnover_verified": embedded_present,
        # fail-closed: incomplete csi800 positions coverage or an
        # unusable reference cannot certify the check.
        "coverage_problems": coverage_problems,
        "veto_triggered": (True if coverage_problems or ratio is None
                           else bool(ratio > TURNOVER_RATIO_THRESHOLD)),
    }


def compute_constraints_check(
        base_side_cfg: dict[str, Any], cons_side_cfg: dict[str, Any],
        base_folds: list[tuple[int, dict[str, Any]]],
        cons_folds: list[tuple[int, dict[str, Any]]]) -> dict[str, Any]:
    problems: list[str] = []
    folds_checked = 0
    for label, cfg, folds in (("base", base_side_cfg, base_folds),
                              ("conservative", cons_side_cfg, cons_folds)):
        if cfg.get("risk_constraints_enabled") is not True:
            problems.append(f"{label}: risk_constraints_enabled != true")
        if cfg.get("risk_constraints_calibration") != "campaign_v1":
            problems.append(f"{label}: calibration != campaign_v1")
        for idx, rep in folds:
            folds_checked += 1
            rc = ((rep.get("backtest") or {}).get("provenance") or {}) \
                .get("config", {}).get("risk_constraints")
            if rc != CAMPAIGN_V1_EXPECTED:
                problems.append(
                    f"{label} fold {idx}: risk_constraints={rc!r}")
    return {
        "expected": CAMPAIGN_V1_EXPECTED,
        "folds_checked": folds_checked,
        "problems": problems,
        "veto_triggered": bool(problems),
    }


def compute_midcap_concentration(
        cons_folds: list[tuple[int, dict[str, Any]]],
        expected_folds: int) -> dict[str, Any]:
    csi500_w: list[float] = []
    unknown_w: list[float] = []
    for idx, rep in cons_folds:
        att = rep.get("attribution") or {}
        if att.get("status") != "ok":
            continue
        rows = _sleeve_rows(rep)
        # The csi500 row is producer-mandatory in an ok csi800 sleeve
        # report — its absence must refuse, never read as zero
        # concentration (codex #373 r16 P2). The ``unknown`` row is
        # legitimately OMITTED by the producer when the honest bucket is
        # empty on both sides (observed in 8/23 real folds), so absence
        # alone is not tampering — instead the weight-mass closure
        # check below catches a DELETED row that actually carried
        # portfolio weight.
        row500 = rows.get("csi500_sleeve")
        if not isinstance(row500, dict) or "portfolio_weight" not in row500:
            raise AttachError(
                f"fold {idx}: attribution status is ok but the "
                "csi500_sleeve portfolio_weight row is absent — an "
                "omitted producer field cannot be read as zero "
                "concentration, refusing.")
        w500 = float(row500["portfolio_weight"])
        row_unknown = rows.get("unknown")
        if isinstance(row_unknown, dict):
            if "portfolio_weight" not in row_unknown:
                raise AttachError(
                    f"fold {idx}: unknown sleeve row lacks "
                    "portfolio_weight — refusing.")
            w_unknown = float(row_unknown["portfolio_weight"])
        else:
            w_unknown = 0.0
        # NaN/inf must refuse (codex #373 r7 P1): ``nan > 0.75`` is
        # False, so corrupted weights would record veto 5 as passing.
        # Checked BEFORE mass closure so a poisoned weight gets its
        # specific message rather than a NaN sum.
        if not (math.isfinite(w500) and math.isfinite(w_unknown)):
            raise AttachError(
                f"fold {idx}: non-finite sleeve weights "
                f"(csi500={w500!r}, unknown={w_unknown!r}) — corrupted "
                "concentration evidence, refusing.")
        # weight-mass closure: portfolio weights over ALL present rows
        # must sum to ~1 — deleting a row that carried weight (e.g. to
        # hide unknown-bucket exposure) breaks the sum.
        total_w = sum(float(r.get("portfolio_weight", 0.0))
                      for r in rows.values())
        if not math.isfinite(total_w) or abs(total_w - 1.0) > 0.005:
            raise AttachError(
                f"fold {idx}: sleeve portfolio weights sum to "
                f"{total_w!r}, not ~1.0 — a weight-carrying row is "
                "missing or corrupted, refusing.")
        csi500_w.append(w500)
        unknown_w.append(w_unknown)
    if len(csi500_w) != expected_folds:
        return {
            "folds_used": len(csi500_w),
            "expected_folds": expected_folds,
            "note": (f"only {len(csi500_w)}/{expected_folds} folds carry "
                     "ok sleeve attribution — fail closed"),
            "veto_triggered": True,
        }
    avg500 = sum(csi500_w) / len(csi500_w)
    avg_unknown = sum(unknown_w) / len(unknown_w)
    return {
        "csi500_time_avg_weight": avg500,
        "unknown_time_avg_weight": avg_unknown,
        "folds_used": len(csi500_w),
        "expected_folds": expected_folds,
        "thresholds": {"csi500": CSI500_WEIGHT_THRESHOLD,
                       "unknown": UNKNOWN_WEIGHT_THRESHOLD},
        "veto_triggered": bool(avg500 > CSI500_WEIGHT_THRESHOLD
                               or avg_unknown > UNKNOWN_WEIGHT_THRESHOLD),
    }


def attach(pair_report_path: Path, base_run: Path, conservative_run: Path,
           reference_run: Path) -> dict[str, Any]:
    """Bind evidence dirs to the pair report, compute checks 2-5, and
    rewrite the pair report in place. Returns the updated report."""
    report: dict[str, Any] = json.loads(
        pair_report_path.read_text(encoding="utf-8"))
    checklist = report["veto_checklist"]
    check1 = checklist.get("1_conservative_net_excess")
    if not isinstance(check1, dict) or "veto_triggered" not in check1:
        raise AttachError(
            "pair report lacks computed check 1 — regenerate with "
            "csi800_campaign_pair_report.py first.")

    base_side, base_fold_reports = _bind_paired_side(
        "base", base_run, report["base"])
    cons_side, cons_fold_reports = _bind_paired_side(
        "conservative", conservative_run, report["conservative"])
    cons_n = int(cons_side["num_folds"])
    _ref_report, ref_failed, ref_fold_reports = _bind_reference(
        reference_run, base_side["config"])

    # Veto 1 is RE-DERIVED from the BOUND sides' official metrics (which
    # come from the hash-verified aggregates), never trusted from the
    # mutable pair-report JSON (codex #373 r6 P1: an edited check-1
    # value/flag would otherwise flip promotion_eligible while every
    # binding check still passes). Any mismatch with the stored entry
    # means the pair report was edited after pairing — refuse.
    cons_net = _require_finite_net(cons_side, "conservative")
    base_net = _require_finite_net(base_side, "base")
    for field, derived in (("value_annualized", cons_net),
                           ("base_value_annualized", base_net)):
        stored = check1.get(field)
        if (isinstance(stored, bool)
                or not isinstance(stored, (int, float))
                or not math.isclose(float(stored), derived,
                                    rel_tol=1e-12, abs_tol=1e-12)):
            raise AttachError(
                f"check 1 {field}={stored!r} does not match the bound "
                f"side's official metrics ({derived!r}) — the pair "
                "report was edited after pairing, refusing.")
    if check1.get("veto_triggered") is not bool(cons_net <= 0.0):
        raise AttachError(
            f"check 1 veto_triggered={check1.get('veto_triggered')!r} "
            f"contradicts the bound conservative net excess "
            f"({cons_net!r}) — the pair report was edited after "
            "pairing, refusing.")

    checklist["2_csi500_dependence"] = compute_csi500_dependence(
        cons_fold_reports, cons_net, cons_n)
    checklist["3_turnover_vs_csi300_ref"] = compute_turnover_check(
        conservative_run, base_run, reference_run,
        cons_fold_reports, base_fold_reports, ref_fold_reports,
        ref_failed)
    checklist["4_risk_constraints_recorded"] = compute_constraints_check(
        base_side["config"], cons_side["config"],
        base_fold_reports, cons_fold_reports)
    checklist["5_midcap_concentration"] = compute_midcap_concentration(
        cons_fold_reports, cons_n)

    eligible, incomplete = evaluate_promotion_eligibility(checklist)
    # Promotion gate on reference authentication (codex #373 r9+r10 P1):
    # UNAUTHENTICATED reference evidence may support a veto (safe
    # direction) but never a promotion — a fully-passing checklist whose
    # veto-3 baseline could have been silently replaced must not emit
    # eligibility. Only PROMOTION_QUALIFYING_REF_BINDING (a
    # producer-stamped positions digest bound to a certified reference
    # artifact — not yet implemented by any producer) qualifies;
    # presence-based labels do not (r10: mutable fold reports can be
    # replaced together with their positions).
    binding = checklist["3_turnover_vs_csi300_ref"].get(
        "reference_content_binding")
    # The structural prerequisite is recorded UNCONDITIONALLY (codex
    # #373 r11 P2): a vetoed artifact must still tell a later operator
    # that even a clean rerun cannot become promotion-eligible until the
    # producer-side certified digest ships — otherwise the recorded veto
    # reads as the only blocker.
    blockers: list[str] = []
    if binding != PROMOTION_QUALIFYING_REF_BINDING:
        blockers.append(
            "reference_binding_unauthenticated: veto-3 reference "
            f"turnover evidence is {binding!r}; promotion structurally "
            f"requires {PROMOTION_QUALIFYING_REF_BINDING!r} (a "
            "producer-stamped positions digest bound to a certified "
            "reference artifact — not yet implemented by any producer; "
            "backlog), independent of the current veto verdict; codex "
            "#373 r9-r11.")
    report["promotion_blockers"] = blockers
    # promotion_blocked_reason asserts "all five checks pass, blocked
    # only by the prerequisite" — recompute it on EVERY attach (codex
    # #373 r12 P2): a rerun over a report whose evidence has since
    # gained a triggered veto must not carry the stale claim forward.
    report.pop("promotion_blocked_reason", None)
    if eligible and blockers:
        eligible = False
        report["promotion_blocked_reason"] = blockers[0]
    report["promotion_eligible"] = eligible
    report["incomplete_checks"] = incomplete
    report["veto_checklist_status"] = (
        "COMPLETE" if not incomplete else
        "INCOMPLETE — NOT promotion-eligible; checks "
        + ", ".join(incomplete) + " must be attached and pass")
    final_bytes = (json.dumps(report, indent=2, ensure_ascii=False)
                   + "\n").encode("utf-8")
    pair_report_path.write_text(final_bytes.decode("utf-8"),
                                encoding="utf-8")
    # Out-of-band anchor (codex #373 r15): the filesystem copy is
    # mutable — consumers must compare this digest against the
    # COMMITTED artifact's bytes (git), which is the trust root.
    print("pair report sha256 (compare against committed bytes): "
          + hashlib.sha256(final_bytes).hexdigest())
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pair-report", required=True, type=Path)
    ap.add_argument("--base-run", required=True, type=Path)
    ap.add_argument("--conservative-run", required=True, type=Path)
    ap.add_argument("--reference-run", required=True, type=Path)
    args = ap.parse_args()

    report = attach(args.pair_report, args.base_run,
                    args.conservative_run, args.reference_run)
    checklist = report["veto_checklist"]
    triggered = [name for name, entry in checklist.items()
                 if isinstance(entry, dict) and entry.get("veto_triggered")]
    print(f"attached checks 2-5 -> {args.pair_report}")
    print(f"promotion_eligible={report['promotion_eligible']} | "
          f"triggered={triggered or 'none'} | "
          f"incomplete={report['incomplete_checks'] or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
