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

HONEST framing (see docs/baseline_regen2.md): the REGEN-2 canonical-stack mean fold
IR (~0.28, numpy<2; the headline is inflated by fold-0's degenerate-score tie-break
artifact — the honest edge is ~0.16-0.20) sits BELOW the REGEN-A price-index 0.48
because the TR benchmark honestly subtracts the ~2.35%/yr reinvested dividend — the
benchmark became honest, not a regression. The point estimate is positive and IC is
stable, but the 95% CI straddles zero: a small, possibly-real but UNPROVEN, not
disproven edge, not predictive of live performance. (NB: the ~0.16 figure in older
notes was the OFF-PIN numpy-2.4.4 mean — see fold0_known_limitation.)
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import sys
import warnings
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

# DEPENDENCY-STACK guard (NOT a platform guard — the earlier "Windows is the correct
# side" hypothesis was DISPROVEN: both CI runners, Linux and Windows, agree; the split
# is numpy<2 vs numpy 2.x). This anchor reproduces to 1e-6 ONLY on the project's
# CANONICAL dependency stack (pyproject pins: numpy<2, scipy<1.14, pandas<2.3) — the
# stack CI runs. Root cause: fold-0's frozen scores are DEGENERATE — ~39 discrete
# value-buckets over 300 stocks (every OTHER fold has 300 continuous unique scores),
# so the topk=50 cutoff sits inside a tie block and the selected names depend on
# numpy's SORT tie-break, which differs across numpy majors. This is a PRE-EXISTING
# lineage condition (REGEN-A's fold-0 is identically degenerate), filed to phase-6
# signal-quality, NOT introduced here. Generating or replaying on an OFF-PIN stack
# (e.g. an off-pin numpy 2.x dev box — the 2026-06 incident) therefore bakes/produces
# a DIFFERENT, non-reproducible fold-0. Guard both paths: generation HARD-fails
# off-pin (never bake a non-canonical anchor); replay WARNS loud off-pin.
_CANONICAL_MAX = {"numpy": (2, 0), "scipy": (1, 14), "pandas": (2, 3)}  # pyproject upper bounds

_OFF_PIN_MSG = (
    "REGEN-2 replay is OFF the canonical dependency pin: {off}. The deterministic "
    "anchor reproduces to 1e-6 ONLY on the canonical stack (pyproject: numpy<2, "
    "scipy<1.14, pandas<2.3) that CI runs. fold-0's scores are degenerate (a tie block "
    "at the topk cutoff), so its selected names — hence its excess — depend on numpy's "
    "sort tie-break, which differs across numpy majors. On this stack fold-0 will NOT "
    "match the committed anchor. Use a canonical-pinned venv. (See docs/baseline_regen2.md.)"
)


def _dep_stack() -> dict[str, str]:
    import numpy
    import pandas
    import scipy
    return {
        "numpy": numpy.__version__, "scipy": scipy.__version__,
        "pandas": pandas.__version__, "python": platform.python_version(),
    }


def _off_canonical_pin(versions: dict[str, str]) -> list[str]:
    def _mm(v: str) -> tuple[int, int]:
        parts = (v.split(".") + ["0"])[:2]
        return (int(parts[0]), int(parts[1]) if parts[1].isdigit() else 0)
    off: list[str] = []
    for pkg, hi in _CANONICAL_MAX.items():
        if _mm(versions[pkg]) >= hi:
            off.append(f"{pkg}={versions[pkg]} (canonical pin requires <{hi[0]}.{hi[1]})")
    return off


def _assert_canonical_dep_stack() -> dict[str, str]:
    """Generation guard (meta-hardening): fail LOUD if generating off the canonical pin.

    The 2026-06 incident: the anchor was generated on an off-pin numpy 2.4.4 dev box,
    baking a fold-0 value CI (numpy<2) could never reproduce. Never again.
    """
    versions = _dep_stack()
    off = _off_canonical_pin(versions)
    if off:
        raise RuntimeError(
            "Refusing to GENERATE the REGEN-2 anchor off the canonical dependency pin. "
            + _OFF_PIN_MSG.format(off="; ".join(off))
        )
    return versions
