"""Build qlib's ``instruments/all.txt`` from active + delisted tickers.

Pipeline (Phase B.1, per docs/pit/pit_universe_design.md §5 Stage 4)
-------------------------------------------------------------------
::

    <tushare_dir>/active_stocks.parquet  (Phase A.1)
    <tushare_dir>/delisted_registry.parquet  (Phase A.2 output)
       -> <output_dir>/instruments/all.txt
       (qlib tab-separated 3-column format: ``ticker  start  end``)

Index-specific files (``csi300.txt``, ``csi500.txt``, ``csi800.txt``)
are produced by Phase A.4 directly into the same ``instruments/``
directory. This module touches ``all.txt`` only.

Scope (Phase B.1)
-----------------
- Active tickers: ``<list_date>  2099-12-31`` per qlib's
  "still active" convention.
- Delisted tickers: ``<list_date>  <delist_date>`` from the
  registry.
- Sorting: by ticker (deterministic output across runs).

Out of scope
------------
- No intersection refinement of csi*.txt with delist boundaries.
  Phase A.4 already emits index runs that terminate at the last
  snapshot where the ticker was present, which is naturally close
  to (but not exactly) the delist_date. Sub-snapshot precision is
  Phase E backlog (§4.5).
- No qlib bin writes (Phase B.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.core.logger import get_logger

_logger = get_logger(__name__)


# Consolidated into ``src.data.pit._common`` (bug.md P2-4). QLIB_OPEN_END_DATE
# is re-exported (kept in this module's namespace) so existing importers and
# tests that read it from here keep working.
from src.data.pit._common import QLIB_OPEN_END_DATE  # noqa: E402
from src.data.pit._common import to_iso_date as _to_iso_date  # noqa: E402
from src.data.pit._common import to_qlib_ticker as _to_qlib_ticker  # noqa: E402


class UniverseFilesError(RuntimeError):
    """Raised when universe-file construction fails."""


@dataclass(frozen=True)
class UniverseFilesResult:
    output_path: Path
    active_count: int
    delisted_count: int
    total_rows: int


class UniverseFilesBuilder:
    """Build ``instruments/all.txt`` from delisted registry + active stocks."""

    def __init__(
        self,
        tushare_dir: Path,
        delisted_registry_path: Path,
        output_dir: Path,
    ) -> None:
        self._tushare_dir = tushare_dir
        self._delisted_registry_path = delisted_registry_path
        self._output_dir = output_dir

    def build(self) -> UniverseFilesResult:
        active = self._load_active_stocks()
        delisted = self._load_delisted_registry()

        active_rows = self._active_rows(active)
        delisted_rows = self._delisted_rows(delisted)

        # Sort for determinism + assert no ticker appears in both buckets.
        active_tickers = {r[0] for r in active_rows}
        delisted_tickers = {r[0] for r in delisted_rows}
        overlap = active_tickers & delisted_tickers
        if overlap:
            raise UniverseFilesError(
                f"{len(overlap)} ticker(s) appear in BOTH active and delisted "
                f"buckets (sample: {sorted(overlap)[:3]}). Run Phase A.2 "
                f"first; its active_control validator would have caught this."
            )

        all_rows = sorted(active_rows + delisted_rows, key=lambda r: r[0])
        output_path = self._output_dir / "instruments" / "all.txt"
        self._atomic_write_instruments(all_rows, output_path)
        _logger.info(
            "Wrote %s: %d rows (%d active, %d delisted)",
            output_path, len(all_rows), len(active_rows), len(delisted_rows),
        )
        return UniverseFilesResult(
            output_path=output_path,
            active_count=len(active_rows),
            delisted_count=len(delisted_rows),
            total_rows=len(all_rows),
        )

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------

    def _load_active_stocks(self) -> pd.DataFrame:
        path = self._tushare_dir / "active_stocks.parquet"
        if not path.exists():
            raise UniverseFilesError(
                f"Missing {path}; run Phase A.1 with --endpoints stock_basic."
            )
        df = pd.read_parquet(path)
        required = {"ts_code", "list_date"}
        missing = required - set(df.columns)
        if missing:
            raise UniverseFilesError(
                f"{path} missing required columns: {sorted(missing)}"
            )
        if df.empty:
            raise UniverseFilesError(f"{path} is empty.")
        return df

    def _load_delisted_registry(self) -> pd.DataFrame:
        path = self._delisted_registry_path
        if not path.exists():
            raise UniverseFilesError(
                f"Missing {path}; run Phase A.2 (02_build_delisted_registry.py)."
            )
        df = pd.read_parquet(path)
        required = {"ticker", "list_date", "delist_date"}
        missing = required - set(df.columns)
        if missing:
            raise UniverseFilesError(
                f"{path} missing required columns: {sorted(missing)}"
            )
        return df

    # ------------------------------------------------------------------
    # Row builders
    # ------------------------------------------------------------------

    @staticmethod
    def _active_rows(active: pd.DataFrame) -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []
        for _, r in active.iterrows():
            try:
                start = _to_iso_date(r["list_date"])
            except ValueError as exc:
                raise UniverseFilesError(
                    f"active stocks row {r['ts_code']!r}: {exc}"
                ) from exc
            rows.append((_to_qlib_ticker(str(r["ts_code"])), start, QLIB_OPEN_END_DATE))
        return rows

    @staticmethod
    def _delisted_rows(delisted: pd.DataFrame) -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []
        for _, r in delisted.iterrows():
            list_dt = pd.Timestamp(r["list_date"])
            delist_dt = pd.Timestamp(r["delist_date"])
            if pd.isna(list_dt) or pd.isna(delist_dt):
                raise UniverseFilesError(
                    f"delisted registry row {r['ticker']!r} has NaT date "
                    f"(list_date={list_dt!r}, delist_date={delist_dt!r})"
                )
            rows.append((
                str(r["ticker"]),
                list_dt.strftime("%Y-%m-%d"),
                delist_dt.strftime("%Y-%m-%d"),
            ))
        return rows

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    @staticmethod
    def _atomic_write_instruments(
        rows: list[tuple[str, str, str]], path: Path,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8", newline="\n") as fh:
            for ticker, start, end in rows:
                fh.write(f"{ticker}\t{start}\t{end}\n")
        tmp_path.replace(path)
