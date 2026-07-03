"""A-share microstructure mask for canonical backtest.

Drops per-day candidates from a predictions ``pd.Series`` BEFORE
qlib's ``TopkDropoutStrategy`` rebalances. Two regimes are masked:

* **Suspension (停牌)**: the stock did not trade that day —
  ``$volume <= 0`` or ``$close`` is NaN (the zero-volume test is
  coded as ``volume < 1`` to stay float-safe — see the
  ``suspended_mask`` comment). qlib's default Exchange would
  otherwise fill at the carried-forward close, producing a phantom
  trade.
* **One-price lock (一字板)**: the entire day's trading happened
  at one price — ``$volume > 0`` AND ``$high == $low``. On A-share
  this almost always means a limit-up or limit-down queue
  cleared every order at the limit price; a real buyer (on
  upper-limit days) or seller (on lower-limit days) cannot
  actually fill, so qlib's optimistic fill is fantasy.

Boundaries
----------
* Imports qlib lazily — module load does NOT require qlib.
* When a ``PITDataProvider`` is supplied, OHLCV fetch routes
  through it (post-delist mask applied). Otherwise fetches via
  direct ``qlib.data.D.features`` — that call site is on the
  PIT-bypass allowlist (audit P0-6 / pit-bypass-ok).

Audit P0-3 / openspec/changes/add-microstructure-mask.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from src.core.logger import get_logger

_logger = get_logger(__name__)


def ts_to_iso_date(ts: Any) -> str:
    """A qlib datetime-level value as an ISO ``YYYY-MM-DD`` string.

    The PARITY CONTRACT used everywhere a ``(date, instrument)`` key must match
    ``MicrostructureMaskResult.masked``: a ``pd.Timestamp`` / ``datetime`` yields
    ``.date().isoformat()``; anything else (a ``date``, a ``numpy.datetime64``)
    falls back to its ``str(...)[:10]`` prefix. Defined ONCE so a tweak can never
    drift between the mask builder, the predictions filter, the T+1 execution
    remap, and the recommend freshness check — a silent mismatch there would
    break frozenset membership. NOTE: returns a STRING; the separate
    list-of-``date`` calendar idiom (``ts.date() if … else
    date.fromisoformat(…)``) is intentionally NOT this helper.
    """
    return ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]


def _iso_dates_for(level: Any) -> Any:
    """Vectorized ``ts_to_iso_date`` over an index level, element-bit-identical.

    Fast path (the qlib norm — a homogeneous ``datetime64[ns]`` /
    ``datetime64[ns, tz]`` level): ``pd.factorize`` maps the level to codes
    over its (few) UNIQUE values — ~250 distinct dates in a year-long panel —
    then the SAME ``ts_to_iso_date`` runs once per unique value and a C-speed
    gather expands back. Within one datetime64 dtype, equality implies the
    same instant in the SAME timezone, so equal values share a local date and
    grouping is safe.

    Fallback (``object`` / anything else): row-wise, verbatim the old loop.
    ``factorize`` groups by raw EQUALITY, and a mixed-tz object level can hold
    Timestamps that compare equal (same instant) yet have DIFFERENT local
    dates (UTC 16:00 == Asia/Shanghai next-day 00:00) — grouping would let
    later rows inherit the first value's ISO date (codex P2 on #319)."""
    import numpy as np
    import pandas as pd

    if str(getattr(level, "dtype", "")).startswith("datetime64"):
        codes, uniques = pd.factorize(level, use_na_sentinel=False)
        iso_uniques = np.array(
            [ts_to_iso_date(ts) for ts in uniques], dtype=object,
        )
        return iso_uniques[codes]
    return np.array([ts_to_iso_date(ts) for ts in level], dtype=object)


def _str_insts_for(level: Any) -> Any:
    """Vectorized ``str(...)`` over an instrument level — factorize + gather
    when every element is a plain string (equal strings stringify equal);
    row-wise fallback otherwise (an object level mixing e.g. ``1`` and ``1.0``
    holds values that compare equal but stringify differently — same
    equality-vs-projection trap as :func:`_iso_dates_for`)."""
    import numpy as np
    import pandas as pd

    if pd.api.types.infer_dtype(level, skipna=False) == "string":
        codes, uniques = pd.factorize(level, use_na_sentinel=False)
        str_uniques = np.array([str(x) for x in uniques], dtype=object)
        return str_uniques[codes]
    return np.array([str(x) for x in level], dtype=object)


def _pairs_at_positions(date_level: Any, inst_level: Any, hit: Any) -> list[tuple[str, str]]:
    """``[(iso_date, str_inst)]`` for the True positions of ``hit`` —
    the vectorized equivalent of appending inside the old row loop."""
    if not hit.any():
        return []
    dates_hit = _iso_dates_for(date_level[hit])
    insts_hit = _str_insts_for(inst_level[hit])
    return list(zip(dates_hit, insts_hit, strict=True))


