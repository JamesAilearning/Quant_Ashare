"""Daily stock-recommendation inference core.

``recommend(config)`` loads a trained qlib model, builds the **as-of-T**
Alpha158 feature cross-section (data ``<= T`` only), scores it, filters
out untradable names (suspension / one-price-lock) via the existing
microstructure mask, ranks by score, and returns the Top-K buy list for
the next session (``T+1`` entry).

Look-ahead safety (the red line)
--------------------------------
* The Alpha158 handler is built with ``end_time = T`` so qlib loads no
  bar dated ``> T``; every Alpha158 feature for ``T`` is a function of
  data ``<= T``.
* Inference processors (e.g. RobustZScoreNorm) are fit on the TRAINING
  window (``fit_start..fit_end``), not on ``T`` — no statistic learns
  from the decision date.
* Only ``col_set="feature"`` with the INFER data key (``DK_I``) is
  prepared. qlib puts ``DropnaLabel`` in the LEARN processors (``DK_L``)
  only, so the NaN label on the latest day does NOT drop the row — the
  list cannot come back empty for that reason. The label is never read.

This module imports and reuses existing components; it modifies none of
them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from src.core.logger import get_logger
from src.core.microstructure_mask import (
    MicrostructureMaskError,
    compute_unavailable_mask,
)
from src.core.qlib_runtime import QlibRuntimeConfig, init_qlib_canonical

_logger = get_logger(__name__)


class DailyRecommendationError(RuntimeError):
    """Raised when a daily recommendation cannot be produced (bad
    as-of date, empty cross-section, model load failure, etc.).
    Fail-closed: never return a silently empty / partial list."""


@dataclass(frozen=True)
class RecommendationConfig:
    """Inputs for one daily-recommendation run.

    ``fit_start`` / ``fit_end`` MUST match the model's training fit
    window so inference normalization uses training statistics (no
    leakage, and distribution-consistent with training). For the Phase B
    artifact these are 2018-01-02 / 2023-12-20.
    """

    model_path: str
    provider_uri: str
    delisted_registry_path: str
    fit_start: str
    fit_end: str
    instruments: str = "csi300"
    as_of_date: str | None = None  # None -> latest PIT trading day
    topk: int = 50
    region: str = "cn"
    # The PIT bins are POST-adjusted (close × adj_factor) — see
    # PITDataProvider._init_qlib and qlib_bin_builder. We tag the canonical
    # runtime post_adjusted to match the bins AND PITDataProvider (which
    # pins post_adjusted), so both can share one qlib session. NOTE: the
    # tag is NOT passed to qlib.init (it is a provenance/consistency marker
    # only), so feature VALUES are identical to a pre_adjusted-tagged run —
    # the Step 1 model (trained under the pre tag) predicts identically.
    adjust_mode: str = "post_adjusted"
    # Best-effort current-name source (tushare stock_basic dump). Names
    # are not in PIT bins; missing file -> blank names + a logged note.
    name_source_parquet: str | None = (
        "D:/qlib_data/tushare_raw/active_stocks.parquet"
    )
    out_dir: str = "output/daily_recommend"


@dataclass(frozen=True)
class RecommendationPick:
    rank: int
    stock_code: str
    stock_name: str
    predicted_score: float
    tradable_flag: bool
    unavailable_reason: str  # "" when tradable


@dataclass(frozen=True)
class DailyRecommendationResult:
    as_of_date: str       # T — data cutoff
    entry_date: str       # T+1 trading day — suggested entry
    picks: tuple[RecommendationPick, ...]
    n_scored: int         # tradable + non-NaN-score candidates considered
    n_masked: int         # dropped by tradability mask
    scored_frame: pd.DataFrame  # full audit frame (incl. masked names)


# --------------------------------------------------------------------------
# Date resolution
# --------------------------------------------------------------------------
def resolve_dates(
    as_of_date: str | None,
    calendar: list[Any] | None = None,
) -> tuple[str, str]:
    """Resolve (as_of_date T, entry_date T+1) against the trading calendar.

    * ``as_of_date is None`` -> T = the LATEST trading day that still has a
      following session in the calendar (i.e. the second-to-last day when
      the calendar ends at the data cutoff). This keeps the no-argument
      CLI usable: the last calendar day cannot be a decision day because
      no T+1 session exists for it in the bundle.
    * Otherwise T must be a real trading day on/before the calendar end.
    * Entry date is the first trading day strictly after T. If an explicit
      T is the last calendar day, T+1 does not exist -> explicit error
      (never silently falls back to T).

    ``calendar`` may be supplied (list of dates) to keep this pure and
    unit-testable; when ``None`` it is fetched from the qlib calendar.
    """
    if calendar is None:
        from qlib.data import D
        calendar = list(D.calendar())
    calendar = [pd.Timestamp(d) for d in calendar]
    if not calendar:
        raise DailyRecommendationError("qlib calendar is empty.")
    last = calendar[-1]

    if as_of_date is None:
        if len(calendar) < 2:
            raise DailyRecommendationError(
                "calendar has fewer than 2 trading days; cannot form a "
                "(decision T, entry T+1) pair for the default as-of."
            )
        # Latest day that still has a next session (= the bundle's last day
        # cannot be a decision day; its entry T+1 is not in the bundle).
        t = calendar[-2]
    else:
        try:
            t = pd.Timestamp(as_of_date)
        except (ValueError, TypeError) as exc:
            raise DailyRecommendationError(
                f"as-of date {as_of_date!r} is not a parseable date "
                "(expected YYYY-MM-DD)."
            ) from exc
        if pd.isna(t):
            raise DailyRecommendationError(
                f"as-of date {as_of_date!r} is not a parseable date "
                "(expected YYYY-MM-DD)."
            )
        if t not in set(calendar):
            raise DailyRecommendationError(
                f"as-of date {t.date()} is not a trading day in the PIT "
                f"calendar (calendar span {calendar[0].date()} .. "
                f"{last.date()}). Pass a real trading day."
            )

    later = [d for d in calendar if d > t]
    if not later:
        raise DailyRecommendationError(
            f"as-of date {t.date()} is the last day in the PIT calendar "
            f"({last.date()}); there is no next trading day (T+1) to enter "
            f"on. Re-run with an earlier --as-of, or extend the bundle."
        )
    entry = later[0]
    return t.strftime("%Y-%m-%d"), entry.strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# Feature construction (as-of T, leak-free)
# --------------------------------------------------------------------------
def _build_asof_dataset(
    config: RecommendationConfig, as_of_date: str,
) -> tuple[Any, pd.DataFrame]:
    """Build the as-of-``T`` Alpha158 ``DatasetH`` once and return it with
    its INFER feature frame.

    Shared by :func:`recommend` (which needs the dataset for
    ``model.predict``) and :func:`prepare_asof_features` (which needs only
    the frame), so the (expensive) Alpha158 handler is built exactly once.

    ``end_time=T`` -> qlib loads no bar dated ``> T``. ``fit_end_time``
    pins normalization to the training window. The INFER data key
    (``DK_I``) means ``DropnaLabel`` (a LEARN processor) is NOT applied,
    so a NaN label on the latest day does NOT drop the row — only the
    (unused) features matter.
    """
    from qlib.contrib.data.handler import Alpha158
    from qlib.data.dataset import DatasetH
    from qlib.data.dataset.handler import DataHandlerLP

    handler = Alpha158(
        instruments=config.instruments,
        start_time=config.fit_start,   # need history back to fit window
        end_time=as_of_date,           # <-- end at T: qlib loads no bar > T
        fit_start_time=config.fit_start,
        fit_end_time=config.fit_end,   # <-- normalization fit = training window
    )
    dataset = DatasetH(handler=handler, segments={"infer": [as_of_date, as_of_date]})
    feature_frame = dataset.prepare(
        "infer", col_set="feature", data_key=DataHandlerLP.DK_I,
    )
    return dataset, feature_frame


def prepare_asof_features(config: RecommendationConfig, as_of_date: str) -> pd.DataFrame:
    """Build the as-of-``T`` Alpha158 INFER feature frame.

    Indexed by ``(datetime, instrument)``, rows for ``as_of_date`` only.
    Exposed separately from :func:`recommend` so the look-ahead-bias test
    can assert ``frame.index.datetime.max() == as_of_date`` directly.
    """
    return _build_asof_dataset(config, as_of_date)[1]


# --------------------------------------------------------------------------
# Names (best-effort, current name only)
# --------------------------------------------------------------------------
def _qlib_code_to_ts_code(code: str) -> str:
    """``SH600000`` -> ``600000.SH`` (tushare ts_code format)."""
    exch, num = code[:2].upper(), code[2:]
    return f"{num}.{exch}"


def _load_name_map(parquet_path: str | None) -> dict[str, str]:
    if not parquet_path:
        return {}
    p = Path(parquet_path)
    if not p.exists():
        _logger.info(
            "name source %s not found; stock_name will be blank "
            "(names are not in PIT bins).", parquet_path,
        )
        return {}
    df = pd.read_parquet(p)
    if "ts_code" not in df.columns or "name" not in df.columns:
        _logger.info("name source %s lacks ts_code/name columns; skipping.", parquet_path)
        return {}
    return {str(r.ts_code): str(r.name) for r in df.itertuples(index=False)}


# --------------------------------------------------------------------------
# Main entry
# --------------------------------------------------------------------------
def recommend(config: RecommendationConfig) -> DailyRecommendationResult:
    init_qlib_canonical(QlibRuntimeConfig(
        provider_uri=config.provider_uri,
        region=config.region,
        data_adjust_mode=config.adjust_mode,
    ))

    as_of_date, entry_date = resolve_dates(config.as_of_date)
    _logger.info(
        "Daily recommendation: as_of(T)=%s, entry(T+1)=%s, universe=%s, topk=%d",
        as_of_date, entry_date, config.instruments, config.topk,
    )

    # 1. Load the trained model.
    model_path = Path(config.model_path)
    if not model_path.exists():
        raise DailyRecommendationError(f"model artifact not found: {model_path}")
    import pickle
    with model_path.open("rb") as f:
        model = pickle.load(f)
    if not hasattr(model, "predict"):
        raise DailyRecommendationError(
            f"loaded object {type(model).__name__} has no .predict; not a qlib model."
        )

    # 2. Build as-of-T features ONCE (dataset reused for predict below).
    dataset, feature_frame = _build_asof_dataset(config, as_of_date)
    if feature_frame.empty:
        raise DailyRecommendationError(
            f"as-of-{as_of_date} feature frame is empty; cannot recommend. "
            "Check the universe has tradable names on this date."
        )
    # Look-ahead self-guard (cheap, always on): no row may be dated > T.
    assert_no_lookahead(feature_frame, as_of_date)

    # model.predict stays on the canonical qlib path (uses DK_I +
    # col_set="feature" internally) against the same dataset.
    scores = model.predict(dataset, segment="infer")
    scores = pd.Series(scores) if not isinstance(scores, pd.Series) else scores
    # Drop NaN SCORES (feature-derived; NOT label). This is the safe
    # dropna — it removes names whose features could not be computed, and
    # never touches the (unused) label. Counted + logged for transparency.
    n_raw = len(scores)
    scores = scores.dropna()
    n_nan_scores = n_raw - len(scores)
    if n_nan_scores:
        _logger.info("dropped %d NaN-score names (incomplete features)", n_nan_scores)
    if scores.empty:
        raise DailyRecommendationError(
            f"all scores NaN for as-of {as_of_date}; cannot recommend."
        )

    # Normalise score index to a flat instrument -> score map for T.
    instruments = [
        idx[-1] if isinstance(idx, tuple) else idx for idx in scores.index
    ]
    score_by_inst = dict(zip(
        instruments, [float(v) for v in scores.to_numpy()], strict=True,
    ))

    # 3. Tradability mask (suspension / one-price-lock) on T.
    pit = _build_pit_provider(config)
    try:
        mask_result = compute_unavailable_mask(
            list(score_by_inst.keys()), as_of_date, as_of_date, pit_provider=pit,
        )
    except MicrostructureMaskError as exc:
        raise DailyRecommendationError(
            f"tradability mask failed for {as_of_date}: {exc}"
        ) from exc
    # compute_unavailable_mask is the AUTHORITATIVE untradable set (the
    # reused canonical filter). It returns aggregate per-regime counts but
    # not per-name regime membership, so _per_regime_sets supplies the
    # precise reason label for the audit column.
    masked_pairs = {inst for (d, inst) in mask_result.masked if d == as_of_date}
    suspended, one_price = _per_regime_sets(pit, list(score_by_inst.keys()), as_of_date)

    # 4. Names (best-effort, current).
    name_map = _load_name_map(config.name_source_parquet)

    def _name(inst: str) -> str:
        return name_map.get(_qlib_code_to_ts_code(inst), "")

    # 5. Build full scored audit frame + Top-K tradable buy list (pure).
    picks, scored_frame, n_masked = build_recommendation(
        score_by_inst=score_by_inst,
        masked_pairs=masked_pairs,
        suspended=suspended,
        one_price=one_price,
        name_fn=_name,
        as_of_date=as_of_date,
        entry_date=entry_date,
        topk=config.topk,
    )
    _logger.info(
        "scored=%d, masked(untradable)=%d, buy-list=%d",
        len(scored_frame), n_masked, len(picks),
    )
    return DailyRecommendationResult(
        as_of_date=as_of_date, entry_date=entry_date, picks=picks,
        n_scored=len(scored_frame) - n_masked, n_masked=n_masked,
        scored_frame=scored_frame,
    )


def assert_no_lookahead(feature_frame: pd.DataFrame, as_of_date: str) -> pd.Timestamp:
    """Raise if any feature row is dated after ``as_of_date``.

    The single most important runtime guard: it makes a look-ahead leak
    fail loud rather than silently produce an inflated list. Returns the
    max datetime for convenience. Pure (no qlib) -> unit-testable.
    """
    if feature_frame.empty:
        raise DailyRecommendationError(
            f"feature frame for {as_of_date} is empty; cannot validate look-ahead."
        )
    max_dt = pd.Timestamp(feature_frame.index.get_level_values("datetime").max())
    if max_dt > pd.Timestamp(as_of_date):
        raise DailyRecommendationError(
            f"LOOK-AHEAD GUARD TRIPPED: feature frame contains datetime "
            f"{max_dt.date()} > as-of {as_of_date}. Refusing to emit a list."
        )
    return max_dt


def build_recommendation(
    *,
    score_by_inst: dict[str, float],
    masked_pairs: set[str],
    suspended: set[str],
    one_price: set[str],
    name_fn: Any,
    as_of_date: str,
    entry_date: str,
    topk: int,
) -> tuple[tuple[RecommendationPick, ...], pd.DataFrame, int]:
    """Pure ranking + tradability + Top-K assembly (no qlib, no IO).

    ``masked_pairs`` is the AUTHORITATIVE untradable set (from the reused
    ``compute_unavailable_mask``); ``suspended`` / ``one_price`` only
    supply the precise reason label. Returns ``(picks, scored_frame,
    n_masked)``. Sorting is stable so equal scores keep input order.
    """
    if topk < 0:
        raise DailyRecommendationError(
            f"topk must be >= 0, got {topk} (a negative head() slice would "
            "silently drop from the tail rather than truncate)."
        )
    rows = []
    for inst, score in score_by_inst.items():
        tradable = inst not in masked_pairs
        if tradable:
            reason = ""
        elif inst in suspended:
            reason = "suspended"
        elif inst in one_price:
            reason = "one_price_lock"
        else:
            reason = "unavailable"  # masked by canonical filter, regime unclassified
        rows.append({
            "stock_code": inst, "stock_name": name_fn(inst),
            "predicted_score": score, "tradable_flag": tradable,
            "unavailable_reason": reason,
        })
    scored_frame = pd.DataFrame(
        rows,
        columns=["stock_code", "stock_name", "predicted_score",
                 "tradable_flag", "unavailable_reason"],
    ).sort_values(
        "predicted_score", ascending=False, kind="stable",
    ).reset_index(drop=True)
    scored_frame.insert(0, "as_of_date", as_of_date)
    scored_frame.insert(1, "entry_date", entry_date)

    tradable = scored_frame[scored_frame["tradable_flag"]].head(topk)
    picks = tuple(
        RecommendationPick(
            rank=i + 1,
            stock_code=r.stock_code,
            stock_name=r.stock_name,
            predicted_score=float(r.predicted_score),
            tradable_flag=True,
            unavailable_reason="",
        )
        for i, r in enumerate(tradable.itertuples(index=False))
    )
    n_masked = int((~scored_frame["tradable_flag"]).sum())
    return picks, scored_frame, n_masked


def _build_pit_provider(config: RecommendationConfig) -> Any:
    from src.pit.query import PITDataProvider
    return PITDataProvider(
        provider_uri=config.provider_uri,
        delisted_registry_path=config.delisted_registry_path,
    )


def _per_regime_sets(
    pit: Any, instruments: list[str], as_of_date: str,
) -> tuple[set[str], set[str]]:
    """Return (suspended_set, one_price_set) for ``as_of_date``.

    Mirrors ``compute_unavailable_mask`` regime rules but keeps the two
    sets separate so the audit column can name the exact reason. Suspended
    is checked first (mutually exclusive with one-price-lock).
    """
    df = pit.get_features(
        ["$volume", "$high", "$low", "$close"], as_of_date, as_of_date,
        instruments=instruments,
    )
    suspended: set[str] = set()
    one_price: set[str] = set()
    if df is None or df.empty:
        return suspended, one_price
    # Normalize column names so we tolerate either `$volume` or a stripped
    # `volume` from the provider (lstrip is idempotent on bare names).
    df = df.rename(columns=lambda c: str(c).lstrip("$"))
    for idx, row in df.iterrows():
        inst = idx[0] if isinstance(idx, tuple) else idx
        vol, hi, lo, close = row["volume"], row["high"], row["low"], row["close"]
        # Suspension (matches compute_unavailable_mask intent): no close,
        # OR no/zero/negative volume. NaN volume = no trade = suspended
        # (a bare ``vol <= 0`` misses NaN since NaN comparisons are False).
        if pd.isna(close) or pd.isna(vol) or vol <= 0:
            suspended.add(str(inst))
        elif vol > 0 and hi == lo:
            one_price.add(str(inst))
    return suspended, one_price


_BUY_LIST_COLUMNS = [
    "as_of_date", "entry_date", "rank", "stock_code", "stock_name",
    "predicted_score", "tradable_flag", "unavailable_reason",
]


def write_outputs(result: DailyRecommendationResult, out_dir: str) -> dict[str, str]:
    """Write buy-list csv + json and the full scored audit csv. Returns
    the written paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = result.as_of_date
    buy_rows = [
        {
            "as_of_date": result.as_of_date,
            "entry_date": result.entry_date,
            "rank": p.rank,
            "stock_code": p.stock_code,
            "stock_name": p.stock_name,
            "predicted_score": p.predicted_score,
            "tradable_flag": p.tradable_flag,
            "unavailable_reason": p.unavailable_reason,
        }
        for p in result.picks
    ]
    csv_path = out / f"daily_recommendation_{stamp}.csv"
    json_path = out / f"daily_recommendation_{stamp}.json"
    audit_path = out / f"daily_recommendation_{stamp}_scored_full.csv"

    # Explicit columns so an empty buy list (e.g. --topk 0, or every
    # candidate masked) still writes a header row downstream readers expect.
    pd.DataFrame(buy_rows, columns=_BUY_LIST_COLUMNS).to_csv(
        csv_path, index=False, encoding="utf-8-sig",
    )
    json_path.write_text(json.dumps({
        "as_of_date": result.as_of_date,
        "entry_date": result.entry_date,
        "n_scored": result.n_scored,
        "n_masked": result.n_masked,
        "picks": buy_rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    result.scored_frame.to_csv(audit_path, index=False, encoding="utf-8-sig")
    return {"csv": str(csv_path), "json": str(json_path), "audit": str(audit_path)}
