"""Deterministic frozen-score replay of the REGEN-2 total-return walk-forward baseline.

REGEN-2 re-baselined the walk-forward headline by switching the excess benchmark
from the SH000300 price index to the official **SH000300TR total-return** index
and retraining on the corrected 2026-06-17 bundle (see ``docs/baseline_regen2.md``).
This module makes that baseline **replay-anchored** (the
``v2-canonical-backtest-contract`` OpenSpec requirement): it replays the frozen
REGEN-2 post-ensemble per-fold prediction Series (frozen by
``scripts/regen/freeze_regen2_scores.py``) through the canonical ``BacktestRunner``
at the official semantics — T+1 execution, close-derived limits, PIT ST exclusion —
with the TR benchmark, NO retrain and NO full bundle. Because the scores are fixed
and the backtest + aggregation are deterministic (bootstrap seed 42), it
reproduces the committed REGEN-2 aggregate to machine precision.

This is the REGEN-2 sibling of ``replay_frozen_baseline.py`` (the REGEN-A
price-index anchor, which stays untouched). It reuses that module's shared
canonical-semantics constants + frozen loader; it differs only in the benchmark
(SH000300TR), the fold set (23 REAL folds — fold 22 = 2025Q4 completes on the
extended bundle, NO NaN tail), and the provenance strings.

HONEST framing (see docs/baseline_regen2.md): the REGEN-2 mean IR (~0.162) sits
BELOW the REGEN-A price-index 0.48 because the TR benchmark honestly subtracts the
~2.35%/yr reinvested dividend — the benchmark became honest, not a regression. The
point estimate is positive and IC is stable, but the 95% CI straddles zero: a
small, possibly-real but UNPROVEN edge, not predictive of live performance.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Reuse the REGEN-A module's shared canonical-semantics constants + loader (single
# source of truth; importing does NOT mutate the price-index anchor).
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
    load_frozen,
)
from src.core._json_utils import _sanitize_for_json  # noqa: E402
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
from src.core.signal_analyzer import SignalAnalysisConfig, SignalAnalyzer  # noqa: E402
from src.core.walk_forward._types import WalkForwardFold  # noqa: E402
from src.core.walk_forward.aggregate import compute_aggregate, extract_cost_metrics  # noqa: E402

# The ONE semantic difference vs the REGEN-A replay: the total-return benchmark.
BENCHMARK_TR = "SH000300TR"
N_FOLDS = 23  # 0..22, ALL real (fold 22 = 2025Q4 completes on the 2026-06-17 bundle)
FROZEN_SOURCE = (
    "REGEN-2 walk_forward_regen2_tr (fresh GPU retrain on bundle 2026-06-17, "
    "SH000300TR total-return; per-fold scores frozen by freeze_regen2_scores.py)"
)


def _replay_fold(fold_index: int, entry: dict[str, Any], namechange_path: str) -> WalkForwardFold:
    scores = entry["scores"]
    test = entry["test"]
    signal = SignalAnalyzer.analyze(
        predictions=scores,
        config=SignalAnalysisConfig(forward_periods=(1, 5), topk=TOPK),
    )
    request = CanonicalBacktestInput(
        predictions_ref=f"regen2_fold{fold_index}",
        evaluation_start=test["start"],
        evaluation_end=test["end"],
        account_config=CanonicalAccountConfig(init_cash=INIT_CASH),
        exchange_config=CanonicalExchangeConfig(
            freq="day",
            execution_price_kind=EXEC_PRICE,
            cost_model=CanonicalExchangeCostModel(
                commission_rate=COMMISSION,
                stamp_tax_schedule=CN_STAMP_TAX_SCHEDULE_DEFAULT,
                slippage_bps=SLIPPAGE_BPS,
                min_cost=MIN_COST,
            ),
            limit_threshold=LIMIT_THRESHOLD,
        ),
        adjust_mode=ADJUST_MODE_PRE,
        signal_to_execution_lag=LAG,
        benchmark_code=BENCHMARK_TR,
    )
    output = BacktestRunner.run(
        request=request,
        predictions=scores,
        topk=TOPK,
        n_drop=N_DROP,
        compute_baselines=False,
        namechange_path=namechange_path,
        require_st_mask=True,
    )
    if fold_index == 0:  # DIAG-FOLD0 (TEMPORARY — check 2: log the actual benchmark leg on Linux)
        ra = output.risk_analysis
        bench = list(ra.get("bench", {}).values())
        ret = list(ra.get("return", {}).values())
        fin = [v for v in bench if isinstance(v, (int, float)) and not math.isnan(v)]
        nan_b = sum(1 for v in bench if v is None or (isinstance(v, float) and math.isnan(v)))
        ewc = ra.get("excess_return_with_cost", {})
        print(f"DIAG-FOLD0 benchmark_code={request.benchmark_code} "
              f"bench: n={len(bench)} nan={nan_b} sum={sum(fin):.8f} mean={(sum(fin)/len(fin) if fin else float('nan')):.3e} head={bench[:3]} | "
              f"return: n={len(ret)} sum={sum(v for v in ret if isinstance(v,(int,float)) and not math.isnan(v)):.8f} | "
              f"excess_with_cost mean={ewc.get('mean')!r} annret={ewc.get('annualized_return')!r} ir={ewc.get('information_ratio')!r}",
              flush=True)
    ann, dd, ir = extract_cost_metrics(output.risk_analysis, fold_index)
    return WalkForwardFold(
        fold_index=fold_index,
        train_period=f"{entry['train']['start']}..{entry['train']['end']}",
        valid_period=f"{entry['valid']['start']}..{entry['valid']['end']}",
        test_period=f"{test['start']}..{test['end']}",
        ic_1d=float(signal.ic_summary[1]["mean_ic"]),
        ic_5d=float(signal.ic_summary[5]["mean_ic"]),
        annualized_return=ann,
        max_drawdown=dd,
        information_ratio=ir,
        prediction_shape=tuple(entry["prediction_shape"]),
    )


def replay_frozen_baseline_regen2(
    frozen_path: Path, provider_uri: str, namechange_path: str,
) -> dict[str, Any]:
    """Replay all 23 REGEN-2 folds at canonical TR semantics; return per-fold + aggregate.

    Returns ``{"folds": [WalkForwardFold, ...], "aggregate_metrics": {...}}``,
    schema-identical to a real WF run (``compute_aggregate``, bootstrap seed 42).
    """
    init_qlib_canonical(
        QlibRuntimeConfig(provider_uri=provider_uri, region="cn", data_adjust_mode=ADJUST_MODE_PRE)
    )
    # DIAG-READ (TEMPORARY — narrow read-bin vs backtest-excess-leg): raw qlib read
    # of the benchmark for fold-0's window, NO backtest. If empty/constant on Linux
    # but valued on Windows -> the platform diff is in the bin/calendar READ, not the
    # backtest excess leg.
    try:
        from qlib.data import D as _DIAG_D
        for _code in ("SH000300TR", "SH000300"):
            _df = _DIAG_D.features([_code], ["$close", "Ref($close,1)"],
                                   start_time="2020-03-25", end_time="2020-04-08", freq="day")
            _vals = _df["$close"].tolist() if (_df is not None and not _df.empty) else "EMPTY"
            print(f"DIAG-READ {_code}: shape={None if _df is None else _df.shape} $close={_vals}", flush=True)
    except Exception as _exc:  # noqa: BLE001 — diagnostic only
        print(f"DIAG-READ raised: {type(_exc).__name__}: {_exc}", flush=True)
    frozen = load_frozen(frozen_path)
    # REGEN-2 is exactly 23 REAL folds (0..22). Require that exact set — a
    # truncated/extended fixture must fail loudly, never be padded (unlike REGEN-A
    # there is NO excluded NaN tail to fabricate).
    expected = set(range(N_FOLDS))
    if set(frozen) != expected:
        raise ValueError(
            f"Frozen fold set {sorted(frozen)} != the expected {N_FOLDS} REGEN-2 "
            f"folds {sorted(expected)}. Refusing to replay a truncated/extended fixture."
        )
    # WARM-UP (cross-platform determinism). qlib's FIRST backtest after init
    # triggers a one-time "load calendar error: future=True; return current
    # calendar" benchmark-calendar fallback; that COLD first-fold state diverged
    # across platforms (Linux did not subtract the TR benchmark on fold 0, so its
    # excess ~= the absolute return). The cold first-fold value is therefore a
    # platform artifact, NOT a valid anchor. Run one throwaway warm-up backtest so
    # EVERY real fold — including fold 0 — runs in the warm state and reproduces
    # identically on every platform. The committed anchor is the warm value.
    first = min(frozen)
    _replay_fold(first, frozen[first], namechange_path)  # discarded: primes qlib state
    folds = [_replay_fold(i, frozen[i], namechange_path) for i in sorted(frozen)]
    # Refuse to anchor a NaN/non-finite fold (codex P2). This is a fail-LOUD guard,
    # NOT a way to drop a problem fold: REGEN-2 must REPRODUCE all 23 real folds.
    # A non-finite metric means the replay is broken — fix it, never exclude.
    for fold in folds:
        for metric in ("ic_1d", "ic_5d", "annualized_return", "max_drawdown", "information_ratio"):
            value = getattr(fold, metric)
            if value is None or not math.isfinite(float(value)):
                raise ValueError(
                    f"REGEN-2 fold {fold.fold_index} produced non-finite {metric}={value!r}. "
                    "Refusing to anchor a NaN fold — the replay must reproduce every fold, "
                    "not exclude it (no REGEN-A-style 22+NaN backdoor)."
                )
    aggregate = compute_aggregate(folds, seed=42)
    valid = aggregate.get("valid_folds_information_ratio")
    if valid != N_FOLDS:
        raise ValueError(
            f"aggregate valid_folds_information_ratio={valid!r} != {N_FOLDS}: not all "
            "23 REGEN-2 folds are real. Refusing to emit a < 23-fold REGEN-2 anchor."
        )
    return {"folds": folds, "aggregate_metrics": aggregate}


def _provenance(provider_uri: str) -> dict[str, Any]:
    return {
        "regen": (
            "REGEN-2 frozen-score replay of the REGEN-2 GPU-retrain fold scores "
            "(NO retrain, NO full bundle rebuild); replay-anchored per "
            "v2-canonical-backtest-contract."
        ),
        "config_file": "config_walk.yaml (semantics mirrored by frozen-score replay)",
        "config_keys": [
            "namechange_path", "signal_to_execution_lag", "limit_threshold",
            "benchmark_code", "instruments", "feature_handler", "topk", "n_drop",
        ],
        "frozen_source": FROZEN_SOURCE,
        "semantics": (
            "T+1 execution (PR-C) + close-derived limits (PR-D) + PIT ST exclusion "
            "(PR-F) + PRs #270-275 survivorship/PIT/freshness; fresh retrain on the "
            "corrected bundle, replayed at the TR benchmark."
        ),
        "benchmark_code": BENCHMARK_TR,
        "benchmark_note": (
            "official CSI300 total-return index (tushare H00300.CSI, ~2.35%/yr "
            "reinvested dividends). Excess return is measured against the honest, "
            "dividend-inclusive benchmark, superseding the SH000300 price-index basis "
            "used by REGEN-A."
        ),
        "provider_uri": provider_uri,
        "signal_to_execution_lag": LAG,
        "limit_threshold": LIMIT_THRESHOLD,
        "num_folds": N_FOLDS,
        "statistical_caveat": (
            "Point estimate is POSITIVE (mean fold IR ~0.162, pooled IR ~0.209) but "
            "the 95% bootstrap CI straddles zero (mean-fold SE ~0.42, within "
            "cross-fold noise): the edge is UNPROVEN, NOT disproven, and is NOT a "
            "prediction of live performance. The reduction vs the REGEN-A price-index "
            "baseline (0.48) is the benchmark becoming honest (the ~2.35%/yr "
            "dividend), not a regression. See docs/baseline_regen2.md."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="REGEN-2 total-return frozen-score replay generator")
    ap.add_argument("--frozen", default=str(
        _PROJECT_ROOT / "tests/regression/fixtures/regen2/frozen_fold_scores.pkl.gz"))
    ap.add_argument("--provider-uri", required=True)
    ap.add_argument("--namechange-path", required=True)
    ap.add_argument("--out", default=str(
        _PROJECT_ROOT / "tests/regression/fixtures/regen2/walk_forward_baseline_metrics.json"))
    args = ap.parse_args(argv)

    res = replay_frozen_baseline_regen2(Path(args.frozen), args.provider_uri, args.namechange_path)
    agg = res["aggregate_metrics"]
    per_fold = [
        {
            "fold_index": f.fold_index, "test_period": f.test_period,
            "ic_1d": f.ic_1d, "ic_5d": f.ic_5d, "annualized_return": f.annualized_return,
            "max_drawdown": f.max_drawdown, "information_ratio": f.information_ratio,
        }
        for f in res["folds"]
    ]
    payload = {
        "_provenance": _provenance(args.provider_uri),
        "aggregate_metrics": dict(agg),
        "per_fold": per_fold,
    }
    sanitised = _sanitize_for_json(payload)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(sanitised, indent=2, sort_keys=False, default=str, allow_nan=False) + "\n",
        encoding="utf-8")
    print(f"mean_information_ratio = {agg['mean_information_ratio']}")
    print(f"valid_folds_information_ratio = {agg['valid_folds_information_ratio']}")
    print(f"mean_annualized_return = {agg['mean_annualized_return']}")
    print(f"worst_drawdown = {agg['worst_drawdown']}")
    print(f"written -> {args.out}")
    return 0


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
