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
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from src.core.logger import get_logger
from src.core.microstructure_mask import (
    MicrostructureMaskError,
    compute_unavailable_mask,
)
from src.core.qlib_runtime import (
    QlibRuntimeConfig,
    _normalize_provider_uri,
    init_qlib_canonical,
)
from src.data.active_stocks_snapshot import SnapshotDateError, embedded_snapshot_date
from src.data.pit._common import qlib_to_ts_code
from src.data.pit.bundle_integrity import (
    INTEGRITY_FILENAME,
    BundleIntegrityError,
    read_bundle_integrity,
)
from src.data.st_status import current_st_codes

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
    # Current-name source (tushare stock_basic dump). Supplies display names
    # AND the current-ST set used to exclude ST/*ST from the buy list, so it
    # is REQUIRED: recommend() fails loud if it is missing or stale rather
    # than emitting a list that could silently include ST names. Overridable
    # via the QUANT_NAME_SOURCE env var (default = the value below, so behaviour
    # is unchanged when it is unset); read per-instance via default_factory.
    name_source_parquet: str | None = field(
        default_factory=lambda: os.environ.get(
            "QUANT_NAME_SOURCE",
            "D:/qlib_data/tushare_raw/active_stocks.parquet",
        )
    )
    # ST snapshot staleness tolerance: the name source's file mtime may lag
    # the as-of date by at most this many calendar days. A stale snapshot can
    # miss a recent ST designation and leak it into the list -> fail loud.
    st_snapshot_max_age_days: int = 7
    # Bundle (price/feature data) staleness tolerance: the qlib bundle's last
    # trading day may lag the EXTERNAL today by at most this many CALENDAR days.
    # Default 14 covers the longest A-share holiday (Spring Festival ~9-10 days,
    # during which no new data is normal) plus a buffer, while still catching a
    # genuinely stale bundle (weeks/months behind) so recommend() refuses rather
    # than silently scoring on stale prices. See _assert_bundle_fresh.
    bundle_max_age_days: int = 14
    out_dir: str = "output/daily_recommend"
    # P3-4c Layer 2: refuse to recommend from a bundle stamped
    # built-from-holey-fetch (or lacking a fetch-integrity stamp) unless the
    # operator explicitly opts in HERE. This is SEPARATE from the build override
    # (--allow-holey-fetch): building a partial research bundle does not sanction
    # trading on it. See _assert_bundle_fetch_complete.
    allow_holey_recommend: bool = False


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
    n_masked: int         # dropped by the microstructure tradability mask
    n_st_excluded: int    # dropped because currently ST/*ST
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


def _st_snapshot_is_stale(
    snapshot_date: date, as_of_date: str, max_age_days: int,
) -> bool:
    """True if the ST snapshot lags the as-of date by more than the tolerance.

    Pure (date arithmetic only) -> unit-testable. Only a snapshot OLDER than
    the as-of date can be stale: a newer snapshot (e.g. a current snapshot used
    for a historical inference date) is the inference path's correct "current
    ST" — point-in-time ST history is PR2's concern, not this guard's.
    """
    asof = date.fromisoformat(as_of_date)
    return (asof - snapshot_date).days > max_age_days


def _bundle_is_stale(bundle_last_day: date, today: date, max_age_days: int) -> bool:
    """True if the bundle's last trading day lags ``today`` by more than the
    tolerance (in CALENDAR days). Pure -> unit-testable. A bundle whose last day
    is on/after ``today`` is never stale.
    """
    return (today - bundle_last_day).days > max_age_days


