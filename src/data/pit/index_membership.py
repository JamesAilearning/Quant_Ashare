"""Resolve historical index membership from Tushare ``index_weight`` dumps.

Pipeline
--------
::

    <tushare_dir>/index_weight/{index_code}.parquet
       -> <output_dir>/instruments/{csi300,csi500,csi800}.txt
       (qlib's native tab-separated 3-column format:
        ``ticker  start_date  end_date``)

For each index, walks Tushare's monthly snapshots and emits one row per
contiguous run of snapshots where a ticker was a member. Members present
in the most recent snapshot get ``end_date = 2099-12-31`` (qlib's
"still active" convention).

Reference validation
--------------------
For each ``index_membership_cases.<index>`` row in
``tests/pit/reference_cases.yaml``:

- ``action: enter`` -> assert some emitted run has ``start_date`` within
  ``±MEMBERSHIP_DATE_TOLERANCE_DAYS`` (default 35) of the asserted date.
- ``action: leave`` -> assert some emitted run has ``end_date`` within
  the same tolerance.

The 35-day tolerance is calibrated for Tushare's monthly snapshot
cadence: an intra-month entry / leave will surface in the next snapshot
~30 days later, so 35 days catches it without false matches against an
unrelated nearby snapshot.

Known data-granularity caveat (per ``reference_cases.yaml``)
------------------------------------------------------------
The reference case ``SH600015`` ("华夏银行" leave CSI300 on 2022-06-13)
falls inside a single month where Tushare returned both ``20220601`` and
``20220630`` snapshots showing PRESENT. This resolver will emit a run
ending at the last present snapshot (~``20220630``); the validator's
35-day tolerance catches this within the asserted-leave boundary. If a
future PIT phase needs sub-monthly precision, it must consult Tushare's
``index_dailybasic`` or constituent-change announcements directly.

Out of scope for Phase A.4
--------------------------
- No bin writes (Phase B.2).
- No sub-monthly precision (see caveat above).
- No reconstruction of pre-2005 index history (Tushare data starts later
  than the design's "covers 2005-present" target for some indices).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.core.logger import get_logger

_logger = get_logger(__name__)


# Tushare ts_code -> short name used for the output file and the
# reference YAML key. Design §4.2: csi300.txt / csi500.txt / csi800.txt.
DEFAULT_INDEX_FILE_MAP: dict[str, str] = {
    "000300.SH": "csi300",
    "000905.SH": "csi500",
    "000906.SH": "csi800",
}

MEMBERSHIP_DATE_TOLERANCE_DAYS = 35

QLIB_OPEN_END_DATE = "2099-12-31"


# Consolidated into ``src.data.pit._common`` (bug.md P2-4). The
# leading-underscore re-exports preserve the existing call-site
# names so nothing else in this module needs to change.
from src.data.pit._common import to_iso_date as _to_iso_date  # noqa: E402
from src.data.pit._common import to_qlib_ticker as _to_qlib_ticker  # noqa: E402


class IndexMembershipError(RuntimeError):
    """Raised when membership resolution / validation fails."""


@dataclass(frozen=True)
class IndexMembershipResult:
    """Per-index summary returned by :meth:`IndexMembershipResolver.resolve`."""

    index_code: str
    output_path: Path
    run_count: int
    distinct_tickers: int
    reference_rows_matched: int
    earliest_snapshot: str  # YYYY-MM-DD
    latest_snapshot: str    # YYYY-MM-DD


class IndexMembershipResolver:
    """Convert Tushare ``index_weight`` snapshots into qlib instruments files."""

    def __init__(
        self,
        tushare_dir: Path,
        output_dir: Path,
        reference_cases_path: Path | None = None,
        indices: tuple[str, ...] = tuple(DEFAULT_INDEX_FILE_MAP.keys()),
    ) -> None:
        bad = tuple(i for i in indices if i not in DEFAULT_INDEX_FILE_MAP)
        if bad:
            raise IndexMembershipError(
                f"Unknown index code(s) {bad!r}; valid: "
                f"{tuple(DEFAULT_INDEX_FILE_MAP.keys())}"
            )
        self._tushare_dir = tushare_dir
        self._output_dir = output_dir
        self._reference_cases_path = reference_cases_path
        self._indices = indices

    # ------------------------------------------------------------------
    # Public orchestrator
    # ------------------------------------------------------------------

    def resolve(self) -> list[IndexMembershipResult]:
        references = self._load_reference_cases() if self._reference_cases_path else {}
        out_dir = self._output_dir / "instruments"
        out_dir.mkdir(parents=True, exist_ok=True)

        results: list[IndexMembershipResult] = []
        for index_code in self._indices:
            short = DEFAULT_INDEX_FILE_MAP[index_code]
            snapshots = self._load_snapshots(index_code)
            runs = self._build_runs(snapshots)
            output_path = out_dir / f"{short}.txt"
            self._atomic_write_instruments(runs, output_path)

            ref_matched = self._validate_references(runs, snapshots, references, short)

            snapshot_dates = sorted(snapshots["trade_date"].unique())
            results.append(IndexMembershipResult(
                index_code=index_code,
                output_path=output_path,
                run_count=len(runs),
                distinct_tickers=len({r[0] for r in runs}),
                reference_rows_matched=ref_matched,
                earliest_snapshot=_to_iso_date(snapshot_dates[0]),
                latest_snapshot=_to_iso_date(snapshot_dates[-1]),
            ))
            _logger.info(
                "Wrote %s: %d runs across %d tickers (snapshots %s -> %s, "
                "reference rows matched: %d)",
                output_path, results[-1].run_count, results[-1].distinct_tickers,
                results[-1].earliest_snapshot, results[-1].latest_snapshot,
                ref_matched,
            )
        return results

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------

    def _load_snapshots(self, index_code: str) -> pd.DataFrame:
        path = self._tushare_dir / "index_weight" / f"{index_code}.parquet"
        if not path.exists():
            raise IndexMembershipError(
                f"Missing {path}; run Phase A.1 (01_fetch_tushare.py) "
                f"with --endpoints index_weight first."
            )
        df = pd.read_parquet(path)
        required = {"con_code", "trade_date"}
        missing = required - set(df.columns)
        if missing:
            raise IndexMembershipError(
                f"{path} missing required columns: {sorted(missing)} "
                f"(found: {sorted(df.columns)})"
            )
        if df.empty:
            raise IndexMembershipError(
                f"{path} is empty — Tushare returned no snapshots."
            )
        # Normalise trade_date to YYYYMMDD strings for run detection
        df = df.copy()
        df["trade_date"] = df["trade_date"].astype(str)
        df["ticker"] = df["con_code"].astype(str).map(_to_qlib_ticker)
        return df

    def _load_reference_cases(self) -> dict[str, Any]:
        path = self._reference_cases_path
        # Callers only invoke this when ``_reference_cases_path`` is
        # truthy (see the ``if self._reference_cases_path else {}``
        # guard at the call site) — narrow ``Path | None`` → ``Path``
        # for mypy.
        if path is None:
            return {}
        if not path.exists():
            raise IndexMembershipError(
                f"Reference cases file not found: {path}"
            )
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise IndexMembershipError(
                f"{path}: expected mapping at top level, got {type(data).__name__}"
            )
        return data

    # ------------------------------------------------------------------
    # Run builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_runs(snapshots: pd.DataFrame) -> list[tuple[str, str, str]]:
        """Return list of ``(ticker, run_start_yyyymmdd, run_end_yyyymmdd_or_open)``.

        A run is a maximal contiguous sequence of snapshot dates where
        the ticker is present. ``run_end`` is the last snapshot date
        where the ticker appeared; if the run continues through the
        latest snapshot the end is set to ``20991231`` (qlib's "active"
        convention; will be rendered as ``2099-12-31`` on write).
        """
        all_dates = sorted(snapshots["trade_date"].unique())
        if not all_dates:
            return []
        latest = all_dates[-1]
        # Index for fast "is ticker in snapshot D" check
        presence: dict[str, set[str]] = {
            d: set() for d in all_dates
        }
        for _, row in snapshots.iterrows():
            presence[row["trade_date"]].add(row["ticker"])

        runs: list[tuple[str, str, str]] = []
        all_tickers = sorted({r["ticker"] for _, r in snapshots[["ticker"]].iterrows()})
        for ticker in all_tickers:
            in_run = False
            run_start: str | None = None
            run_end: str | None = None
            for d in all_dates:
                if ticker in presence[d]:
                    if not in_run:
                        run_start = d
                        in_run = True
                    run_end = d
                else:
                    if in_run:
                        # ``in_run`` is set together with ``run_start``
                        # / ``run_end`` above, so they are non-None
                        # whenever this branch fires; assert narrows
                        # ``str | None`` → ``str`` for mypy.
                        assert run_start is not None
                        assert run_end is not None
                        runs.append((ticker, run_start, run_end))
                        in_run = False
                        run_start = None
                        run_end = None
            if in_run:
                # Still member at latest snapshot — open-ended
                assert run_start is not None
                assert run_end is not None
                end_token = "20991231" if run_end == latest else run_end
                runs.append((ticker, run_start, end_token))

        runs.sort(key=lambda r: (r[0], r[1]))
        return runs

    # ------------------------------------------------------------------
    # Validator
    # ------------------------------------------------------------------

    def _validate_references(
        self,
        runs: list[tuple[str, str, str]],
        snapshots: pd.DataFrame,
        references: dict[str, Any],
        index_short: str,
    ) -> int:
        """Validate index_membership_cases entries against the resolved runs.

        Returns the count of matched reference rows. Mismatches raise
        with the reference date, the closest run boundary, and the
        delta in days so the operator can decide whether to update
        the reference, widen the tolerance, or chase the data.
        """
        cases = (references.get("index_membership_cases") or {}).get(index_short) or []
        if not cases:
            return 0

        matched = 0
        errors: list[str] = []
        tolerance = pd.Timedelta(days=MEMBERSHIP_DATE_TOLERANCE_DAYS)

        for case in cases:
            ticker = case.get("ticker")
            action = case.get("action")
            asserted = pd.Timestamp(case.get("date"))
            ticker_runs = [r for r in runs if r[0] == ticker]
            if not ticker_runs:
                errors.append(
                    f"  [{index_short}] {ticker!r} {action} on {asserted.date()}: "
                    f"ticker not in any resolved run"
                )
                continue
            if action == "enter":
                boundaries = [pd.Timestamp(_to_iso_date(r[1])) for r in ticker_runs]
                closest = min(boundaries, key=lambda b: abs(b - asserted))
            elif action == "leave":
                # For "leave", we want the END boundary of a closed run
                # (a run whose end_token is not 20991231).
                boundaries = [
                    pd.Timestamp(_to_iso_date(r[2]))
                    for r in ticker_runs
                    if r[2] != "20991231"
                ]
                if not boundaries:
                    errors.append(
                        f"  [{index_short}] {ticker!r} leave on {asserted.date()}: "
                        f"all resolved runs are open-ended — ticker still a member"
                    )
                    continue
                closest = min(boundaries, key=lambda b: abs(b - asserted))
            else:
                errors.append(
                    f"  [{index_short}] {ticker!r}: unknown action {action!r} "
                    f"(expected 'enter' or 'leave')"
                )
                continue

            delta = abs(closest - asserted)
            if delta > tolerance:
                errors.append(
                    f"  [{index_short}] {ticker!r} {action} on {asserted.date()}: "
                    f"closest run boundary {closest.date()} is {delta.days}d away "
                    f"(tolerance: {MEMBERSHIP_DATE_TOLERANCE_DAYS}d). "
                    f"Investigate or update reference."
                )
            else:
                matched += 1
                _logger.info(
                    "  ref match [%s]: %s %s %s -> run boundary %s (%+dd)",
                    index_short, ticker, action, asserted.date(),
                    closest.date(), (closest - asserted).days,
                )

        if errors:
            raise IndexMembershipError(
                "Index membership reference validation failed:\n"
                + "\n".join(errors)
            )
        return matched

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    @staticmethod
    def _atomic_write_instruments(
        runs: list[tuple[str, str, str]], path: Path,
    ) -> None:
        """Write qlib's tab-separated 3-column instruments format.

        Atomic rename so a killed process doesn't leave a partial file
        that downstream qlib would silently mis-read.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8", newline="\n") as fh:
            for ticker, start, end in runs:
                start_iso = _to_iso_date(start)
                end_iso = QLIB_OPEN_END_DATE if end == "20991231" else _to_iso_date(end)
                fh.write(f"{ticker}\t{start_iso}\t{end_iso}\n")
        tmp_path.replace(path)