FROZEN_SOURCE = (
    "REGEN-2 walk_forward_regen2_tr (fresh GPU retrain on bundle 2026-06-17, "
    "SH000300TR total-return; per-fold scores frozen by freeze_regen2_scores.py)"
)


def _replay_fold(
    fold_index: int, entry: dict[str, Any], namechange_path: str,
    pit_provider: Any | None = None,
) -> WalkForwardFold:
    scores = entry["scores"]
    test = entry["test"]
    signal = SignalAnalyzer.analyze(
        predictions=scores,
        config=SignalAnalysisConfig(forward_periods=(1, 5), topk=TOPK),
        # Audit P2 PR-2 (intentional semantic change, deliberate re-sign): the
        # anchor replays the CANONICAL semantics — when the registry fixture is
        # supplied, IC consumes PIT-masked closes exactly like production.
        pit_provider=pit_provider,
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
    delisted_registry_path: str | None = None,
) -> dict[str, Any]:
    """Replay all 23 REGEN-2 folds at canonical TR semantics; return per-fold + aggregate.

    Returns ``{"folds": [WalkForwardFold, ...], "aggregate_metrics": {...}}``,
    schema-identical to a real WF run (``compute_aggregate``, bootstrap seed 42).

    Dependency-stack scope: the 1e-6 reproduction is guaranteed ONLY on the canonical
    pinned stack (numpy<2, scipy<1.14, pandas<2.3 — see ``_CANONICAL_MAX`` / the module
    docstring). On an off-pin stack this emits a loud warning because fold-0's
    degenerate scores make its selection numpy-sort-tie-break-dependent, so fold-0 will
    not match the committed anchor (NOT a silent wrong value).
    """
    off = _off_canonical_pin(_dep_stack())
    if off:
        warnings.warn(_OFF_PIN_MSG.format(off="; ".join(off)), RuntimeWarning, stacklevel=2)
    init_qlib_canonical(
        QlibRuntimeConfig(provider_uri=provider_uri, region="cn", data_adjust_mode=ADJUST_MODE_PRE)
    )
    # Audit P2 PR-2: mirror the canonical engines' wiring (same labels -> the
    # provider's init is an idempotent no-op). None/empty -> legacy WARN path.
    from src.core.pit_wiring import build_pit_provider
    pit_provider = build_pit_provider(
        delisted_registry_path=delisted_registry_path or "",
        provider_uri=provider_uri,
        data_adjust_mode=ADJUST_MODE_PRE,
        region="cn",
    )
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
    folds = [
        _replay_fold(i, frozen[i], namechange_path, pit_provider=pit_provider)
        for i in sorted(frozen)
    ]
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


