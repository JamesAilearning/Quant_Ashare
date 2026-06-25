"""Deterministic frozen-score replay of the REGEN-A corrected walk-forward baseline.

REGEN-A re-baselined the walk-forward headline by REPLAYING the C1 round's frozen
per-fold prediction Series through the CURRENT canonical ``BacktestRunner``
semantics (T+1 execution, close-derived price limits, PIT ST exclusion) — NO
retrain, NO bundle rebuild. This module is the single source of that replay:

  * the generator (``main``) writes the committed baseline JSON, and
  * ``tests/regression/test_walk_forward_replay_baseline`` calls
    :func:`replay_frozen_baseline` and asserts bit-exact reproduction.

Because the frozen scores are fixed and the backtest + aggregation are
deterministic (bootstrap seed 42), the same bundle reproduces the same numbers
to machine precision — hence the regression test uses a TIGHT tolerance, unlike
the retrain-based ``test_walk_forward_aggregate_baseline`` (which re-trains and
must stay loose; see ``docs/baseline_20260616.md``).

IMPORTANT framing (see the baseline doc): the corrected headline mean IR
(~0.4815) sits ABOVE the old 0.3672 ONLY because the old metric was T+2-stale
and let limit-up phantom fills through. The shift is outlier-driven and within
the cross-fold noise (mean-fold-IR SE ~= 0.41). It is a METRIC correction, not a
strategy improvement, and does NOT predict better live performance.
"""
from __future__ import annotations

import argparse
import gzip
import json
import pickle
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

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

# Canonical replay semantics — must mirror config_walk.yaml (the official WF path).
LAG = 1                 # T+1 execution (PR-C)
LIMIT_THRESHOLD = 0.095  # close-derived A-share main-board limit (PR-D)
TOPK = 50
N_DROP = 5
INIT_CASH = 100_000_000
COMMISSION = 0.0005
SLIPPAGE_BPS = 5.0
MIN_COST = 5.0
BENCHMARK = "SH000300"   # price index; total-return SH000300TR deferred to REGEN-2
EXEC_PRICE = "close"

# Provenance of the frozen scores (C1 round; see docs/baseline_20260616.md).
FROZEN_SOURCE = "phase_c1 walk_forward_c1_gpu (run 2026-06-01, config_fingerprint 22e0682cfe0c24e5)"
TOTAL_FOLDS = 23  # 22 valid + fold 22 (bundle-tail T+1 overrun, excluded as NaN), matching the C1 run


def load_frozen(frozen_path: Path) -> dict[int, dict[str, Any]]:
    with gzip.open(frozen_path, "rb") as fh:
        data: dict[int, dict[str, Any]] = pickle.load(fh)
    return data


