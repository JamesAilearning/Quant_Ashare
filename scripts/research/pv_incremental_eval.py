"""pv_incremental_v1 OOS decision evaluator.

Implements the FROZEN plan ``docs/prereg/pv_incremental.yaml``
(protocol pv_incremental_v1, signed at #398). Per candidate:

  * factor values from the frozen expression via
    ``src.factor_mining.evaluator.evaluate_expression`` (D5 path:
    panel data through ``pit_adapter`` only);
  * daily cross-sectional rank-IC series on the OOS dev window
    (signal t -> execution t+1 -> forward close[t+1]->close[t+2],
    the frozen lag semantics); days narrower than
    ``min_names_per_day`` are dropped AND counted;
  * orthogonality: daily cross-sectional Spearman rho against the
    Alpha158 baseline predictions (provenance-ledgered run) —
    ``mean_abs_rho`` above the frozen OOS hard band disqualifies the
    candidate from survivor semantics regardless of IC;
  * persists a per-candidate artifact carrying the full daily series
    for the FULL-BATCH FWER step (which is NOT computed here).

Window discipline is fail-loud: the evaluator refuses any window not
byte-equal to the frozen OOS dev window, refuses any data date
>= the frozen ``forbidden_from`` boundary, and NEVER touches the
2025 holdout (``holdout_unblinded`` must be false in the plan).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PLAN_PATH = PROJECT_ROOT / "docs" / "prereg" / "pv_incremental.yaml"
PROTOCOL_ID = "pv_incremental_v1"


class PVEvalError(RuntimeError):
    """Classified refusal — the zero-write path out of the evaluator."""


def load_frozen_plan(path: Path = PLAN_PATH) -> dict[str, Any]:
    """Load + validate the frozen plan; any drift fails loud."""
    try:
        plan = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PVEvalError(f"frozen plan unreadable: {path} ({exc})") from exc
    if not isinstance(plan, dict) or plan.get("protocol_id") != PROTOCOL_ID:
        raise PVEvalError(
            f"frozen plan is not {PROTOCOL_ID}: {path} — refusing.")
    if plan.get("holdout_unblinded") is not False:
        raise PVEvalError(
            "holdout_unblinded is not false — the 2025 holdout must stay "
            "blinded for every evaluator run; unblinding is a one-way "
            "operator signature at promotion finals, not here.")
    for section in ("windows", "metric", "fitness", "fwer", "fields",
                    "operators"):
        if section not in plan:
            raise PVEvalError(f"frozen plan lacks {section!r} — refusing.")
    return plan


def check_pv_terminals(expression: str, fields: list[str]) -> None:
    """PV-DP-1 enforcement (codex #399 r7): the frozen plan registers
    exactly seven PV fields — a CSF/PURE-rooted expression over a
    non-PV terminal (``$pe`` etc., valuation fields the freeze
    explicitly EXCLUDES) must refuse as a registration error, not
    die as a raw KeyError mid-batch."""
    terminals = set(re.findall(r"\$[a-z_0-9]+", expression))
    allowed = {f"${f}" for f in fields}
    foreign = sorted(terminals - allowed)
    if foreign:
        raise PVEvalError(
            f"expression uses non-PV terminals {foreign} — the "
            f"frozen plan registers exactly {sorted(allowed)}; fix "
            "the registration, refusing.")


def check_window_discipline(plan: dict[str, Any], start: str,
                            end: str) -> None:
    """The OOS run must use EXACTLY the frozen window (PV-DP-2)."""
    w = plan["windows"]
    if (start, end) != (w["oos_start"], w["oos_end"]):
        raise PVEvalError(
            f"window {start}..{end} is not the frozen OOS dev window "
            f"{w['oos_start']}..{w['oos_end']} — pre-registration is "
            "immutable after signature; refusing.")
    if end >= w["forbidden_from"]:
        raise PVEvalError("window crosses the forbidden 2026 boundary.")
    if int(end[:4]) >= int(w["holdout_year"]):
        raise PVEvalError("window touches the blinded holdout year.")


def check_candidate_id(cid: object) -> str:
    """candidate_id becomes a FILENAME under the sealed batch
    directory (codex #399 r11): an id carrying path separators or an
    absolute prefix (`../escape`, `/tmp/escape`) would escape
    --out-dir — the stray artifact lands elsewhere on disk while the
    completion stamp seals out_dir, so the batch can never
    adjudicate. Safe-slug only, refused before any write."""
    if not isinstance(cid, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]*", cid):
        raise PVEvalError(
            f"candidate_id {cid!r} is not a safe filename slug "
            "([A-Za-z0-9][A-Za-z0-9_-]*) — refusing before any "
            "artifact is written.")
    return cid


def preflight_candidates(candidates: list[dict[str, Any]],
                         fields: list[str]) -> list[Any]:
    """Validate the whole registered manifest BEFORE the first
    artifact write (codex #399 r12+r13): a bad id OR bad expression
    found mid-loop leaves earlier `<id>.json` files on disk with no
    completion stamp — a dirty batch a retry cannot reuse (exclusive
    create) and the adjudicator cannot accept. Ids safe + unique,
    every expression on the PV whitelist, parsing under the frozen
    grammar and rooted CSF/PURE — or nothing is written. Returns the
    parsed expressions, aligned with `candidates`."""
    from src.factor_mining.expression import parse_expression
    from src.factor_mining.grammar import ExprType, GrammarError

    seen: set[str] = set()
    for cand in candidates:
        cid = check_candidate_id(cand.get("candidate_id"))
        if cid in seen:
            raise PVEvalError(
                f"candidate_id {cid!r} appears more than once in the "
                "registered manifest — refusing before any artifact "
                "is written.")
        seen.add(cid)
        # Orientation is the GP's record of the IS sign (codex #401
        # r13): a factor bred under the frozen |rank-IC| criterion may
        # be a stable NEGATIVE predictor, and this gate tests SIGNED
        # daily IC against a one-sided POSITIVE threshold — applying
        # the sign is what stops a real factor being tested backwards.
        orientation = cand.get("orientation", 1)
        if orientation not in (1, -1):
            raise PVEvalError(
                f"candidate {cid!r}: orientation {orientation!r} is "
                "neither +1 nor -1 — refusing (the registered manifest "
                "must state which sign the GP selected).")
    parsed: list[Any] = []
    for cand in candidates:
        # The factor-mining root contract (codex #399 r6): a
        # candidate must parse under the frozen grammar (taint rules
        # refuse at parse time) AND its root must be a PURE
        # cross-sectional factor — a parser-valid raw/price-level
        # root (e.g. `$close`) would bypass the adjustment-purity
        # guarantees the grammar enforces.
        check_pv_terminals(cand["expression"], fields)
        try:
            expr = parse_expression(cand["expression"])
        except (GrammarError, ValueError, KeyError) as exc:
            raise PVEvalError(
                f"candidate {cand['candidate_id']} does not parse "
                f"under the frozen grammar: {exc}") from exc
        root_type = expr.output_type
        if root_type != ExprType("CSF", "PURE"):
            raise PVEvalError(
                f"candidate {cand['candidate_id']} has root type "
                f"{root_type} — the registered contract requires "
                "ExprType('CSF', 'PURE'); fix the registration, "
                "refusing.")
        parsed.append(expr)
    return parsed


def check_baseline_provenance(plan: dict[str, Any], baseline_path: Path,
                              baseline_sha256: str) -> dict[str, Any]:
    """The frozen plan requires the ledgered Alpha158 walk-forward
    baseline, not any parquet that happens to load (codex #399 r10):
    an ad hoc/stale file would key the ONLY incremental gate
    (orthogonality) to the wrong baseline. Refuse before any
    artifact is written unless a provenance sidecar binds THIS file
    to the frozen baseline model with full run provenance."""
    frozen = plan["fitness"]["baseline"]
    if not frozen.get("provenance_required"):
        return {}
    sidecar = baseline_path.with_name(
        baseline_path.name + ".provenance.json")
    if not sidecar.exists():
        raise PVEvalError(
            f"baseline provenance sidecar {sidecar.name!r} not found "
            "next to the baseline parquet — the frozen plan requires "
            f"the ledgered {frozen['model']!r} baseline; refusing "
            "before any artifact is written.")
    prov: dict[str, Any] = json.loads(
        sidecar.read_text(encoding="utf-8"))
    if prov.get("model") != frozen["model"]:
        raise PVEvalError(
            f"baseline provenance declares model {prov.get('model')!r} "
            f"— not the frozen {frozen['model']!r}; refusing.")
    if prov.get("file_sha256") != baseline_sha256:
        raise PVEvalError(
            "baseline provenance sidecar file_sha256 does not match "
            "the parquet on disk — stale/mismatched sidecar; "
            "refusing.")
    for key in ("run_config_sha256", "source_git"):
        val = prov.get(key)
        if not isinstance(val, str) or not val.strip():
            raise PVEvalError(
                f"baseline provenance is missing {key!r} — the frozen "
                "plan requires full run provenance in the ledger; "
                "refusing.")
    return prov


def forward_returns(close: pd.DataFrame, lag: int = 1) -> pd.DataFrame:
    """Frozen lag semantics: signal at t, execution close[t+lag],
    forward return close[t+lag] -> close[t+lag+1], aligned to t."""
    exec_px = close.shift(-lag)
    next_px = close.shift(-(lag + 1))
    return next_px / exec_px - 1.0


def daily_rank_ic(factor: pd.DataFrame, fwd: pd.DataFrame,
                  min_names: int) -> tuple[pd.Series, int]:
    """Per-day cross-sectional Spearman IC; thin days drop+count."""
    ics: dict[Any, float] = {}
    dropped = 0
    for dt in factor.index.intersection(fwd.index):
        f = factor.loc[dt]
        r = fwd.loc[dt]
        mask = f.notna() & r.notna()
        n = int(mask.sum())
        if n < min_names:
            dropped += 1
            continue
        ic_val = float(
            f[mask].rank().corr(r[mask].rank(), method="pearson"))
        if not np.isfinite(ic_val):
            # Constant/fully-tied cross-section (codex #399 r2):
            # a NaN IC must never reach the FWER series — it would
            # silently fall out of the per-draw maximum and lower
            # the family bar while the trial still counts as a
            # member. Degenerate days are dropped AND counted.
            dropped += 1
            continue
        ics[dt] = ic_val
    return pd.Series(ics, dtype=float).sort_index(), dropped


def orthogonality_series(factor: pd.DataFrame,
                         baseline: pd.DataFrame,
                         min_names: int) -> pd.Series:
    """Daily cross-sectional Spearman rho vs the baseline preds."""
    rhos: dict[Any, float] = {}
    for dt in factor.index.intersection(baseline.index):
        f = factor.loc[dt]
        b = baseline.loc[dt]
        mask = f.notna() & b.notna()
        if int(mask.sum()) < min_names:
            continue
        rhos[dt] = float(
            f[mask].rank().corr(b[mask].rank(), method="pearson"))
    return pd.Series(rhos, dtype=float).sort_index()


def summarize(ic: pd.Series) -> dict[str, float | int]:
    n = int(ic.shape[0])
    mean = float(ic.mean()) if n else float("nan")
    std = float(ic.std(ddof=1)) if n > 1 else float("nan")
    return {
        "n_days": n,
        "ic_mean": mean,
        "ic_std": std,
        "ic_ir": mean / std if n > 1 and std > 0 else float("nan"),
        "t_stat": (mean / (std / np.sqrt(n))
                   if n > 1 and std > 0 else float("nan")),
    }


def build_artifact(plan: dict[str, Any], candidate_id: str,
                   expression: str, ic: pd.Series, dropped: int,
                   orth: pd.Series,
                   baseline_sha256: str) -> dict[str, Any]:
    stats = summarize(ic)
    # The orthogonality check is the ONLY guard against promoting
    # standalone/non-incremental signal (codex #399 r1): a baseline
    # that covers only a sliver of the eligible IC days must not
    # adjudicate the band from that sliver — insufficient coverage is
    # a data-prep error and the run fails loud.
    orth_cfg = plan["fitness"]["orthogonality"]
    min_cov = float(orth_cfg["min_coverage_of_ic_days"])
    # Coverage is measured ON THE ELIGIBLE IC DATES (codex #399 r2):
    # baseline days outside ic.index (e.g. tail signal days with no
    # forward return) must not compensate for missing eligible ones,
    # and the band itself is adjudicated from the same restricted
    # series.
    orth = orth.reindex(ic.index).dropna()
    if ic.shape[0] and orth.shape[0] < min_cov * ic.shape[0]:
        raise PVEvalError(
            f"{candidate_id}: baseline preds cover only "
            f"{orth.shape[0]}/{ic.shape[0]} eligible IC days "
            f"(< {min_cov:.0%}) — the orthogonality gate cannot be "
            "adjudicated from partial overlap; regenerate the "
            "baseline predictions over the full OOS window.")
    orth_mean_abs = float(orth.abs().mean()) if orth.shape[0] else float("nan")
    hard_band = orth_cfg["oos_hard_band_mean_abs_rho"]
    return {
        "protocol_id": PROTOCOL_ID,
        "candidate_id": candidate_id,
        "expression": expression,
        "window": {"start": plan["windows"]["oos_start"],
                   "end": plan["windows"]["oos_end"]},
        "daily_ic": [{"date": str(k)[:10], "ic": v}
                     for k, v in ic.items()],
        "dropped_thin_days": dropped,
        **stats,
        "orth_mean_abs_rho": orth_mean_abs,
        "orth_n_days": int(orth.shape[0]),
        "orth_within_hard_band": bool(orth_mean_abs <= hard_band)
        if orth.shape[0] else False,
        "baseline_preds_sha256": baseline_sha256,
    }


def _load_wide_parquet(path: Path, what: str) -> pd.DataFrame:
    try:
        frame = pd.read_parquet(path)
    except (OSError, ValueError) as exc:
        raise PVEvalError(f"{what} unreadable: {path} ({exc})") from exc
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise PVEvalError(f"{what} must be date-indexed wide frame.")
    return frame


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidates", required=True,
                   help="JSON file: [{candidate_id, expression}, ...] — "
                        "the registered batch, oldest registration first.")
    p.add_argument("--baseline-preds", required=True,
                   help="Wide parquet (date x instrument) of the "
                        "provenance-ledgered Alpha158 baseline preds.")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--window-start", required=True)
    p.add_argument("--window-end", required=True)
    p.add_argument("--provider",
                   default="D:/qlib_data/my_cn_data_pit")
    p.add_argument("--delisted-registry",
                   default="D:/qlib_data/tushare_raw/"
                           "delisted_registry.parquet")
    args = p.parse_args(argv)

    try:
        plan = load_frozen_plan()
        check_window_discipline(plan, args.window_start, args.window_end)
        baseline_path = Path(args.baseline_preds)
        baseline_sha = hashlib.sha256(
            baseline_path.read_bytes()).hexdigest()
        check_baseline_provenance(plan, baseline_path, baseline_sha)
        baseline = _load_wide_parquet(baseline_path, "baseline preds")

        from src.core.pit_wiring import build_pit_provider
        from src.factor_mining.evaluator import evaluate_expression
        from src.factor_mining.pit_adapter import FactorMiningDataView

        provider = build_pit_provider(
            provider_uri=args.provider,
            delisted_registry_path=args.delisted_registry,
            data_adjust_mode="pre_adjusted", region="cn")
        # The frozen plan lists unsigned field names; the expression
        # grammar's terminals (and the panel keys the view returns)
        # are qlib feature names — "$close" etc. (codex #399 r1).
        view = FactorMiningDataView(
            provider, start=args.window_start, end=args.window_end,
            universe_name=plan["universe"]["instruments"],
            fields=[f"${f}" for f in plan["fields"]])
        panel = view.load_panel()
        close = panel["$close"]
        fwd = forward_returns(
            close, lag=int(plan["metric"]["signal_to_execution_lag"]))
        min_names = int(plan["metric"]["min_names_per_day"])

        candidates = json.loads(
            Path(args.candidates).read_text(encoding="utf-8"))
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        # ONE run identity per invocation (codex #399 r5): every
        # artifact carries it and the completion stamp seals the
        # batch — the adjudicator refuses mixed old/new families.
        import uuid

        run_id = uuid.uuid4().hex
        written: list[str] = []

        # Preflight the WHOLE manifest before the first write (codex
        # #399 r12+r13): an unsafe/duplicate id OR an invalid
        # expression discovered mid-loop would leave earlier
        # artifacts on disk with no completion stamp — a dirty,
        # unadjudicable batch directory for exactly the bad-manifest
        # case these checks exist to refuse.
        parsed = preflight_candidates(candidates, list(plan["fields"]))

        for cand, expr in zip(candidates, parsed, strict=True):
            factor = evaluate_expression(expr, panel)
            if not isinstance(factor, pd.DataFrame):
                raise PVEvalError(
                    f"candidate {cand['candidate_id']} evaluates to a "
                    "scalar, not a panel — degenerate expression, "
                    "refusing.")
            # Apply the registered orientation BEFORE any metric: the
            # IC, the orthogonality series and the FWER t all have to
            # see the factor in the direction the GP selected it
            # (codex #401 r13).
            if int(cand.get("orientation", 1)) == -1:
                factor = -factor
            ic, dropped = daily_rank_ic(factor, fwd, min_names)
            if ic.shape[0] == 0:
                raise PVEvalError(
                    f"{cand['candidate_id']}: zero eligible IC days "
                    "— the candidate is not evaluable on the frozen "
                    "window; refusing (a zero-day artifact would "
                    "carry undefined orthogonality).")
            orth = orthogonality_series(factor, baseline, min_names)
            artifact = build_artifact(
                plan, cand["candidate_id"], cand["expression"], ic,
                dropped, orth, baseline_sha)
            # Record the applied sign so the adjudicator's record shows
            # WHICH orientation produced these numbers.
            artifact["orientation"] = int(cand.get("orientation", 1))
            artifact["run_id"] = run_id
            out = out_dir / f"{cand['candidate_id']}.json"
            try:
                with open(out, "x", encoding="utf-8") as fh:
                    fh.write(json.dumps(artifact, indent=2,
                                        ensure_ascii=False) + "\n")
            except FileExistsError as exc:
                raise PVEvalError(
                    f"artifact already exists: {out} — reruns use a "
                    "FRESH artifacts dir (mixed-batch adjudication is "
                    "refused downstream); refusing to clobber."
                ) from exc
            written.append(cand["candidate_id"])
            print(f"[pv-eval] {cand['candidate_id']}: n_days="
                  f"{artifact['n_days']} ic_mean={artifact['ic_mean']:+.6f} "
                  f"t={artifact['t_stat']:+.3f} "
                  f"orth={artifact['orth_mean_abs_rho']:.4f} -> {out}")
        completion = {
            "protocol_id": PROTOCOL_ID, "run_id": run_id,
            "candidate_ids": written,
            "baseline_preds_sha256": baseline_sha,
        }
        (out_dir / "_batch_complete.json").write_text(
            json.dumps(completion, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print(f"[pv-eval] batch complete: run_id={run_id} "
              f"({len(written)} artifacts)")
    except PVEvalError as exc:
        print(f"[pv-eval] REFUSED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