def _provenance(provider_uri: str, dep_stack: dict[str, str]) -> dict[str, Any]:
    return {
        "regen": (
            "REGEN-2 frozen-score replay of the REGEN-2 GPU-retrain fold scores "
            "(NO retrain, NO full bundle rebuild); replay-anchored per "
            "v2-canonical-backtest-contract."
        ),
        "dependency_stack": dep_stack,
        "dependency_stack_note": (
            "Generated on the project's CANONICAL pinned stack (pyproject: numpy<2, "
            "scipy<1.14, pandas<2.3) — the stack CI runs. This anchor reproduces to "
            "1e-6 ONLY on this stack; see fold0_known_limitation. A gen-env==canonical "
            "assertion (scripts/regen/replay_frozen_baseline_regen2._assert_canonical_"
            "dep_stack) fails generation LOUD off-pin, closing the 2026-06 hole where "
            "an off-pin numpy 2.4.4 box baked a fold-0 CI could never reproduce."
        ),
        "fold0_known_limitation": (
            "KNOWN LIMITATION (fail-loud, not silently accepted): fold-0's frozen "
            "predictions are DEGENERATE — ~39 discrete value-buckets over 300 stocks "
            "(every OTHER fold has 300 continuous unique scores; fold-0 alone, 56/59 "
            "days). The topk=50 cutoff therefore lands inside a tie block, so the "
            "selected names depend on numpy's SORT tie-break, which differs across "
            "numpy majors. fold-0 excess (ann +0.1336 / IR +1.767) is thus the "
            "CANONICAL-STACK deterministic value, NOT a cross-implementation-robust "
            "one. PRE-EXISTING across the replay lineage (REGEN-A's fold-0 is "
            "byte-identically degenerate) — NOT introduced by REGEN-2. Suspected cause: "
            "2020Q2 (COVID) test-window feature gaps / suspensions routing many stocks "
            "to one model leaf. Root-cause investigation is filed to PHASE-6 "
            "signal-quality, isolated from this anchor (changing the tie-break = "
            "changing the alpha, which would move other folds). Folds 1..22 are "
            "numpy-version-insensitive and reproduce byte-identically."
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
            "Point estimate POSITIVE but the 95% bootstrap CI straddles zero (within "
            "cross-fold noise, mean-fold SE ~0.4): the edge is UNPROVEN, NOT disproven, "
            "and is NOT a prediction of live performance. Canonical-stack mean fold IR "
            "is in aggregate_metrics (~0.28). NOTE — the rise from the off-pin "
            "baseline's ~0.16 to ~0.28 is 100% a fold-0 ARTIFACT, NOT signal: fold-0's "
            "single-fold IR swings -0.889 -> +1.767 (swing ~2.66) because its degenerate "
            "scores (~39 value-buckets / 261 ties over 300 stocks) select a DIFFERENT "
            "stock set under the canonical numpy sort tie-break (see "
            "fold0_known_limitation). [IR and annualized_return are SEPARATE metrics — "
            "fold-0's ann moves -0.0616 -> +0.1336; do not conflate the two.] folds "
            "1..22 are byte-identical. fold-0's extreme swing is exactly why this fold "
            "is not evidence; the honest edge stays ~0.16-0.2 and statistically "
            "unproven. The reduction vs the REGEN-A price-index baseline (0.48) is the "
            "benchmark becoming honest (the ~2.35%/yr dividend), not a regression. See "
            "docs/baseline_regen2.md."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="REGEN-2 total-return frozen-score replay generator")
    ap.add_argument("--frozen", default=str(
        _PROJECT_ROOT / "tests/regression/fixtures/regen2/frozen_fold_scores.pkl.gz"))
    ap.add_argument("--provider-uri", required=True)
    ap.add_argument("--namechange-path", required=True)
    ap.add_argument(
        "--delisted-registry-path", default=None,
        help="delisted registry parquet for PIT-masked IC (audit P2 PR-2); "
        "omit for the legacy pre-PIT replay semantics",
    )
    # Canonical root baseline (PR-2 promoted REGEN-2 here). The frozen scores stay at
    # fixtures/regen2/; only the baseline JSON is the canonical root.
    ap.add_argument("--out", default=str(
        _PROJECT_ROOT / "tests/regression/fixtures/walk_forward_baseline_metrics.json"))
    args = ap.parse_args(argv)

    # Meta-hardening: refuse to GENERATE the anchor off the canonical pin (the 2026-06
    # off-pin-numpy-2.4.4 incident). CI runs the canonical stack; the anchor must too.
    dep_stack = _assert_canonical_dep_stack()
    res = replay_frozen_baseline_regen2(
        Path(args.frozen), args.provider_uri, args.namechange_path,
        delisted_registry_path=args.delisted_registry_path,
    )
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
        # Machine-readable CANONICAL marker: PR-2 promoted the REGEN-2 replay anchor to
        # the canonical root baseline (fixtures/walk_forward_baseline_metrics.json). The
        # replay is reproduced CI-real on the canonical numpy<2 stack by
        # test_walk_forward_replay_baseline_regen2 (mini-bundle); the frozen scores live
        # at fixtures/regen2/. REGEN-A is preserved as the SH000300 price-index control.
        "_status": {
            "canonical": True,
            "role": "CANONICAL walk-forward regression baseline (REGEN-2, SH000300TR total-return)",
            "note": (
                "Promoted from the PR-1 replay anchor (PR-2). Reproduced CI-real on the "
                "canonical numpy<2 stack by test_walk_forward_replay_baseline_regen2 "
                "(mini-bundle); frozen scores at fixtures/regen2/frozen_fold_scores.pkl.gz. "
                "REGEN-A is preserved as the SH000300 price-index control at "
                "fixtures/regen_a/walk_forward_baseline_metrics_regen_a.json."
            ),
        },
        "_provenance": _provenance(args.provider_uri, dep_stack),
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