class MicrostructureMaskError(RuntimeError):
    """Raised when the mask cannot be computed (bad qlib fetch,
    malformed OHLCV, etc.). Callers in the canonical path catch
    this and re-raise as ``BacktestRunnerError``."""


@dataclass(frozen=True)
class MicrostructureMaskResult:
    """The set of ``(date_iso, instrument)`` pairs unavailable
    for fill, plus per-regime counts for operator-facing WARN
    logs.

    ``masked`` is a ``frozenset`` so ``apply_mask_to_predictions``
    can do O(1) membership checks per row of the predictions
    Series. The date is an ISO ``YYYY-MM-DD`` string (not a
    ``pd.Timestamp``) to keep the result type plain-Python and
    serialisable without pandas dependencies.
    """

    masked: frozenset[tuple[str, str]]
    n_suspended: int
    n_one_price_days: int

    def __post_init__(self) -> None:
        if self.n_suspended < 0 or self.n_one_price_days < 0:
            raise MicrostructureMaskError(
                "MicrostructureMaskResult counts must be non-negative; "
                f"got n_suspended={self.n_suspended}, "
                f"n_one_price_days={self.n_one_price_days}."
            )

    @property
    def total_masked(self) -> int:
        return len(self.masked)


def compute_unavailable_mask(
    instruments: Iterable[str],
    start_date: str,
    end_date: str,
    *,
    pit_provider: Any | None = None,
) -> MicrostructureMaskResult:
    """Compute the per-day microstructure mask for ``instruments``
    over ``[start_date, end_date]`` (both ISO ``YYYY-MM-DD``,
    inclusive).

    Routing:

    * ``pit_provider`` supplied → OHLCV fetch routes through
      ``PITDataProvider.get_features`` (audit P0-6: post-delist
      mask applied; cache shared).
    * ``pit_provider`` omitted → falls through to direct
      ``qlib.data.D.features``. **pit-bypass-ok**: this is the
      audit-P0-6 allow-listed fallback; the governance test in
      ``tests/governance/test_pit_provider_is_sole_qlib_features_caller.py``
      whitelists this file.

    Rules:

    * Suspended: ``$volume <= 0`` OR ``$close`` is NaN (the zero-volume
      test is coded as ``volume < 1``, float-safe).
    * One-price lock: NOT suspended AND ``$high == $low``.

    The two are mutually exclusive by construction (suspension is
    checked first; one-price lock requires ``$volume > 0`` which
    implies not-suspended).
    """
    instrument_list = sorted(set(instruments))
    if not instrument_list:
        return MicrostructureMaskResult(
            masked=frozenset(), n_suspended=0, n_one_price_days=0,
        )

    # Validate date bounds early.
    try:
        s = date.fromisoformat(start_date)
        e = date.fromisoformat(end_date)
    except ValueError as exc:
        raise MicrostructureMaskError(
            "compute_unavailable_mask: start_date / end_date must be "
            f"ISO YYYY-MM-DD; got start={start_date!r}, end={end_date!r}: {exc}"
        ) from exc
    if e < s:
        raise MicrostructureMaskError(
            "compute_unavailable_mask: end_date "
            f"({end_date}) precedes start_date ({start_date})."
        )

    fields = ["$volume", "$high", "$low", "$close"]
    try:
        if pit_provider is not None:
            df = pit_provider.get_features(
                fields, start_date, end_date,
                instruments=instrument_list,
            )
        else:
            from qlib.data import D
            df = D.features(
                instrument_list, fields,
                start_time=start_date, end_time=end_date,
            )
    except Exception as exc:
        raise MicrostructureMaskError(
            "compute_unavailable_mask: OHLCV fetch failed "
            f"({type(exc).__name__}: {exc}). Verify canonical qlib "
            "init and that the provider covers "
            f"[{start_date}, {end_date}] for {len(instrument_list)} "
            "instruments."
        ) from exc

    if df is None or df.empty:
        # No OHLCV data — nothing to mask. Distinct from an error
        # because qlib legitimately returns an empty frame when
        # the universe is empty or the window has no trading days.
        return MicrostructureMaskResult(
            masked=frozenset(), n_suspended=0, n_one_price_days=0,
        )

    # Normalise column names. qlib commonly returns the literal
    # ``$volume`` / ``$high`` / ``$low`` / ``$close`` strings; some
    # provider configurations strip the ``$`` prefix. Accept either.
    rename: dict[str, str] = {}
    for raw, clean in (
        ("$volume", "volume"), ("$high", "high"),
        ("$low", "low"), ("$close", "close"),
    ):
        if raw in df.columns:
            rename[raw] = clean
    df = df.rename(columns=rename) if rename else df

    required = ("volume", "high", "low", "close")
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise MicrostructureMaskError(
            "compute_unavailable_mask: OHLCV fetch returned a frame "
            f"missing required columns {missing}; got "
            f"{list(df.columns)}. Verify qlib bundle has $volume, "
            "$high, $low, $close."
        )

    # qlib's MultiIndex is typically (instrument, datetime). The
    # exact order depends on the caller; access by name for
    # robustness.
    if not hasattr(df.index, "names") or set(df.index.names) != {
        "instrument", "datetime",
    }:
        raise MicrostructureMaskError(
            "compute_unavailable_mask: OHLCV frame must have a "
            f"(instrument, datetime) MultiIndex; got names="
            f"{getattr(df.index, 'names', None)!r}."
        )

    inst_level = df.index.get_level_values("instrument")
    date_level = df.index.get_level_values("datetime")
    volume = df["volume"]
    close = df["close"]
    high = df["high"]
    low = df["low"]

    # Suspended: volume <= 0 OR close is NaN. We use ``< 1`` rather
    # than ``<= 0`` so a Tushare-published bundle that codes
    # suspensions as ``volume == 0`` still gets caught regardless
    # of float-vs-int dtype quirks. NaN volume is also treated as
    # suspended (qlib often writes NaN for non-trading bins).
    suspended_mask = (volume.isna()) | (volume < 1) | (close.isna())

    # One-price lock: not suspended AND high == low. Both must be
    # non-NaN (the suspended branch already caught NaN-close, but
    # high/low can be NaN independently in some bundles).
    one_price_mask = (
        (~suspended_mask)
        & (high.notna())
        & (low.notna())
        & (high == low)
    )

    # Vectorized (audit P1): the old per-row Python loop cost O(N) iterations
    # (5000 insts x 250 days ~ 1.25M; x23 walk-forward folds). ``masked`` is a
    # frozenset, so pair ORDER is immaterial — only the set contents and the
    # two counts must be exactly what the loop produced. Element semantics stay
    # BIT-IDENTICAL by calling the SAME ``ts_to_iso_date`` / ``str`` on the
    # (few) UNIQUE level values via factorize + C-speed gather, instead of once
    # per row — tz/NaT/object-dtype behavior is inherited verbatim.
    sus_values = suspended_mask.to_numpy(copy=False)
    one_values = one_price_mask.to_numpy(copy=False)
    n_suspended = int(sus_values.sum())
    n_one_price = int(one_values.sum())

    hit = sus_values | one_values  # one_price already excludes suspended rows
    masked_pairs = _pairs_at_positions(date_level, inst_level, hit)

    return MicrostructureMaskResult(
        masked=frozenset(masked_pairs),
        n_suspended=n_suspended,
        n_one_price_days=n_one_price,
    )


