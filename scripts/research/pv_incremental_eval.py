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
        baseline = _load_wide_parquet(baseline_path, "baseline preds")

        from src.core.pit_wiring import build_pit_provider
        from src.factor_mining.evaluator import evaluate_expression
        from src.factor_mining.expression import parse_expression
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
        for cand in candidates:
            expr = parse_expression(cand["expression"])
            factor = evaluate_expression(expr, panel)
            if not isinstance(factor, pd.DataFrame):
                raise PVEvalError(
                    f"candidate {cand['candidate_id']} evaluates to a "
                    "scalar, not a panel — degenerate expression, "
                    "refusing.")
            ic, dropped = daily_rank_ic(factor, fwd, min_names)
            orth = orthogonality_series(factor, baseline, min_names)
            artifact = build_artifact(
                plan, cand["candidate_id"], cand["expression"], ic,
                dropped, orth, baseline_sha)
            out = out_dir / f"{cand['candidate_id']}.json"
            out.write_text(
                json.dumps(artifact, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8")
            print(f"[pv-eval] {cand['candidate_id']}: n_days="
                  f"{artifact['n_days']} ic_mean={artifact['ic_mean']:+.6f} "
                  f"t={artifact['t_stat']:+.3f} "
                  f"orth={artifact['orth_mean_abs_rho']:.4f} -> {out}")
    except PVEvalError as exc:
        print(f"[pv-eval] REFUSED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
