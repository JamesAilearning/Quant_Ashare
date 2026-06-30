"""CLI: daily stock recommendation (Phase B, ring 5).

Loads a trained Alpha158 + LGB model, builds the as-of-T cross-section
(data <= T only), scores + ranks, filters untradable names, and writes a
Top-K buy list for the next session (T+1 entry).

Usage::

    # latest PIT trading day, top 50, default model/paths
    python scripts/daily_recommend.py

    # a specific historical decision day, top 30
    python scripts/daily_recommend.py --as-of 2025-06-30 --topk 30

The model artifact defaults to the canonical clean-PIT model
(D:/stock/phase_b_artifacts/alpha158_lgb_pit.pkl). The inference
NORMALIZATION fit window is read from that model's companion meta
(``fit_start_for_inference`` / ``fit_end_for_inference``) so it tracks whatever
model is loaded — a promotion that writes the meta moves the window with it, and
if the meta lacks the window the run FAILS LOUD rather than silently using a
stale hardcoded one. Override with --model / --fit-start / --fit-end.
See _resolve_inference_fit_window / _model_meta_paths.

NOTE: qlib's Alpha158 uses joblib's Windows 'spawn' workers, which
re-import this module per worker. The ``if __name__ == "__main__"`` guard
+ ``freeze_support()`` are MANDATORY (a missing guard fork-bombs — a known
Phase A trap).
"""

from __future__ import annotations

import argparse
import json
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
def _model_meta_paths(model_path: str) -> list[Path]:
    """Both sidecar conventions that can carry the inference fit window, in
    priority order:

    1. ``<stem>.meta.json``     — the hand-curated PROMOTION meta (carries
       ``fit_*_for_inference``; written at promotion time).
    2. ``<model>.pkl.meta.json`` — the ModelTrainer sidecar
       (``src/core/model_trainer.py`` writes / ``ensemble.py`` reads this name).
       It does NOT currently carry the fit window, but a model produced by the
       pipeline ships ONLY this file — so we must inspect it to decide between
       "meta exists but lacks the window" (fail-loud) and "no meta at all"
       (fallback). Checking both stops the resolver from silently falling back
       to a stale window just because the promotion meta wasn't hand-written.
    """
    p = Path(model_path)
    return [p.with_suffix(".meta.json"), p.with_name(p.name + ".meta.json")]