def _assert_bundle_fresh(
    bundle_last_day: date, today: date, max_age_days: int,
) -> None:
    """Fail-loud guard: refuse if the price/feature bundle is stale.

    ``resolve_dates`` picks the as-of date from the BUNDLE's own calendar, so a
    stale bundle would silently treat a weeks/months-old day as "today" and emit
    a list scored on stale prices (the daily-automation worst case). This
    compares the bundle's last trading day against an EXTERNAL ``today`` (the
    system date in production, injectable for tests) and raises rather than
    emitting. The tolerance is generous enough (default 14 calendar days) that a
    normal pre-holiday gap — Spring Festival is ~9-10 days with no new data —
    does NOT trip it, while a genuinely stale bundle (weeks/months behind) does.
    Pure -> unit-testable.
    """
    if _bundle_is_stale(bundle_last_day, today, max_age_days):
        lag = (today - bundle_last_day).days
        raise DailyRecommendationError(
            f"Price/feature bundle is STALE: last trading day {bundle_last_day} "
            f"lags today {today} by {lag} calendar day(s) (> {max_age_days}). "
            "The qlib bundle has not been updated, so a list now would be scored "
            "on stale prices. Refusing to emit. Update the bundle (re-fetch "
            "tushare + rebuild the qlib bins) before recommending, or raise "
            "bundle_max_age_days for an intentional historical run."
        )


def _assert_st_snapshot_consistent_with_bundle(
    snapshot_date: date, bundle_last_day: date, max_age_days: int,
) -> None:
    """Fail-loud guard (P3-5): the ST snapshot and the price bundle must come
    from the same update cycle.

    The embedded ``snapshot_date`` says when active_stocks was fetched; the
    bundle's calendar tail says how far its prices run. A snapshot that lags the
    bundle tail by more than ``bundle_max_age_days`` means the two artifacts
    were NOT refreshed together (e.g. the bundle was rebuilt but stock_basic
    was not re-fetched) — its ST/name view predates the prices being ranked, so
    a recent ST designation could leak in. A snapshot NEWER than the bundle
    tail is fine (snapshots refresh more often than bundles). Pure ->
    unit-testable.
    """
    lag = (bundle_last_day - snapshot_date).days
    if lag > max_age_days:
        raise DailyRecommendationError(
            f"ST snapshot is INCONSISTENT with the price bundle: embedded "
            f"snapshot_date {snapshot_date} lags the bundle's last trading day "
            f"{bundle_last_day} by {lag} calendar day(s) (> {max_age_days}). "
            "The snapshot and the bundle were not refreshed together, so the "
            "ST/name view predates the prices being ranked. Re-fetch tushare "
            "stock_basic (refreshing active_stocks.parquet) before "
            "recommending, or raise bundle_max_age_days for an intentional run."
        )


def _assert_bundle_fetch_complete(
    provider_uri: str, *, allow_holey_recommend: bool,
) -> None:
    """Fail-loud guard (P3-4c Layer 2): refuse to recommend from a bundle built
    from a HOLEY tushare fetch, or one lacking a fetch-integrity stamp.

    The qlib bin builder stamps each bundle (``_fetch_integrity.json``) with whether
    it was built from a complete fetch. A holey bundle ranks on
    survivorship-incomplete data; a MISSING stamp means completeness cannot be
    confirmed (e.g. a pre-P3-4c bundle). Either way refuse rather than emit a list
    — unless the operator opts in HERE. This is INDEPENDENT of the build-side
    ``--allow-holey-fetch``: the stamp carries the FACT (was the fetch holey?), not
    the authorization to trade on it, so each boundary must opt in on its own. A
    CORRUPT / unknown-schema stamp fails loud REGARDLESS of the override — the
    override accepts a holey or MISSING stamp (known states), not an unreadable one.
    """
    # Read FIRST, from the SAME normalized path qlib initialized against
    # (expanduser / abspath / realpath / normcase, not the raw string — otherwise a
    # `~/...` or whitespaced URI reads a non-existent literal path and a clean
    # bundle looks unstamped, codex P2). A corrupt / unknown-schema stamp raises
    # BundleIntegrityError, which we surface as fail-loud BEFORE honouring the
    # override — `--allow-holey-recommend` accepts incompleteness, not corruption
    # (codex P2).
    try:
        integrity = read_bundle_integrity(Path(_normalize_provider_uri(provider_uri)))
    except BundleIntegrityError as exc:
        raise DailyRecommendationError(
            f"Bundle {provider_uri} has an UNREADABLE fetch-integrity stamp: {exc} "
            "Refusing to recommend on corrupt provenance — a holey or missing stamp "
            "can be overridden with --allow-holey-recommend, a corrupt one cannot."
        ) from exc
    if allow_holey_recommend:
        return
    if integrity is None:
        raise DailyRecommendationError(
            f"Bundle {provider_uri} has no fetch-integrity stamp "
            f"({INTEGRITY_FILENAME}); cannot confirm it was built from a complete "
            "tushare fetch. Refusing to recommend on possibly-incomplete data. "
            "Rebuild the bundle (scripts/data_pipeline/05_build_qlib_bins) to stamp "
            "it, or pass --allow-holey-recommend for an intentional run on an "
            "unstamped bundle."
        )
    if integrity.built_from_holey_fetch:
        raise DailyRecommendationError(
            f"Bundle {provider_uri} was BUILT FROM A HOLEY tushare fetch "
            f"({len(integrity.holes)} recorded hole(s)): its data is "
            "survivorship-incomplete, so a list now would rank on partial data. "
            "Refusing to recommend. Re-fetch to fill the holes and rebuild, or pass "
            "--allow-holey-recommend for an intentional research run. This is a "
            "SEPARATE decision from the build-side --allow-holey-fetch that produced "
            "the bundle — building partial data does not sanction trading on it."
        )


