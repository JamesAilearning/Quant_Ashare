#!/usr/bin/env python3
"""Out-of-sample eval of a FROZEN qlib model over a guard window (④ promotion recon).

Loads an ALREADY-TRAINED pickled model (NOT a retrain), builds the Alpha158 dataset for a
guard window normalized to the model's own training fit window, predicts, and computes the
CANONICAL signal + backtest metrics + a degeneracy scan — reusing the exact config the
WF / REGEN replay uses (``replay_frozen_baseline`` constants + ``CanonicalBacktestInput``),
so the incumbent baseline and a later candidate are apples-to-apples (variable isolation).

Reports: IC(1d/5d), IC-IR(1d), turnover, backtest annualized_return / information_ratio /
max_drawdown (excess-with-cost vs SH000300TR), and a per-date prediction-degeneracy scan
(gross unique-score collapse — the ~39-bucket/261-tie REGEN fold-0 mode — AND a tie block
straddling the top-k cutoff, where the buy list is tie-break dependent even with an
otherwise-unique universe). The degeneracy scan also runs on the incumbent (does it degenerate?).

This is real compute on the LIVE bundle (read-only) but NOT a retrain — run FOREGROUND.
Defaults target the incumbent on the guard window; override --train-end / --valid-* and
--model for the candidate. ST-mask is ENABLED (matches the incumbent's single-fold path).
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Canonical backtest knobs — imported from the SAME module the WF/REGEN replay uses, so
# the metrics are computed identically (T+1, close exec, ±9.5% limit, costs).
from scripts.eval_profiles import EVAL_PROFILES, resolve_profile  # noqa: E402
from scripts.regen.replay_frozen_baseline import (  # noqa: E402
    COMMISSION,
    EXEC_PRICE,
    INIT_CASH,
    LAG,
    LIMIT_THRESHOLD,
    MIN_COST,
    N_DROP,
    SLIPPAGE_BPS,
    TOPK,
)
from src.core.backtest_runner import BacktestRunner  # noqa: E402
from src.core.canonical_backtest_contract import (  # noqa: E402
    ADJUST_MODE_PRE,
    CN_STAMP_TAX_SCHEDULE_DEFAULT,
    CanonicalAccountConfig,
    CanonicalBacktestInput,
    CanonicalExchangeConfig,
    CanonicalExchangeCostModel,
)
from src.core.qlib_runtime import QlibRuntimeConfig, init_qlib_canonical  # noqa: E402
from src.core.risk_constraints import campaign_risk_constraints_v1  # noqa: E402
from src.core.signal_analyzer import SignalAnalysisConfig, SignalAnalyzer  # noqa: E402
from src.core.walk_forward.aggregate import extract_cost_metrics  # noqa: E402
from src.data.feature_dataset_builder import (  # noqa: E402
    FeatureDatasetBuilder,
    FeatureDatasetConfig,
)

_BENCHMARK_TR = "SH000300TR"  # canonical total-return basis (PR-2)

# Eval profiles live in the PURE scripts/eval_profiles.py (codex #387
# r1: governance pins must not drag this qlib-bound module onto their
# import path). The csi300_daily profile's slippage MUST equal the
# replay constant — assert it at import time here (the one place both
# modules are loaded together) and cross-pin in the qlib-gated
# tests/logic/test_eval_frozen_model_oos.py.
assert EVAL_PROFILES["csi300_daily"]["slippage_bps"] == SLIPPAGE_BPS, (
    "eval_profiles.csi300_daily slippage drifted from "
    "replay_frozen_baseline.SLIPPAGE_BPS")


def _executable_stamps(
    preds: pd.Series, args: argparse.Namespace, profile: dict[str, Any],
) -> pd.Series:
    """Thin prediction stamps to the profile's rebalance-day set — via the
    SAME canonical helper the profiled backtest uses, so the veto-bearing
    degeneracy scan counts only EXECUTABLE stamps (codex #387 r3: a
    degenerate score block on a mid-week HOLD stamp never trades under
    iso_week cadence and must not hard-veto the candidate). Daily profile:
    returns ``preds`` unchanged (the helper's byte-identical fast path)."""
    if (profile["rebalance_cadence_days"] == 1
            and profile["rebalance_anchor"] == "fold_phase"):
        return preds
    from qlib.data import D

    cal = list(D.calendar(
        start_time=args.guard_start, end_time=args.guard_end))
    thinned = BacktestRunner._thin_predictions(
        preds,
        cadence_days=profile["rebalance_cadence_days"],
        phase=profile["rebalance_phase"],
        anchor=profile["rebalance_anchor"],
        trading_calendar=cal,
    )
    if not isinstance(thinned, pd.Series):
        thinned = pd.Series(thinned)
    return thinned


def _predictions_over_window(args: argparse.Namespace) -> pd.Series:
    """Build the Alpha158 dataset (test = guard window, normalized to the fit window),
    load the frozen model, and predict on the test segment."""
    init_qlib_canonical(
        QlibRuntimeConfig(
            provider_uri=args.provider, region="cn", data_adjust_mode=ADJUST_MODE_PRE,
        )
    )
    build = FeatureDatasetBuilder.build(
        FeatureDatasetConfig(
            instruments=args.instruments,
            feature_handler=args.handler,
            train_start=args.fit_start,       # normalization fit = the model's training
            train_end=args.fit_end,
            valid_start=args.valid_start,     # placeholder segment (unused for a frozen model;
            valid_end=args.valid_end,         # kept embargo-valid so the builder accepts it)
            test_start=args.guard_start,      # the GUARD window we score
            test_end=args.guard_end,
        )
    )
    with Path(args.model).open("rb") as fh:
        model = pickle.load(fh)
    if not hasattr(model, "predict"):
        raise SystemExit(f"loaded object {type(model).__name__} has no .predict")
    preds = model.predict(build.dataset, segment="test")
    if not isinstance(preds, pd.Series):
        preds = pd.Series(preds)
    return preds.dropna()


# A tie block STRADDLING the top-k cutoff is degenerate when a MATERIAL share of the buy
# list is filled by tie-break — >= 20% of top-k SLOTS. The materiality basis is the number
# of top-k slots filled FROM the tie block (``TOPK - n_above``), NOT the total names tied:
# 49 names above + 10 tied means only 1 buy is tie-break-dependent (immaterial), even though
# 10 names sit on the bubble (codex). Smaller straddles are reported but not vetoed.
_CUTOFF_STRADDLE_VETO = max(round(0.2 * TOPK), 2)


def _degeneracy_scan(preds: pd.Series) -> dict[str, Any]:
    """Per-date prediction degeneracy. Two failure modes:

    (a) GROSS collapse — few unique scores over the whole universe (REGEN fold-0: ~39/300).
    (b) a tie block STRADDLING the top-k cutoff — the k-th score is shared by MORE names
        than the slots left, so top-k membership is tie-break dependent EVEN IF the rest of
        the universe is unique (codex: 75 tied at rank 50 with 225 unique elsewhere gives a
        high unique ratio yet an arbitrary buy list). The earlier unique-ratio-only check
        missed (b); this detects the cutoff straddle directly.

    Materiality of (b) is the count of top-k slots filled FROM the tie block
    (``tie_filled_slots = TOPK - n_above``) — i.e. how many of the actual buys are arbitrary
    — NOT the total names tied at the cutoff (which over-counts when the bubble is wide but
    only a slot or two is in-play). Vetoes (``n_degenerate_days``) on a gross collapse OR a
    straddle with >= ``_CUTOFF_STRADDLE_VETO`` tie-filled slots. Both the slot count
    (``max_tie_filled_slots``, the veto basis) and the total bubble size
    (``max_names_tied_at_cutoff``) are reported for the operator to eyeball."""
    rows = []
    for date, grp in preds.groupby(level=0):  # level 0 = datetime (name may be unset)
        n = int(grp.shape[0])
        uniq = int(grp.nunique())
        n_at_cutoff = 0
        tie_filled_slots = 0
        if n > TOPK:
            srt = sorted(grp.tolist(), reverse=True)
            boundary = srt[TOPK - 1]                          # the k-th (cutoff) score
            n_above = sum(1 for v in srt if v > boundary)     # strictly above the cutoff
            n_at = sum(1 for v in srt if v == boundary)       # tied AT the cutoff score
            if n_above < TOPK < n_above + n_at:               # cutoff strictly inside the tie
                n_at_cutoff = n_at                            # total bubble size (reported)
                tie_filled_slots = TOPK - n_above            # arbitrary buys (veto basis)
        rows.append(
            (str(date)[:10], n, uniq, 1.0 - uniq / n if n else 0.0,
             n_at_cutoff, tie_filled_slots)
        )
    df = pd.DataFrame(
        rows,
        columns=["date", "n", "n_unique", "tie_density",
                 "n_tied_at_cutoff", "tie_filled_slots"],
    )
    gross = df["n_unique"] < df["n"] * 0.5
    material_straddle = df["tie_filled_slots"] >= _CUTOFF_STRADDLE_VETO
    degen = df[gross | material_straddle]
    return {
        "n_days": int(df.shape[0]),
        "min_unique": int(df["n_unique"].min()),
        "median_unique": float(df["n_unique"].median()),
        "median_universe": float(df["n"].median()),
        "max_tie_density": float(df["tie_density"].max()),
        "median_tie_density": float(df["tie_density"].median()),
        "n_cutoff_straddle_days": int((df["n_tied_at_cutoff"] > 0).sum()),
        "max_names_tied_at_cutoff": int(df["n_tied_at_cutoff"].max()),
        "max_tie_filled_slots": int(df["tie_filled_slots"].max()),
        "cutoff_straddle_veto_min": int(_CUTOFF_STRADDLE_VETO),
        "n_degenerate_days": int(degen.shape[0]),
        "degenerate_days_sample": degen.head(10).to_dict("records"),
    }


def _concentration_stats(positions: Any) -> dict[str, float]:
    """Per-date holding concentration from the backtest positions — a behavioral guard.
    An equal-weight top-50 should sit near n_holdings~50 / top10_share~0.2 / HHI~0.02; a
    candidate that holds far fewer effective names, or skews weight onto a few, is
    concentrating (measured even though the backtest ran with NO risk constraints)."""
    import statistics

    n_holds: list[int] = []
    top10: list[float] = []
    max_w: list[float] = []
    hhi: list[float] = []
    for _date, holds in (positions or {}).items():
        weights = [abs(float(x)) for x in holds.values() if x is not None]
        tot = sum(weights)
        if not weights or tot <= 0:
            continue
        shares = sorted((w / tot for w in weights), reverse=True)
        n_holds.append(len(weights))
        top10.append(sum(shares[:10]))
        max_w.append(shares[0])
        hhi.append(sum(s * s for s in shares))
    if not n_holds:
        return {}
    return {
        "median_n_holdings": float(statistics.median(n_holds)),
        "min_n_holdings": float(min(n_holds)),
        "median_top10_share": float(statistics.median(top10)),
        "max_single_name_weight": float(max(max_w)),
        "median_hhi": float(statistics.median(hhi)),
    }


def _backtest_metrics(
    preds: pd.Series, args: argparse.Namespace, profile: dict[str, Any],
) -> dict[str, Any]:
    request = CanonicalBacktestInput(
        predictions_ref=f"oos_eval:{Path(args.model).name}",
        evaluation_start=args.guard_start,
        evaluation_end=args.guard_end,
        account_config=CanonicalAccountConfig(init_cash=INIT_CASH),
        exchange_config=CanonicalExchangeConfig(
            freq="day",
            execution_price_kind=EXEC_PRICE,
            cost_model=CanonicalExchangeCostModel(
                commission_rate=COMMISSION,
                stamp_tax_schedule=CN_STAMP_TAX_SCHEDULE_DEFAULT,
                slippage_bps=profile["slippage_bps"],
                min_cost=MIN_COST,
            ),
            limit_threshold=LIMIT_THRESHOLD,
        ),
        adjust_mode=ADJUST_MODE_PRE,
        signal_to_execution_lag=LAG,
        benchmark_code=profile["benchmark_code"],
    )
    output = BacktestRunner.run(
        request=request,
        predictions=preds,
        topk=TOPK,
        n_drop=N_DROP,
        compute_baselines=False,
        namechange_path=args.namechange,
        require_st_mask=True,  # match the incumbent's single-fold path
        rebalance_cadence_days=profile["rebalance_cadence_days"],
        rebalance_phase=profile["rebalance_phase"],
        rebalance_anchor=profile["rebalance_anchor"],
        risk_constraint_scope=profile["risk_constraint_scope"],
        risk_constraints=(
            campaign_risk_constraints_v1()
            if profile["campaign_constraints"] else None
        ),
        universe_hint=profile["instruments"],
    )
    ann, dd, ir = extract_cost_metrics(output.risk_analysis, 0)
    metrics: dict[str, Any] = {
        "annualized_return": ann,
        "max_drawdown": dd,
        "information_ratio": ir,
        "concentration": _concentration_stats(output.positions),
    }
    # GROSS leg (a PRE-REGISTERED PR-B diagnostic) — fail CLOSED on any
    # schema drift (codex #387 r1): a silently absent gross leg would
    # hand PR-C an eval artifact missing a mandated field instead of
    # stopping at the producer.
    gross_block = output.risk_analysis.get("excess_return_without_cost")
    if not isinstance(gross_block, dict) or (
            "annualized_return" not in gross_block):
        raise SystemExit(
            "risk_analysis is missing excess_return_without_cost."
            "annualized_return — schema drift; the gross leg is a "
            "pre-registered PR-B diagnostic, refusing to emit a "
            "partial eval artifact.")
    raw_gross = gross_block["annualized_return"]
    if isinstance(raw_gross, bool) or not isinstance(
            raw_gross, (int, float)) or not math.isfinite(float(raw_gross)):
        raise SystemExit(
            f"excess_return_without_cost.annualized_return is not a "
            f"finite number ({raw_gross!r}) — refusing.")
    metrics["gross_annualized_return"] = float(raw_gross)
    return metrics


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", default="D:/stock/phase_b_artifacts/alpha158_lgb_pit.pkl")
    p.add_argument("--provider", default="D:/qlib_data/my_cn_data_pit")
    p.add_argument("--namechange", default="D:/qlib_data/tushare_raw/all_namechanges.parquet")
    p.add_argument(
        "--profile", choices=sorted(EVAL_PROFILES), default="csi300_daily",
        help="Pre-registered semantic knob set (universe/benchmark/"
             "slippage/cadence/constraints). csi300_daily = legacy ④ "
             "behaviour; csi800_n5 = the certified-winner production "
             "guard (PR-B of csi800-n5-production-promotion). The "
             "profile's knobs are NOT individually overridable.")
    p.add_argument("--handler", default="Alpha158")
    # Normalization fit window = the model's OWN training window (canonical model
    # default; the canonical alpha158_lgb_pit.pkl trains 2018-01-02..2024-12-18).
    p.add_argument("--fit-start", default="2018-01-02")
    p.add_argument("--fit-end", default="2024-12-18")
    # Placeholder valid segment (unused by a frozen model; embargo-valid for the builder).
    p.add_argument("--valid-start", default="2025-01-02")
    p.add_argument("--valid-end", default="2025-06-26")
    # The common clean guard window (2025-07+ : OOS for both incumbent and candidate). The
    # end defaults to the comparison-origin window in docs/promotion/ (2025-07-01..2026-06-12),
    # NOT the bundle tail (2026-06-17): the backtest needs a T+1 fill bar after the last
    # signal date, so ending on the final bar raises `index N out of bounds`. Keeping this in
    # lockstep with the baseline JSON means a default run reproduces the committed origin.
    p.add_argument("--guard-start", default="2025-07-01")
    p.add_argument("--guard-end", default="2026-06-12")
    p.add_argument("--out", default=None, help="JSON output path.")
    args = p.parse_args(argv)
    profile = resolve_profile(args.profile)
    # instruments come FROM the profile (pre-registered, not tunable):
    # the dataset build below reads args.instruments.
    args.instruments = profile["instruments"]

    print(f"[oos-eval] model={args.model}")
    print(f"[oos-eval] profile={args.profile}  "
          f"universe={profile['instruments']}  "
          f"bench={profile['benchmark_code']}  "
          f"slippage={profile['slippage_bps']}bps  "
          f"cadence={profile['rebalance_cadence_days']}"
          f"/{profile['rebalance_anchor']}")
    print(f"[oos-eval] fit={args.fit_start}..{args.fit_end}  guard={args.guard_start}..{args.guard_end}")
    preds = _predictions_over_window(args)
    print(f"[oos-eval] predictions: {preds.shape[0]} rows, "
          f"{preds.index.get_level_values(0).nunique()} dates")

    signal = SignalAnalyzer.analyze(
        predictions=preds,
        config=SignalAnalysisConfig(forward_periods=(1, 5), topk=TOPK),
    )
    # Veto-bearing degeneracy scan runs on the EXECUTABLE stamp set (the
    # profile's rebalance days — same thinning as the backtest below);
    # the all-stamps scan is kept as a non-veto diagnostic for cadence
    # profiles (codex #387 r3).
    exec_preds = _executable_stamps(preds, args, profile)
    degen = _degeneracy_scan(exec_preds)
    degen_all: dict[str, Any] | None = None
    if exec_preds is not preds:
        degen_all = _degeneracy_scan(preds)
        print(f"[oos-eval] executable stamps: "
              f"{exec_preds.index.get_level_values(0).nunique()} of "
              f"{preds.index.get_level_values(0).nunique()} prediction dates")
    backtest = _backtest_metrics(preds, args, profile)

    result = {
        "model": args.model,
        # Profile provenance (PR-B): which pre-registered semantics this
        # eval ran under — downstream gates bind to these values.
        "profile": args.profile,
        "profile_knobs": profile,
        "fit_window": [args.fit_start, args.fit_end],
        "guard_window": [args.guard_start, args.guard_end],
        "n_pred_rows": int(preds.shape[0]),
        "n_pred_dates": int(preds.index.get_level_values(0).nunique()),
        "ic_1d": float(signal.ic_summary[1]["mean_ic"]),
        "ic_5d": float(signal.ic_summary[5]["mean_ic"]),
        "ic_ir_1d": float(signal.ic_summary[1]["ir"]),
        "ic_1d_positive_ratio": float(signal.ic_summary[1].get("ic_positive_ratio", float("nan"))),
        "mean_turnover": float(signal.turnover_stats.get("mean_turnover", float("nan"))),
        "backtest_excess_with_cost": backtest,
        # Veto-bearing scan: EXECUTABLE stamps only (profile-thinned).
        "degeneracy": degen,
        # Non-veto diagnostic (cadence profiles only): every raw stamp.
        "degeneracy_all_stamps": degen_all,
    }
    print("\n" + "=" * 64)
    print(json.dumps({k: v for k, v in result.items() if k != "degeneracy"}, indent=2, default=str))
    print("--- degeneracy scan ---")
    print(json.dumps(degen, indent=2, default=str))
    print("=" * 64)

    out = Path(args.out) if args.out else (
        PROJECT_ROOT / "output" / "oos_eval"
        # Profile in the default filename (codex #387 r1): the two
        # profiles are DIFFERENT gates over the same model/window — a
        # csi800_n5 eval must never overwrite the legacy artifact.
        / (f"{Path(args.model).stem}_{args.profile}"
           f"_{args.guard_start}_{args.guard_end}.json")
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"[oos-eval] wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