def _resolve_inference_fit_window(
    model_path: str,
    cli_fit_start: str | None,
    cli_fit_end: str | None,
) -> tuple[str, str]:
    """Resolve the (fit_start, fit_end) normalization window for inference.

    Inference MUST normalize features on the model's OWN training fit window
    (training statistics) — a mismatch silently mis-normalizes every prediction.
    Rather than hardcode the window in a constant that drifts from the model on
    every promotion, derive it from the model's companion meta
    (``fit_start_for_inference`` / ``fit_end_for_inference`` — see
    :func:`_model_meta_paths` for the two sidecar names).

    Priority: explicit CLI flags > model meta. FAIL-CLOSED — a wrong/stale window
    can NEVER be used silently, and there is NO hardcoded fallback (a hardcoded
    window is the pre-promotion window and would mis-normalize a newer model):

    * a meta file EXISTS but no meta carries the fit window (e.g. only the
      ModelTrainer sidecar is present) -> raise.
    * a meta carries the field but it is not a non-empty string (a numeric year
      like ``2018`` would silently become 1970 via ``pd.Timestamp``) -> raise.
    * a meta is valid JSON but not an object, or unreadable -> raise.
    * the model ships NO meta at all AND --fit-start/--fit-end were not both
      supplied -> raise (codex P1: a lost/absent sidecar must not fall back to a
      stale window behind a log line; the operator must add a meta or pass both
      flags). The ONLY non-meta path that resolves is both CLI flags given.

    A CLI flag, when given, fills/overrides the corresponding side — so a meta
    that has only one side plus the matching ``--fit-*`` flag still resolves.
    """
    # Explicit on both sides => operator override; skip meta entirely.
    if cli_fit_start is not None and cli_fit_end is not None:
        return cli_fit_start, cli_fit_end

    existing = [p for p in _model_meta_paths(model_path) if p.is_file()]
    for meta_path in existing:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DailyRecommendationError(
                f"Model meta {meta_path} exists but could not be read/parsed "
                f"({type(exc).__name__}: {exc}). Cannot determine the inference "
                "normalization fit window — fix the meta or pass "
                "--fit-start/--fit-end explicitly."
            ) from exc
        if not isinstance(meta, dict):
            raise DailyRecommendationError(
                f"Model meta {meta_path} is not a JSON object (got "
                f"{type(meta).__name__}). Cannot read the inference fit window."
            )
        meta_start = meta.get("fit_start_for_inference")
        meta_end = meta.get("fit_end_for_inference")
        if meta_start is None and meta_end is None:
            continue  # this sidecar doesn't carry the window — try the next
        # This meta DOES carry (at least one side of) the window. Combine with any
        # CLI override, then require both sides to be non-empty strings.
        start = cli_fit_start or meta_start
        end = cli_fit_end or meta_end
        if not isinstance(start, str) or not start:
            raise DailyRecommendationError(
                f"Model meta {meta_path}: fit_start_for_inference is missing or not "
                f"a non-empty string (got {start!r}), and was not supplied via "
                "--fit-start. Refusing to fall back to a hardcoded window (it would "
                "mis-normalize). Fix the meta or pass --fit-start."
            )
        if not isinstance(end, str) or not end:
            raise DailyRecommendationError(
                f"Model meta {meta_path}: fit_end_for_inference is missing or not a "
                f"non-empty string (got {end!r}), and was not supplied via "
                "--fit-end. Refusing to fall back to a hardcoded window (it would "
                "mis-normalize). Fix the meta or pass --fit-end."
            )
        return start, end

    # A meta file exists but NONE carried the fit window -> fail-loud (do NOT
    # silently fall back to a stale hardcoded window).
    if existing:
        raise DailyRecommendationError(
            f"Model {model_path} has a meta sidecar "
            f"({', '.join(str(p) for p in existing)}) but none carries "
            "fit_start_for_inference / fit_end_for_inference. Refusing to fall "
            "back to a hardcoded fit window (it would mis-normalize the model). "
            "Add the fields to the model's promotion meta, or pass "
            "--fit-start/--fit-end explicitly."
        )

    # No meta at all (and not both CLI flags, per the top short-circuit) -> FAIL
    # CLOSED. A silent fallback to a hardcoded window would normalize a promoted
    # model on the STALE pre-promotion window (codex P1): a deployment that copied
    # only the .pkl, or a lost sidecar mount, must NOT emit recommendations on the
    # wrong window behind a mere log line.
    raise DailyRecommendationError(
        f"Model {model_path} has NO companion meta sidecar (looked for "
        f"{' / '.join(str(p) for p in _model_meta_paths(model_path))}) and "
        "--fit-start/--fit-end were not both supplied. Refusing to guess the "
        "inference normalization fit window — a hardcoded default would be the "
        "pre-promotion window and would mis-normalize a newer model. Write a "
        "<model>.meta.json with fit_start_for_inference / fit_end_for_inference, "
        "or pass BOTH --fit-start and --fit-end explicitly."
    )


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
    p.add_argument("--fit-start", default=None,
                   help="Training fit-window start (must match the model). "
                        "Default: read from <model>.meta.json fit_start_for_inference.")
    p.add_argument("--fit-end", default=None,
                   help="Training fit-window end (must match the model). "
                        "Default: read from <model>.meta.json fit_end_for_inference.")
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

    try:
        fit_start, fit_end = _resolve_inference_fit_window(
            args.model, args.fit_start, args.fit_end
        )
    except DailyRecommendationError as exc:
        _logger.error("Cannot resolve inference fit window: %s", exc)
        return 1

    config = RecommendationConfig(
        model_path=args.model,
        provider_uri=args.provider_uri,
        delisted_registry_path=args.delisted_registry,
        fit_start=fit_start,
        fit_end=fit_end,
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