def _validate_st_snapshot(config: RecommendationConfig, as_of_date: str) -> date:
    """Fail-loud guard: the current-ST source must exist, be fresh, and carry
    the required schema.

    ST filtering needs the active-stocks snapshot; a missing, stale, or
    malformed snapshot would silently leak ST names into the list, so we refuse
    to emit one.

    Staleness reads the EMBEDDED ``snapshot_date`` column the fetcher stamps at
    fetch time (P3-5). The previous source — file mtime — was a WEAK proxy: a
    sync / copy tool that rewrites mtime to "now" makes a stale file look fresh
    and lets the guard pass silently, the opposite of fail-loud. The embedded
    date survives copies and pandas round-trips. A pre-P3-5 file (no column)
    fails loud with a re-fetch instruction rather than silently falling back to
    mtime. Returns the validated snapshot date (recommend() then checks it for
    consistency against the bundle calendar tail).
    """
    raw = config.name_source_parquet
    if not raw:
        raise DailyRecommendationError(
            "ST filtering requires name_source_parquet (the active-stocks "
            "snapshot); got None. Refusing to emit a list that could silently "
            "include ST names."
        )
    path = Path(raw)
    if not path.exists():
        raise DailyRecommendationError(
            f"ST filtering requires the active-stocks snapshot at {path}; file "
            "not found. Refusing to emit a possibly-unfiltered list."
        )
    # Schema: the required ST filter reads ts_code + name. A present, fresh, but
    # malformed snapshot (columns dropped by an upstream schema change, or an
    # empty file) would make _load_name_map fall back to {} and silently
    # DISABLE ST filtering — exactly the leak this guard exists to prevent.
    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        raise DailyRecommendationError(
            f"ST snapshot {path} could not be read as parquet "
            f"({type(exc).__name__}: {exc}). Refusing to emit a "
            "possibly-unfiltered list."
        ) from exc
    missing = [c for c in ("ts_code", "name") if c not in df.columns]
    if missing:
        raise DailyRecommendationError(
            f"ST snapshot {path.name} is missing required column(s) {missing} "
            f"(has {list(df.columns)}); cannot build the current-ST set. "
            "Refusing to emit a possibly-unfiltered list."
        )
    if df.empty:
        raise DailyRecommendationError(
            f"ST snapshot {path.name} has zero rows; cannot build the "
            "current-ST set. Refusing to emit a possibly-unfiltered list."
        )
    try:
        snapshot_date = embedded_snapshot_date(df, source=f"ST snapshot {path.name}")
    except SnapshotDateError as exc:
        raise DailyRecommendationError(
            f"{exc} Refusing to emit a list whose ST-snapshot age cannot be "
            "established."
        ) from exc
    if _st_snapshot_is_stale(
        snapshot_date, as_of_date, config.st_snapshot_max_age_days,
    ):
        raise DailyRecommendationError(
            f"ST snapshot {path.name} is stale: embedded snapshot_date "
            f"{snapshot_date} lags as-of {as_of_date} by more than "
            f"{config.st_snapshot_max_age_days} day(s). A stale snapshot can "
            "miss a recent ST designation and leak it into the list. Refresh "
            "active_stocks.parquet (re-fetch tushare stock_basic) or raise "
            "st_snapshot_max_age_days."
        )
    return snapshot_date