def apply_mask_to_predictions(
    predictions: Any,
    mask: frozenset[tuple[str, str]] | MicrostructureMaskResult,
) -> tuple[Any, int]:
    """Drop every ``(date, instrument)`` row in ``mask`` from
    ``predictions``. Returns ``(filtered_predictions, n_dropped)``.

    ``predictions`` must be a ``pd.Series`` with a
    ``(datetime, instrument)`` MultiIndex — same shape qlib's
    ``TopkDropoutStrategy`` consumes. The date level is converted
    to ISO ``YYYY-MM-DD`` for membership-check parity with
    ``MicrostructureMaskResult.masked``.

    Empty mask → returns ``predictions`` unchanged AND
    ``n_dropped=0`` (same object — no defensive copy on the no-op
    fast path).
    """
    pair_set: frozenset[tuple[str, str]]
    if isinstance(mask, MicrostructureMaskResult):
        pair_set = mask.masked
    else:
        pair_set = mask

    if not pair_set:
        return predictions, 0

    import pandas as pd

    if not isinstance(predictions, pd.Series):
        raise MicrostructureMaskError(
            "apply_mask_to_predictions: predictions must be a "
            f"pd.Series; got {type(predictions).__name__}."
        )
    if not isinstance(predictions.index, pd.MultiIndex):
        raise MicrostructureMaskError(
            "apply_mask_to_predictions: predictions must have a "
            "(datetime, instrument) MultiIndex; got "
            f"{type(predictions.index).__name__}."
        )

    # Vectorized (audit P1): build the (date_iso, str_inst) key per row via
    # factorize + gather (element-bit-identical to the old per-row loop — see
    # _iso_dates_for), then ONE vectorized membership test. Both sides of the
    # comparison are plain (str, str) tuples after the conversion above, so
    # MultiIndex.isin's engine lookup is exact — no dtype coercion subtleties
    # remain (they were consumed by ts_to_iso_date/str, same as the old loop).
    date_level = predictions.index.get_level_values("datetime")
    inst_level = predictions.index.get_level_values("instrument")
    date_isos = _iso_dates_for(date_level)
    inst_strs = _str_insts_for(inst_level)
    key_index = pd.MultiIndex.from_arrays([date_isos, inst_strs])
    dropped = key_index.isin(pair_set)
    n_dropped = int(dropped.sum())

    if n_dropped == 0:
        # Mask was non-empty but didn't hit any predictions row —
        # likely a different instrument universe. No-op fast path.
        return predictions, 0

    filtered = predictions[~dropped]
    return filtered, n_dropped


__all__ = [
    "MicrostructureMaskError",
    "MicrostructureMaskResult",
    "apply_mask_to_predictions",
    "compute_unavailable_mask",
]
