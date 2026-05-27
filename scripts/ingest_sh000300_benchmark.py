"""Ingest CSI 300 index (SH000300) daily OHLC+volume into the qlib binary bundle.

The user's qlib data bundle at ``D:/qlib_data/my_cn_data`` does not contain
``SH000300`` — backtests using ``benchmark_code="SH000300"`` therefore fall
over reading zero benchmark rows.  The user supplied a raw spreadsheet at

    D:/qlib_data/my_cn_data/000300perf (1).xlsx

(5082 daily bars from 2005-04-08 through 2026-03-10, sheet ``000300perf``).
This script converts that spreadsheet into the qlib on-disk feature
format so the index is available as a standalone instrument called
``SH000300`` (aka ``sh000300`` on disk).

Qlib day-frequency binary format
--------------------------------
Per instrument, per field::

    features/<inst_lower>/<field>.day.bin

The file is a little-endian ``float32`` sequence:

* ``arr[0]`` is the calendar offset of the first value — cast to ``int``
  it becomes the index into ``calendars/day.txt`` at which ``arr[1]`` sits.
* ``arr[1:]`` are the per-trading-day values, in calendar order.  Gaps
  (days the instrument didn't trade) are ``NaN``.

Pipeline
--------
1. Read the spreadsheet, normalise columns, keep only dates inside the
   qlib calendar (``<= 2026-03-06`` in this bundle; the spreadsheet
   extends to 2026-03-10, which we drop).
2. Align the series to the calendar slice starting at the first observed
   date, filling any gaps with ``NaN``.
3. Write ``close`` / ``open`` / ``high`` / ``low`` / ``volume`` /
   ``factor`` into ``features/sh000300/*.day.bin``.  ``factor`` is
   identically ``1.0`` (indices don't adjust for splits / dividends).
4. Back up and extend ``instruments/all.txt`` with one row::

       SH000300\\t<first_date>\\t<last_date>

5. Verify by re-reading the data through ``qlib.data.D.features``.

Idempotency
-----------
The script detects a pre-existing ``features/sh000300/`` directory and
refuses to proceed unless ``--force`` is given.  The ``all.txt`` line is
only appended if ``SH000300`` is not already listed, so re-runs don't
silently duplicate the entry.

Usage
-----
    D:/Python/Python11/python.exe scripts/ingest_sh000300_benchmark.py
    D:/Python/Python11/python.exe scripts/ingest_sh000300_benchmark.py --force
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

QLIB_ROOT = Path(r"D:/qlib_data/my_cn_data")
XLSX_PATH = QLIB_ROOT / "000300perf (1).xlsx"
CALENDAR_PATH = QLIB_ROOT / "calendars" / "day.txt"
ALL_TXT_PATH = QLIB_ROOT / "instruments" / "all.txt"
FEATURES_DIR = QLIB_ROOT / "features" / "sh000300"

INSTRUMENT_CODE = "SH000300"

# Columns to write. Index doesn't split/dividend so factor is identically 1.
# Volume unit in the spreadsheet is "Million Shares"; qlib stores raw share
# counts for equities. We multiply by 1e6 so that $volume on SH000300 has
# the same order of magnitude as equity volumes — benchmark backtests only
# read $close, but other handlers that touch $volume won't hit a scale surprise.
FIELD_MAP = {
    # qlib field  -> (xlsx column index, scale)
    "open":       (6,  1.0),
    "high":       (7,  1.0),
    "low":        (8,  1.0),
    "close":      (9,  1.0),
    "volume":     (12, 1_000_000.0),   # M Shares -> Shares
}
# 'factor' is synthesised below.


def _log(msg: str) -> None:
    print(f"[ingest-sh000300] {msg}", flush=True)


def _needs_leading_newline(raw_bytes: bytes) -> bool:
    """Return True iff appending a new line to this file would produce a
    malformed join (i.e. the last existing line lacks a terminator).

    The previous implementation relied on ``raw.splitlines()[-1].endswith("\\n")``,
    but ``splitlines()`` *strips* terminators — that check was always True
    for non-empty files, so a spurious blank line was written before every
    append. This helper inspects the raw trailing bytes instead, handling
    both Unix ``\\n`` and Windows ``\\r\\n`` line endings.

    An empty file does not need a leading newline (nothing to separate from).
    """
    if not raw_bytes:
        return False
    return not raw_bytes.endswith((b"\n", b"\r\n"))


def _load_calendar() -> list[str]:
    with open(CALENDAR_PATH, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]


def _load_spreadsheet() -> pd.DataFrame:
    df = pd.read_excel(XLSX_PATH)
    # Date column is int yyyymmdd; convert to ISO strings.
    raw_dates = df.iloc[:, 0].astype(int).astype(str)
    iso = pd.to_datetime(raw_dates, format="%Y%m%d").dt.strftime("%Y-%m-%d")
    normalised = pd.DataFrame({"date": iso})
    for field, (col_idx, scale) in FIELD_MAP.items():
        normalised[field] = df.iloc[:, col_idx].astype(np.float64) * scale
    normalised = normalised.sort_values("date").reset_index(drop=True)
    return normalised


def _align_to_calendar(
    data: pd.DataFrame, calendar: list[str],
) -> tuple[int, pd.DataFrame]:
    """Return ``(start_index, aligned_df)``.

    ``start_index`` is the calendar offset of the first data point.
    ``aligned_df`` is indexed by calendar dates from ``calendar[start_index]``
    through the last calendar date, with ``NaN`` on missing dates.
    """
    # Drop dates beyond the calendar's reach (e.g. 2026-03-10 in this bundle).
    cal_set = set(calendar)
    before = len(data)
    data = data[data["date"].isin(cal_set)].reset_index(drop=True)
    dropped = before - len(data)
    if dropped:
        _log(
            f"dropped {dropped} spreadsheet rows outside the qlib calendar "
            f"(calendar ends at {calendar[-1]})"
        )

    first_date = data["date"].iloc[0]
    start_index = calendar.index(first_date)
    tail = calendar[start_index:]
    _log(
        f"calendar slice: {tail[0]} ~ {tail[-1]} "
        f"({len(tail)} trading days, start_index={start_index})"
    )

    aligned = pd.DataFrame({"date": tail})
    aligned = aligned.merge(data, on="date", how="left")

    gaps = aligned.iloc[:, 1:].isna().any(axis=1).sum()
    if gaps:
        _log(
            f"{gaps} calendar days have no spreadsheet row — writing NaN "
            "for those days"
        )
    return start_index, aligned


def _write_bin(field_path: Path, start_index: int, values: "np.ndarray[Any, Any]") -> None:
    field_path.parent.mkdir(parents=True, exist_ok=True)
    # Qlib format: first cell is start_index (as float32), then values.
    payload = np.empty(len(values) + 1, dtype=np.float32)
    payload[0] = np.float32(start_index)
    payload[1:] = values.astype(np.float32)
    payload.tofile(field_path)


def _update_all_txt(first_date: str, last_date: str) -> None:
    # Read raw bytes — we need to know whether the file itself ends with a
    # newline, NOT whether the last post-splitlines string ends with one
    # (splitlines strips them, so the old check was always True and we were
    # writing a spurious blank line before every append — polluting the
    # instrument registry).
    raw_bytes = ALL_TXT_PATH.read_bytes()
    lines = raw_bytes.decode("utf-8").splitlines()
    existing = [ln for ln in lines if ln.startswith(f"{INSTRUMENT_CODE}\t")]
    if existing:
        _log(
            f"{INSTRUMENT_CODE} already listed in all.txt ({existing[0]}); "
            "leaving instrument registry untouched"
        )
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = ALL_TXT_PATH.with_suffix(f".txt.bak_preingest_{ts}")
    shutil.copy2(ALL_TXT_PATH, backup)
    _log(f"backed up instruments/all.txt -> {backup.name}")

    new_line = f"{INSTRUMENT_CODE}\t{first_date}\t{last_date}"
    with open(ALL_TXT_PATH, "a", encoding="utf-8") as f:
        if _needs_leading_newline(raw_bytes):
            f.write("\n")
        f.write(new_line + "\n")
    _log(f"appended to all.txt: {new_line}")


def _verify() -> None:
    """Round-trip through qlib to confirm the write."""
    _log("verifying via qlib.data.D.features...")
    import qlib
    from qlib.config import REG_CN
    qlib.init(provider_uri=str(QLIB_ROOT), region=REG_CN)
    from qlib.data import D
    df = D.features(
        [INSTRUMENT_CODE], ["$close", "$open", "$volume"],
        start_time="2025-07-01", end_time="2025-12-31", freq="day",
    )
    if df is None or df.empty:
        raise RuntimeError(
            f"verification failed: D.features returned empty for {INSTRUMENT_CODE}"
        )
    _log(f"ok — {len(df)} rows for {INSTRUMENT_CODE} in 2025-07~12")
    _log(f"first close: {df['$close'].iloc[0]:.2f}, "
         f"last close: {df['$close'].iloc[-1]:.2f}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true",
        help="overwrite an existing features/sh000300/ directory",
    )
    args = parser.parse_args()

    if not XLSX_PATH.exists():
        _log(f"ERROR: spreadsheet not found at {XLSX_PATH}")
        return 1
    if not CALENDAR_PATH.exists():
        _log(f"ERROR: calendar not found at {CALENDAR_PATH}")
        return 1
    if not ALL_TXT_PATH.exists():
        _log(f"ERROR: instruments/all.txt not found at {ALL_TXT_PATH}")
        return 1

    if FEATURES_DIR.exists() and not args.force:
        _log(
            f"features/sh000300/ already exists. Pass --force to overwrite. "
            f"Current contents: {[p.name for p in FEATURES_DIR.iterdir()]}"
        )
        return 1

    calendar = _load_calendar()
    _log(f"calendar: {calendar[0]} ~ {calendar[-1]} ({len(calendar)} days)")

    data = _load_spreadsheet()
    _log(
        f"spreadsheet: {len(data)} rows, "
        f"dates {data['date'].iloc[0]} ~ {data['date'].iloc[-1]}"
    )

    start_index, aligned = _align_to_calendar(data, calendar)

    FEATURES_DIR.mkdir(parents=True, exist_ok=True)
    for field in FIELD_MAP:
        path = FEATURES_DIR / f"{field}.day.bin"
        _write_bin(path, start_index, aligned[field].to_numpy())
        _log(f"wrote {path.relative_to(QLIB_ROOT)}  ({len(aligned)} values)")

    # factor = 1.0 always (index, no adjustment).
    factor_values = np.ones(len(aligned), dtype=np.float32)
    _write_bin(FEATURES_DIR / "factor.day.bin", start_index, factor_values)
    _log(f"wrote features/sh000300/factor.day.bin  ({len(aligned)} values, all 1.0)")

    _update_all_txt(
        first_date=aligned["date"].iloc[0],
        last_date=aligned["date"].iloc[-1],
    )

    _verify()
    _log("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