def _load_model(model_path: Path) -> Any:
    """Load the pickled qlib model, failing closed with a domain error.

    A missing path, a corrupt / truncated pickle, or an unpickle that
    needs an unavailable class all raise ``DailyRecommendationError``
    rather than letting a raw ``UnpicklingError`` / ``EOFError`` /
    ``ModuleNotFoundError`` escape. The loaded object must expose
    ``.predict`` (qlib model contract).
    """
    if not model_path.exists():
        raise DailyRecommendationError(f"model artifact not found: {model_path}")
    import pickle
    try:
        with model_path.open("rb") as f:
            model = pickle.load(f)
    except (pickle.UnpicklingError, EOFError, OSError, ValueError,
            AttributeError, ImportError) as exc:
        raise DailyRecommendationError(
            f"failed to load model artifact {model_path}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not hasattr(model, "predict"):
        raise DailyRecommendationError(
            f"loaded object {type(model).__name__} has no .predict; "
            "not a qlib model."
        )
    return model


# --------------------------------------------------------------------------
# Main entry
# --------------------------------------------------------------------------
def recommend(
    config: RecommendationConfig, *, now: date | None = None,
) -> DailyRecommendationResult:
    init_qlib_canonical(QlibRuntimeConfig(
        provider_uri=config.provider_uri,
        region=config.region,
        data_adjust_mode=config.adjust_mode,
    ))

    as_of_date, entry_date = resolve_dates(config.as_of_date)

    # Phase 2: price/feature-data freshness guard. resolve_dates picks the
    # as-of date from the BUNDLE's own calendar, so a stale bundle would
    # silently score on weeks/months-old prices and emit a stale list with no
    # error. Compare the bundle's last trading day against an EXTERNAL today
    # (system date; injectable via ``now`` for tests/determinism) and refuse if
    # it lags by more than bundle_max_age_days.
    from qlib.data import D
    bundle_last_day = pd.Timestamp(list(D.calendar())[-1]).date()
    _assert_bundle_fresh(
        bundle_last_day,
        now if now is not None else date.today(),
        config.bundle_max_age_days,
    )
    # P3-4c Layer 2: refuse a bundle built from a holey tushare fetch (or one
    # lacking a fetch-integrity stamp) unless explicitly allowed here — a decision
    # SEPARATE from the build-side --allow-holey-fetch.
    _assert_bundle_fetch_complete(
        config.provider_uri, allow_holey_recommend=config.allow_holey_recommend,
    )

    _logger.info(
        "Daily recommendation: as_of(T)=%s, entry(T+1)=%s, universe=%s, topk=%d",
        as_of_date, entry_date, config.instruments, config.topk,
    )

    # 1. Load the trained model.
    model = _load_model(Path(config.model_path))

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

    # Normalise score index to a flat instrument -> score map for T. Fail-loud
    # on a multi-date index, a date other than T, or duplicate instruments
    # rather than letting dict(zip) silently drop a key (see
    # _scores_to_inst_map). Pinning the stamp == as_of_date (PR-C) makes the
    # live timing contract explicit: this is a day-T signal for entry on the
    # next trading session — the SAME semantics as the canonical backtest's
    # lag=1 (signal stamped T, filled T+1 via qlib's built-in shift), so
    # backtested and live behavior coincide by construction.
    score_by_inst = _scores_to_inst_map(scores, expected_date=as_of_date)

    # 3. Tradability mask (suspension / one-price-lock) on the ENTRY day —
    # the day the recommendation would actually fill (codex P1 round 4 on
    # PR #241, matching the backtest's execution-day masking). This is NOT
    # look-ahead: ``resolve_dates`` requires the entry session to exist in
    # the bundle calendar (the default as-of is the second-to-last day), so
    # the entry day's bars are already on disk at decision time. A name
    # tradable on T but suspended/one-price-locked on T+1 must not be
    # emitted — the lag=1 backtest drops the same T-stamped signal by
    # execution day.
    pit = _build_pit_provider(config)
    try:
        mask_result = compute_unavailable_mask(
            list(score_by_inst.keys()), entry_date, entry_date, pit_provider=pit,
        )
    except MicrostructureMaskError as exc:
        raise DailyRecommendationError(
            f"tradability mask failed for entry day {entry_date}: {exc}"
        ) from exc
    # compute_unavailable_mask is the AUTHORITATIVE untradable set (the
    # reused canonical filter). It returns aggregate per-regime counts but
    # not per-name regime membership, so _per_regime_sets supplies the
    # precise reason label for the audit column.
    masked_pairs = {inst for (d, inst) in mask_result.masked if d == entry_date}
    suspended, one_price = _per_regime_sets(pit, list(score_by_inst.keys()), entry_date)

    # 4. Names + current-ST exclusion set. The name source is REQUIRED here
    # (fail-loud if missing/stale): it supplies both display names AND the
    # ST/*ST set we must exclude. _validate_st_snapshot refuses to emit a
    # list that could silently include ST names. Its embedded snapshot_date is
    # then checked against the bundle calendar tail (P3-5): the two artifacts
    # must come from the same update cycle.
    st_snapshot_date = _validate_st_snapshot(config, as_of_date)
    _assert_st_snapshot_consistent_with_bundle(
        st_snapshot_date, bundle_last_day, config.bundle_max_age_days,
    )
    name_map = _load_name_map(config.name_source_parquet)

    def _name(inst: str) -> str:
        return name_map.get(qlib_to_ts_code(inst), "")

    # Current-ST set keyed by qlib instrument (matches score_by_inst), built
    # from the already-loaded snapshot — no second load path.
    st_excluded = current_st_codes({inst: _name(inst) for inst in score_by_inst})

    # 5. Build full scored audit frame + Top-K tradable buy list (pure). ST
    # names are dropped from the candidate pool BEFORE the Top-K slice, so the
    # buy list holds K tradable, non-ST names (not K minus the ST hits).
    picks, scored_frame, n_excluded = build_recommendation(
        score_by_inst=score_by_inst,
        masked_pairs=masked_pairs,
        suspended=suspended,
        one_price=one_price,
        st_excluded=st_excluded,
        name_fn=_name,
        as_of_date=as_of_date,
        entry_date=entry_date,
        topk=config.topk,
    )
    n_st = int((scored_frame["unavailable_reason"] == "st").sum())
    n_masked = n_excluded - n_st
    _logger.info(
        "scored=%d, masked(untradable)=%d, st-excluded=%d, buy-list=%d",
        len(scored_frame), n_masked, n_st, len(picks),
    )
    return DailyRecommendationResult(
        as_of_date=as_of_date, entry_date=entry_date, picks=picks,
        n_scored=len(scored_frame) - n_excluded, n_masked=n_masked,
        n_st_excluded=n_st, scored_frame=scored_frame,
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


def _scores_to_inst_map(
    scores: pd.Series, *, expected_date: str | None = None,
) -> dict[str, float]:
    """Collapse a single-as-of-date prediction Series to ``{instrument: score}``.

    ``scores`` is the qlib ``model.predict`` output for the ``[T, T]`` infer
    segment — a Series indexed by ``(datetime, instrument)`` (MultiIndex) or,
    defensively, a flat instrument index. The recommendation list is built on
    the returned map, so a silent key collapse (``dict`` keeps last-write-wins
    on a duplicate instrument) would emit a truncated / mis-scored buy list
    with no error.

    Two fail-loud guards, neither covered by the previous
    ``dict(zip(..., strict=True))`` — ``strict=True`` only checks length parity,
    which can never differ here since both operands derive from the same Series,
    so it guarded nothing that could actually go wrong:

    * **single date** — for a ``(datetime, instrument)`` MultiIndex, exactly
      one distinct datetime (a flat index has no datetime level and skips this
      check). Catches an infer segment widened beyond ``[T, T]``, which would
      alias the same instrument across days and silently keep only the last.
    * **unique instruments** — no instrument appears twice. Catches a malformed
      universe file or a panel-integrity anomaly.

    Pure (no qlib / IO) -> unit-testable. An empty Series returns ``{}``.
    """
    idx = scores.index
    # The single-date guard only applies to a (datetime, instrument)
    # MultiIndex. A flat instrument index has no datetime level, so it skips
    # the date check — reading get_level_values(0) on a flat index returns the
    # instrument labels, which would false-fire "multi-date" on any 2+-name
    # cross-section — and goes straight to the duplicate check.
    if isinstance(idx, pd.MultiIndex):
        datetimes = set(idx.get_level_values(0))
        if len(datetimes) > 1:
            raise DailyRecommendationError(
                f"prediction spans {len(datetimes)} distinct dates "
                f"{sorted(str(d) for d in datetimes)}; expected a single as-of "
                "date. The instrument->score map would alias the same "
                "instrument across days. Refusing to emit a list."
            )
        # PR-C timing pin: the single stamp must BE the as-of date. The
        # earlier guards only enforced "single date" and "<= T" (no
        # look-ahead); a stale `< T` stamp — e.g. an infer segment that
        # silently resolved to an older session — would have passed and
        # emitted yesterday's list labelled as today's.
        if expected_date is not None and datetimes:
            actual = next(iter(datetimes))
            actual_iso = (
                actual.date().isoformat() if hasattr(actual, "date")
                else str(actual)[:10]
            )
            if actual_iso != expected_date:
                raise DailyRecommendationError(
                    f"prediction is stamped {actual_iso} but the requested "
                    f"as-of date is {expected_date}. A stale stamp would emit "
                    "an older session's list labelled as today's. Refusing."
                )
        instruments = list(idx.get_level_values(-1))
    else:
        instruments = list(idx)
    dupes = sorted(str(k) for k, c in Counter(instruments).items() if c > 1)
    if dupes:
        shown = ", ".join(dupes[:10]) + (" ..." if len(dupes) > 10 else "")
        raise DailyRecommendationError(
            f"duplicate instruments in single-date prediction: {shown} "
            f"({len(dupes)} distinct code(s) repeated). Universe file or "
            "segment-width anomaly; refusing to emit a list (dict(zip) would "
            "silently drop the duplicates)."
        )
    values = [float(v) for v in scores.to_numpy()]
    return dict(zip(instruments, values, strict=True))


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
    st_excluded: frozenset[str] | set[str] = frozenset(),
) -> tuple[tuple[RecommendationPick, ...], pd.DataFrame, int]:
    """Pure ranking + tradability + Top-K assembly (no qlib, no IO).

    ``masked_pairs`` is the AUTHORITATIVE untradable set (from the reused
    ``compute_unavailable_mask``); ``suspended`` / ``one_price`` only supply
    the precise reason label. ``st_excluded`` is the current-ST set: those
    names are dropped from the candidate pool BEFORE the Top-K slice (so the
    list keeps K tradable, non-ST picks) and carry reason ``"st"`` in the audit
    frame. Microstructure masking takes precedence over the ST label when a
    name is both. Returns ``(picks, scored_frame, n_excluded)`` where
    ``n_excluded`` counts every not-tradable row (masked OR ST). Sorting is
    stable so equal scores keep input order.
    """
    if topk < 0:
        raise DailyRecommendationError(
            f"topk must be >= 0, got {topk} (a negative head() slice would "
            "silently drop from the tail rather than truncate)."
        )
    rows = []
    for inst, score in score_by_inst.items():
        if inst in masked_pairs:
            tradable = False
            if inst in suspended:
                reason = "suspended"
            elif inst in one_price:
                reason = "one_price_lock"
            else:
                reason = "unavailable"  # masked by canonical filter, regime unclassified
        elif inst in st_excluded:
            tradable = False
            reason = "st"
        else:
            tradable = True
            reason = ""
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
    n_excluded = int((~scored_frame["tradable_flag"]).sum())
    return picks, scored_frame, n_excluded


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
        "n_st_excluded": result.n_st_excluded,
        "picks": buy_rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    result.scored_frame.to_csv(audit_path, index=False, encoding="utf-8-sig")
    return {"csv": str(csv_path), "json": str(json_path), "audit": str(audit_path)}
