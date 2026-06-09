"""CLI: daily stock recommendation (Phase B, ring 5).

Loads a trained Alpha158 + LGB model, builds the as-of-T cross-section
(data <= T only), scores + ranks, filters untradable names, and writes a
Top-K buy list for the next session (T+1 entry).

Usage::

    # latest PIT trading day, top 50, default model/paths
    python scripts/daily_recommend.py

    # a specific historical decision day, top 30
    python scripts/daily_recommend.py --as-of 2025-06-30 --topk 30

The model artifact + fit window default to the Phase B clean-PIT model
(D:/stock/phase_b_artifacts/alpha158_lgb_pit.pkl, train 2018-01-02 ->
2023-12-20). Override with --model / --fit-start / --fit-end.

NOTE: qlib's Alpha158 uses joblib's Windows 'spawn' workers, which
re-import this module per worker. The ``if __name__ == "__main__"`` guard
+ ``freeze_support()`` are MANDATORY (a missing guard fork-bombs — a known
Phase A trap).
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.logger import get_logger, setup_logging  # noqa: E402
from src.inference.daily_recommend import (  # noqa: E402
    DailyRecommendationError,
    RecommendationConfig,
    recommend,
    write_outputs,
)

_logger = get_logger("src.scripts.daily_recommend")

# Phase B clean-PIT defaults. Each is overridable via a QUANT_* env var (the
# default = the current path, so behaviour is unchanged when it is unset); the
# same var names drive the YAML configs (${QUANT_*:-default}), so setting e.g.
# QUANT_PROVIDER_URI once moves both the pipeline configs and this CLI. The CLI
# flags below still take precedence over the env default.
_DEFAULT_MODEL = os.environ.get(
    "QUANT_MODEL_PATH", "D:/stock/phase_b_artifacts/alpha158_lgb_pit.pkl"
)
_DEFAULT_PROVIDER = os.environ.get(
    "QUANT_PROVIDER_URI", "D:/qlib_data/my_cn_data_pit"
)
_DEFAULT_REGISTRY = os.environ.get(
    "QUANT_DELISTED_REGISTRY", "D:/qlib_data/tushare_raw/delisted_registry.parquet"
)
# Mirrors RecommendationConfig.name_source_parquet — the active-stocks snapshot
# is REQUIRED for the ST filter, so the CLI must let a non-default layout point
# at it (otherwise _validate_st_snapshot fails "file not found" with no escape).
_DEFAULT_NAME_SOURCE = os.environ.get(
    "QUANT_NAME_SOURCE", "D:/qlib_data/tushare_raw/active_stocks.parquet"
)
_DEFAULT_FIT_START = "2018-01-02"
_DEFAULT_FIT_END = "2023-12-20"


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Daily stock recommendation (Alpha158 + LGB, PIT).")
    p.add_argument("--as-of", default=None,
                   help="Decision date T (YYYY-MM-DD). Default = latest PIT trading day.")
    p.add_argument("--topk", type=int, default=50, help="Buy-list size (default 50).")
    p.add_argument("--out-dir", default="output/daily_recommend",
                   help="Output directory for csv/json (default output/daily_recommend).")
    p.add_argument("--model", default=_DEFAULT_MODEL, help="Model artifact path.")
    p.add_argument("--provider-uri", default=_DEFAULT_PROVIDER, help="PIT qlib provider_uri.")
    p.add_argument("--delisted-registry", default=_DEFAULT_REGISTRY,
                   help="PIT delisted registry parquet.")
    p.add_argument("--name-source", default=_DEFAULT_NAME_SOURCE,
                   help="Active-stocks snapshot parquet. REQUIRED for the ST "
                        "filter (supplies current names + the current-ST set).")
    p.add_argument("--st-max-age-days", type=int, default=7,
                   help="Max days the ST snapshot may lag the as-of date "
                        "before it is rejected as stale (default 7).")
    p.add_argument("--bundle-max-age-days", type=int, default=14,
                   help="Max CALENDAR days the qlib bundle's last trading day "
                        "may lag today before the price/feature data is "
                        "rejected as stale (default 14; covers A-share "
                        "holidays). Raise it for an intentional historical run.")
    p.add_argument("--instruments", default="csi300", help="Universe (default csi300).")
    p.add_argument("--fit-start", default=_DEFAULT_FIT_START,
                   help="Training fit-window start (must match the model).")
    p.add_argument("--fit-end", default=_DEFAULT_FIT_END,
                   help="Training fit-window end (must match the model).")
    p.add_argument(
        "--allow-holey-recommend", action="store_true",
        help="Recommend even if the bundle was built from a holey tushare fetch "
             "(or lacks a fetch-integrity stamp) (P3-4c). SEPARATE from the "
             "build-side --allow-holey-fetch: building partial data does not "
             "sanction trading on it, so this is a second explicit opt-in.")
    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = _build_arg_parser().parse_args(argv)

    config = RecommendationConfig(
        model_path=args.model,
        provider_uri=args.provider_uri,
        delisted_registry_path=args.delisted_registry,
        fit_start=args.fit_start,
        fit_end=args.fit_end,
        instruments=args.instruments,
        as_of_date=args.as_of,
        topk=args.topk,
        name_source_parquet=args.name_source,
        st_snapshot_max_age_days=args.st_max_age_days,
        bundle_max_age_days=args.bundle_max_age_days,
        out_dir=args.out_dir,
        allow_holey_recommend=args.allow_holey_recommend,
    )

    try:
        result = recommend(config)
    except DailyRecommendationError as exc:
        _logger.error("Daily recommendation failed: %s", exc)
        return 1

    paths = write_outputs(result, config.out_dir)

    # Terminal print — both time points always shown.
    print("=" * 64)
    print("  DAILY STOCK RECOMMENDATION")
    print(f"  as_of_date (data cutoff, T)   : {result.as_of_date}")
    print(f"  entry_date (suggested buy, T+1): {result.entry_date}")
    print(f"  universe={config.instruments}  scored={result.n_scored}  "
          f"untradable_masked={result.n_masked}  st_excluded={result.n_st_excluded}  "
          f"buy_list={len(result.picks)}")
    print("=" * 64)
    print(f"  {'rank':>4}  {'code':<10} {'score':>10}  name")
    for p in result.picks:
        print(f"  {p.rank:>4}  {p.stock_code:<10} {p.predicted_score:>10.5f}  {p.stock_name}")
    print("=" * 64)
    print(f"  buy-list csv : {paths['csv']}")
    print(f"  buy-list json: {paths['json']}")
    print(f"  full scored  : {paths['audit']}")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
