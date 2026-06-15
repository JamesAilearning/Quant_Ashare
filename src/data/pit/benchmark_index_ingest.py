"""Ingest benchmark INDEX daily series into a qlib provider bundle (PR-E).

Why this exists
---------------
A backtest with ``benchmark_code="SH000300"`` reads ``features/sh000300/
close.day.bin`` from the bundle. The legacy path (``scripts/
ingest_sh000300_benchmark.py``, retired here) read a one-off xlsx and wrote
those bins POST HOC into the LIVE bundle — so the daily-update atomic swap
(``src.data_pipeline.bundle_swap``) erased them on the next rebuild, and the
series it carried was the CSI 300 PRICE index. The price index excludes
reinvested dividends while strategy returns include them (adjusted closes),
so benchmarking against it overstates excess return by roughly the index
dividend yield (audit E2; ~2-2.5pp annualized on CSI 300).

This module is the builder-adjacent fix: it writes benchmark-index bins +
a registry entry in ``instruments/benchmark.txt`` (a SEPARATE universe
file, NOT ``all.txt`` — the benchmark must stay out of the stock training
universe; codex P1 on #243) INTO A CALLER-PROVIDED bundle dir, so a rebuild
writes them into STAGING (like steps 03/04) and the swap preserves them.
The total-return index (``H00300.CSI``) is the canonical benchmark; the
price index (``000300.SH``) is kept for reference.

qlib day-frequency binary format
--------------------------------
Per instrument, per field ``features/<inst_lower>/<field>.day.bin`` — a
little-endian float32 sequence whose first element is the calendar offset
of the first value and the rest are per-trading-day values (NaN on gaps).

Total-return indices may publish CLOSE ONLY (no intraday OHLC / volume). The
benchmark return series qlib computes uses ``$close`` only (``report.py``:
``$close/Ref($close,1)-1``); the OHLC fields are written equal to close so
the instrument has a consistent level rather than NaN, and ``$volume`` is
NaN (a benchmark index has no tradable volume and nothing reads it). No
``$factor`` bin is written — equities in this bundle carry none either, and
the benchmark read path never adjusts by factor.

Intra-span calendar GAPS (days the index did not publish, inside its active
window) are FORWARD-FILLED, not left NaN. qlib turns a NaN benchmark close
into a fabricated 0% return via ``report.py``'s ``.fillna(0)`` — and it
poisons TWO days (the gap day and the recovery day, whose ``Ref($close,1)``
pulls the NaN), silently dropping the true cross-gap index move from the
cumulative benchmark. Forward-filling the level makes the gap day a true 0%
and the recovery day carry the real move.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


class BenchmarkIngestError(RuntimeError):
    """Raised on a malformed index frame, calendar mismatch, or write
    failure — fail loud rather than write a half-formed benchmark
    instrument the backtest would silently read as zero rows."""


# qlib field -> source column in the tushare index_daily frame. ``close`` is
# load-bearing (the benchmark return series); the OHLC fields fall back to
# close when the source omits them (total-return indices), so they are never
# NaN-on-a-trading-day. ``volume`` has no fallback (NaN when absent).
_PRICE_FIELDS = ("open", "high", "low", "close")


@dataclass(frozen=True)
class BenchmarkIngestResult:
    """Summary of one index ingest."""

    instrument_code: str
    first_date: str
    last_date: str
    n_trading_days: int
    n_gap_days: int  # intra-span calendar days the source did not publish
    ohlc_degenerate: bool  # source published close only (no usable OHLC)


def _load_calendar(provider_dir: Path) -> list[str]:
    path = provider_dir / "calendars" / "day.txt"
    if not path.exists():
        raise BenchmarkIngestError(
            f"calendar not found at {path}; the bundle must be built "
            "(calendars/day.txt present) before benchmark ingest."
        )
    with path.open(encoding="utf-8") as fh:
        cal = [ln.strip() for ln in fh if ln.strip()]
    if not cal:
        raise BenchmarkIngestError(f"calendar at {path} is empty.")
    return cal


def _normalize_index_daily(
    df: pd.DataFrame, instrument_code: str,
) -> tuple[pd.DataFrame, bool]:
    """tushare index_daily frame -> ``(data, ohlc_degenerate)``.

    ``data`` has ``date`` (ISO) + price fields, sorted. Requires
    ``trade_date`` + ``close``. OHLC fall back to ``close`` when absent or
    NaN (total-return indices). ``ohlc_degenerate`` reflects the SOURCE
    (before fallback): True iff the source carries no usable open/high/low.
    Fails loud on a missing close, an all-NaN close, or duplicate dates."""
    if "trade_date" not in df.columns or "close" not in df.columns:
        raise BenchmarkIngestError(
            f"{instrument_code}: index_daily frame must carry 'trade_date' "
            f"and 'close'; got columns {list(df.columns)}."
        )
    if df.empty:
        raise BenchmarkIngestError(f"{instrument_code}: index_daily frame is empty.")

    try:
        iso = pd.to_datetime(
            df["trade_date"].astype(str), format="%Y%m%d",
        ).dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError) as exc:
        # A malformed trade_date is a source-contract violation, not an
        # internal error — raise the typed error so the CLI maps it to a
        # stage exit code instead of escaping into the orchestrator (codex
        # P2 on #243).
        raise BenchmarkIngestError(
            f"{instrument_code}: unparseable trade_date in index_daily "
            f"(expected YYYYMMDD): {exc}"
        ) from exc
    out = pd.DataFrame({"date": iso})
    close = pd.to_numeric(df["close"], errors="coerce")
    # Every row in ``df`` is a PUBLISHED day, so any NaN here is a corrupt
    # source close (null / non-numeric), NOT an absent calendar day. The
    # intra-span ffill exists only for dates the source did not publish at
    # all (they appear as NaN only after the calendar merge); a published
    # row with an invalid close must fail loud, never be silently
    # forward-filled into a fabricated 0% benchmark return (codex P2 on #243).
    if bool(close.isna().any()):
        bad = df.loc[close.isna().to_numpy(), "trade_date"].astype(str).head(5).tolist()
        raise BenchmarkIngestError(
            f"{instrument_code}: {int(close.isna().sum())} published row(s) "
            f"have a null / non-numeric close (e.g. {bad}); refusing to "
            "fabricate a benchmark return for a corrupt source row. Fix the "
            "index_daily source."
        )
    out["close"] = close.to_numpy()
    # ohlc_degenerate is computed from the SOURCE: do any of open/high/low
    # exist as columns AND carry a real value anywhere? (A total-return
    # index publishing close only has none.)
    ohlc_present = any(
        field in df.columns
        and bool(pd.to_numeric(df[field], errors="coerce").notna().any())
        for field in ("open", "high", "low")
    )
    for field in ("open", "high", "low"):
        if field in df.columns:
            col = pd.to_numeric(df[field], errors="coerce")
            # Fall back to close where the source omits the field (NaN).
            out[field] = col.where(col.notna(), close).to_numpy()
        else:
            out[field] = close.to_numpy()
    out["volume"] = (
        pd.to_numeric(df["vol"], errors="coerce").to_numpy()
        if "vol" in df.columns else np.full(len(df), np.nan)
    )
    out = out.sort_values("date").reset_index(drop=True)
    if out["date"].duplicated().any():
        dup = out.loc[out["date"].duplicated(), "date"].head(3).tolist()
        raise BenchmarkIngestError(
            f"{instrument_code}: duplicate trade_date(s) in index_daily "
            f"(e.g. {dup}); refusing to write an ambiguous series."
        )
    return out, (not ohlc_present)


def _align_to_calendar(
    data: pd.DataFrame, calendar: list[str], instrument_code: str,
) -> tuple[int, pd.DataFrame]:
    """``(start_index, aligned)``: aligned to the calendar slice from the
    first published date THROUGH THE LAST PUBLISHED DATE (inclusive), with
    NaN on intra-span calendar days the index skipped.

    The slice STOPS at the last published date — it does NOT extend to the
    calendar tail (codex P1 on PR #243). If the index lags the calendar (a
    daily update whose `index_daily` has not printed today's row yet), a
    forward-fill to `calendar[-1]` would FABRICATE trailing benchmark closes
    and register the instrument through a date it never published, silently
    turning an incomplete fetch into 0% benchmark returns. Ending at the
    last published date leaves the trailing days with no benchmark data at
    all — honest, and the backtest window normally ends at/before the data
    tail anyway."""
    cal_index = {d: i for i, d in enumerate(calendar)}
    in_cal = data[data["date"].isin(cal_index)].reset_index(drop=True)
    if in_cal.empty:
        raise BenchmarkIngestError(
            f"{instrument_code}: no index date falls inside the bundle "
            f"calendar ({calendar[0]}..{calendar[-1]}). Wrong calendar or "
            "wrong index?"
        )
    start_index = cal_index[in_cal["date"].iloc[0]]
    end_index = cal_index[in_cal["date"].iloc[-1]]
    span = calendar[start_index:end_index + 1]
    aligned = pd.DataFrame({"date": span}).merge(in_cal, on="date", how="left")
    return start_index, aligned


def _write_bin(field_path: Path, start_index: int, values: np.ndarray[Any, Any]) -> None:
    field_path.parent.mkdir(parents=True, exist_ok=True)
    payload = np.empty(len(values) + 1, dtype=np.float32)
    payload[0] = np.float32(start_index)
    payload[1:] = values.astype(np.float32)
    payload.tofile(field_path)


BENCHMARK_INSTRUMENTS_FILE = "benchmark.txt"


def _register_benchmark(
    provider_dir: Path, instrument_code: str, first_date: str, last_date: str,
) -> None:
    """Idempotently register the instrument in ``instruments/benchmark.txt``
    — a SEPARATE universe file, NOT ``all.txt`` (codex P1 on #243).

    ``all.txt`` is the stock TRAINING universe (``instruments: all``);
    appending a benchmark index there would make ``FeatureDatasetBuilder``
    train Alpha158 on a non-tradable index (no factor bin) and could put it
    back into the exchange ``codes`` set the backtest excludes. A benchmark
    is read by EXPLICIT ``benchmark_code`` via ``D.features`` (verified:
    qlib reads its bins by name regardless of universe membership), so it
    never needs to be in ``all``; storing it under ``benchmark.txt`` keeps
    the training universe clean while leaving the benchmark queryable as
    ``D.instruments(\"benchmark\")``.

    Replaces any existing line for this code (a re-ingest with a wider date
    range updates the span, not duplicates), preserving every other line and
    the newline discipline. Created if absent (the builder writes ``all.txt``
    only)."""
    instr_dir = provider_dir / "instruments"
    instr_dir.mkdir(parents=True, exist_ok=True)
    path = instr_dir / BENCHMARK_INSTRUMENTS_FILE
    lines = (
        path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    )
    kept = [ln for ln in lines if not ln.startswith(f"{instrument_code}\t")]
    kept.append(f"{instrument_code}\t{first_date}\t{last_date}")
    path.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")

    # Migration scrub (codex P2 on #243): the retired xlsx ingest wrote the
    # benchmark into all.txt. A standalone run against such a bundle would
    # leave the index in the training universe even after this ingest moves
    # it to benchmark.txt. Remove any matching row from all.txt — a
    # benchmark code is never a real equity, so this only undoes the legacy
    # contamination. (The orchestrator path is already clean: 05 rewrites a
    # fresh equities-only all.txt before 07 runs.)
    all_path = instr_dir / "all.txt"
    if all_path.exists():
        all_lines = all_path.read_text(encoding="utf-8").splitlines()
        scrubbed = [ln for ln in all_lines if not ln.startswith(f"{instrument_code}\t")]
        if len(scrubbed) != len(all_lines):
            all_path.write_text(
                "\n".join(scrubbed) + "\n", encoding="utf-8", newline="\n",
            )


def ingest_benchmark_index(
    index_daily: pd.DataFrame,
    *,
    instrument_code: str,
    provider_dir: Path,
) -> BenchmarkIngestResult:
    """Write one benchmark index into ``provider_dir`` as a qlib instrument.

    ``index_daily`` is a tushare ``index_daily`` frame (``trade_date`` +
    ``close`` required; OHLC/``vol`` optional). ``instrument_code`` is the
    qlib instrument name (e.g. ``SH000300``); bins land in
    ``features/<lower>/``. ``provider_dir`` is the bundle to write into —
    pass the STAGING dir during a rebuild so the atomic swap preserves the
    benchmark (the legacy xlsx path wrote into LIVE and the swap erased it).
    Calendar + registry are read from ``provider_dir`` (built by step 05).
    """
    calendar = _load_calendar(provider_dir)
    data, ohlc_degenerate = _normalize_index_daily(index_daily, instrument_code)
    start_index, aligned = _align_to_calendar(data, calendar, instrument_code)

    # Count intra-span gaps (calendar days the source did not publish) BEFORE
    # forward-filling them — the count is the diagnostic; the ffill is the
    # fix. ffill carries the last published level across the gap so qlib's
    # benchmark return is a true 0% on the gap day and the real move on the
    # recovery day, instead of NaN -> .fillna(0) silently dropping the move.
    n_gap = int(aligned["close"].isna().sum())
    for field in (*_PRICE_FIELDS, "volume"):
        aligned[field] = aligned[field].ffill()

    features_dir = provider_dir / "features" / instrument_code.lower()
    features_dir.mkdir(parents=True, exist_ok=True)
    for field in _PRICE_FIELDS:
        _write_bin(
            features_dir / f"{field}.day.bin", start_index,
            aligned[field].to_numpy(dtype="float64"),
        )
    _write_bin(
        features_dir / "volume.day.bin", start_index,
        aligned["volume"].to_numpy(dtype="float64"),
    )
    # No factor bin: equities in this bundle carry none, and the benchmark
    # read path (report.py: $close/Ref($close,1)-1) never adjusts by factor.

    first_date = aligned["date"].iloc[0]
    last_date = aligned["date"].iloc[-1]
    _register_benchmark(provider_dir, instrument_code, first_date, last_date)

    return BenchmarkIngestResult(
        instrument_code=instrument_code,
        first_date=first_date,
        last_date=last_date,
        n_trading_days=len(aligned),
        n_gap_days=n_gap,
        ohlc_degenerate=ohlc_degenerate,
    )