def _replay_fold(fold_index: int, entry: dict[str, Any], namechange_path: str) -> WalkForwardFold:
    scores = entry["scores"]
    test = entry["test"]
    signal = SignalAnalyzer.analyze(
        predictions=scores,
        config=SignalAnalysisConfig(forward_periods=(1, 5), topk=TOPK),
    )
    ic_1d = float(signal.ic_summary[1]["mean_ic"])
    ic_5d = float(signal.ic_summary[5]["mean_ic"])
    request = CanonicalBacktestInput(
        predictions_ref=f"regen_a_fold{fold_index}",
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
        benchmark_code=BENCHMARK,
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
    ann, dd, ir = extract_cost_metrics(output.risk_analysis, fold_index)
    return WalkForwardFold(
        fold_index=fold_index,
        train_period=f"{entry['train']['start']}~{entry['train']['end']}",
        valid_period=f"{entry['valid']['start']}~{entry['valid']['end']}",
        test_period=f"{test['start']}~{test['end']}",
        ic_1d=ic_1d,
        ic_5d=ic_5d,
        annualized_return=ann,
        max_drawdown=dd,
        information_ratio=ir,
        prediction_shape=tuple(entry["prediction_shape"]),
    )


def replay_frozen_baseline(
    frozen_path: Path, provider_uri: str, namechange_path: str,
) -> dict[str, Any]:
    """Replay all frozen folds at canonical semantics; return per-fold + aggregate.

    Returns ``{"folds": [WalkForwardFold, ...], "aggregate_metrics": {...}}``.
    The aggregate is built by the project's own :func:`compute_aggregate`
    (bootstrap seed 42) so it is schema-identical to a real WF run.
    """
    init_qlib_canonical(
        QlibRuntimeConfig(provider_uri=provider_uri, region="cn", data_adjust_mode=ADJUST_MODE_PRE)
    )
    frozen = load_frozen(frozen_path)
    # The REGEN-A anchor is defined as exactly the 22 valid C1 folds (0..21) plus
    # fold 22 as the excluded tail. Require the frozen keys to be EXACTLY that
    # valid set before padding — otherwise a truncated/extended fixture (e.g.
    # folds 0..20) would be silently expanded with NaN placeholders, dropping a
    # real valid fold from the anchor while still self-reproducing (Codex P2).
    expected_valid = set(range(TOTAL_FOLDS - 1))  # 0..21
    if set(frozen) != expected_valid:
        raise ValueError(
            f"Frozen fold set {sorted(frozen)} != the expected {len(expected_valid)} "
            f"valid folds {sorted(expected_valid)}. Refusing to replay: padding the "
            "difference as the NaN tail would silently drop/fabricate folds."
        )
    folds: list[WalkForwardFold] = []
    for fold_index in sorted(frozen):
        folds.append(_replay_fold(fold_index, frozen[fold_index], namechange_path))
    # fold 22 failed on the bundle-tail T+1 overrun in the C1 run; carry it as the
    # single excluded NaN fold so num_folds (23) / valid (22) match that run.
    nan = float("nan")
    folds.append(WalkForwardFold(
        fold_index=TOTAL_FOLDS - 1, train_period="", valid_period="", test_period="",
        ic_1d=nan, ic_5d=nan, annualized_return=nan, max_drawdown=nan,
        information_ratio=nan, prediction_shape=(0,),
    ))
    aggregate = compute_aggregate(folds, seed=42)
    return {"folds": folds, "aggregate_metrics": aggregate}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frozen", default=str(
        _PROJECT_ROOT / "tests/regression/fixtures/regen_a/frozen_fold_scores.pkl.gz"))
    ap.add_argument("--provider-uri", required=True)
    ap.add_argument("--namechange-path", required=True)
    # PR-2: the canonical root is now REGEN-2 (total-return). REGEN-A is the preserved
    # SH000300 price-index CONTROL — this generator writes the control copy, not the root.
    ap.add_argument("--out", default=str(
        _PROJECT_ROOT / "tests/regression/fixtures/regen_a/walk_forward_baseline_metrics_regen_a.json"))
    args = ap.parse_args(argv)

    res = replay_frozen_baseline(Path(args.frozen), args.provider_uri, args.namechange_path)
    agg = res["aggregate_metrics"]
    per_fold = [
        {
            "fold_index": f.fold_index,
            "test_period": f.test_period,
            "ic_1d": f.ic_1d,
            "ic_5d": f.ic_5d,
            "annualized_return": f.annualized_return,
            "max_drawdown": f.max_drawdown,
            "information_ratio": f.information_ratio,
        }
        for f in res["folds"]
    ]
    payload = {
        "_provenance": {
            "regen": "REGEN-A frozen-score replay (NO retrain, NO bundle rebuild)",
            # config_file records the canonical config whose semantics the replay
            # mirrors (T+1 / limits / ST). The baseline VALUE comes from replaying
            # the C1 frozen scores, NOT from a fresh run of this config.
            "config_file": "config_walk.yaml (semantics mirrored by frozen-score replay)",
            # The canonical parameters actually applied in the replay. Includes
            # ``namechange_path`` so the ST-provenance consistency guard
            # (test_baseline_st_provenance_consistency) confirms the baseline was
            # produced ST-excluded, matching config_walk.yaml.
            "config_keys": [
                "namechange_path", "signal_to_execution_lag", "limit_threshold",
                "benchmark_code", "instruments", "feature_handler", "topk", "n_drop",
            ],
            "frozen_source": FROZEN_SOURCE,
            "semantics": "T+1 execution (PR-C) + close-derived limits (PR-D) + PIT ST exclusion (PR-F)",
            "benchmark_code": BENCHMARK,
            "benchmark_note": "price index; total-return SH000300TR deferred to REGEN-2 (excess will revise down ~2-2.5pp)",
            "provider_uri": args.provider_uri,
            "signal_to_execution_lag": LAG,
            "limit_threshold": LIMIT_THRESHOLD,
            "num_folds": TOTAL_FOLDS,
            "statistical_caveat": (
                "mean-fold-IR SE ~= 0.41 over 22 folds; the +0.18 shift vs the old "
                "T+2 baseline is outlier-driven (folds 12/11/10) and within noise "
                "(mean delta ~= 1 SE). This is a METRIC correction, NOT a strategy "
                "improvement, and does NOT predict better live performance. "
                "See docs/baseline_20260616.md."
            ),
        },
        "aggregate_metrics": dict(agg),
        "per_fold": per_fold,
    }
    # Sanitize NaN/Inf -> null (fold 22 + timing block carry NaN) and refuse to
    # emit non-standard tokens, matching the repo's shared JSON writers so jq /
    # browsers / strict parsers can read the committed fixture (Codex P2).
    sanitised = _sanitize_for_json(payload)
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
