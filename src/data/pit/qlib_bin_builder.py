"""Build qlib bin storage from Tushare daily + adj_factor + daily_basic + delisted_registry.

Pipeline (Phase B.2, per docs/pit/pit_universe_design.md §5 Stage 5)
-------------------------------------------------------------------
::

    <tushare_dir>/daily/{year}/{ticker}.parquet
    <tushare_dir>/adj_factor/{year}/{ticker}.parquet
    <tushare_dir>/daily_basic/{year}/{ticker}.parquet   (optional)
    <tushare_dir>/active_stocks.parquet
    <delisted_registry_path>
       -> <output_dir>/calendars/day.txt
       -> <output_dir>/features/<ticker_lower>/{open,high,low,close,volume,money}.day.bin
       -> <output_dir>/features/<ticker_lower>/{pe,pb,ps,turnover_rate,circ_mv,total_mv}.day.bin
          (only when a daily_basic parquet exists for the ticker)

qlib bin format (matches src/data/tushare/provider_bundle/publisher.py
``_write_qlib_bundle``):

- ``calendars/day.txt`` — one ISO date per line, sorted.
- ``features/<lowercase_ticker>/<field>.day.bin`` — little-endian
  float32 sequence. First element is the ``start_index`` (offset into
  the calendar where this ticker's data begins). Subsequent elements
  are the field values aligned to consecutive calendar dates from
  ``start_index`` to ``start_index + len-1``.

NaN-after-delist invariant
--------------------------
For delisted tickers, the ticker's bin contains valid values from
``list_date`` to ``delist_date`` and NaN for every calendar date
strictly after ``delist_date``. This is the structural defence in
docs/pit/pit_universe_design.md §4.3.

For active tickers, valid values extend to the latest calendar date
in the Tushare daily dump.

Borrow-shell tickers (e.g. ``SH600145``) are NOT in delisted_registry
and so get a continuous data run. Their continuity is implicit (no
NaN-padding logic touches them); §4.6.

Adjusted-price contract
-----------------------
The bin stores PRE-ADJUSTED prices (close × adj_factor and same for
open/high/low). adj_factor is Tushare's as-of-today snapshot per
§4.3.1, so absolute adjusted prices are NOT PIT-correct features.
Downstream consumers MUST use within-ticker ratios / returns only
(the contract is enforced at the Phase C query layer).

Scope (Phase B.2)
-----------------
- 6 OHLCV fields: open, high, low, close, volume, money.
- 6 OPTIONAL ``daily_basic`` fields: pe, pb, ps, turnover_rate,
  circ_mv, total_mv. Emitted only for tickers that have a
  ``daily_basic/<year>/<ticker>.parquet`` payload in the source dump.
  Backward-compatible: a bundle built from an older Tushare snapshot
  (no daily_basic dir) still produces the same 6 OHLCV bins per
  ticker.
- Volume in Tushare's ``vol`` (lots / 手, ×100 to shares).
- Amount in Tushare's ``amount`` (千元, ×1000 to yuan).
- daily_basic fields are written as-is (no unit conversion); they
  share the per-ticker calendar alignment, NaN-after-delist mask,
  and start_idx convention with the OHLCV bins.
- Per-ticker DataFrame load and reindex to a global calendar.
- Atomic-rename of the final provider directory (no half-written
  partial provider visible to qlib mid-write).

Out of scope
------------
- vwap / factor / change derived fields (computable in qlib
  expressions; can land in a follow-up).
- Sub-snapshot timing for borrow-shell restructure annotation
  (attribution-layer concern per §4.6).
- Manual delist_date overrides (design §13 q2 backlog).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from src.core.logger import get_logger

_logger = get_logger(__name__)


# qlib bin field set this builder produces. Order is documentational only —
# each field becomes its own .bin file.
BIN_FEATURE_FIELDS: tuple[str, ...] = (
    "open", "high", "low", "close", "volume", "money",
)

# Optional daily_basic fields (Tushare ``daily_basic`` endpoint, per the
# ``extend-feature-universe-with-daily-basic`` OpenSpec change). The
# builder emits these bins ONLY when a daily_basic parquet exists for
# the ticker; PIT bundles built before daily_basic was ingested still
# work (they just lack these six fields). No unit conversion is applied
# (Tushare publishes daily_basic in the units we want: PE/PB/PS as
# unitless ratios, turnover_rate as a percentage, circ_mv / total_mv
# as 万元).
BIN_DAILY_BASIC_FIELDS: tuple[str, ...] = (
    "pe", "pb", "ps", "turnover_rate", "circ_mv", "total_mv",
)

# Tushare unit conversions (per src/data/tushare/provider_bundle/_types).
TUSHARE_VOL_LOTS_TO_SHARES = 100  # vol is in 手 (100 shares)
TUSHARE_AMOUNT_KYUAN_TO_YUAN = 1000  # amount is in 千元


# Consolidated into ``src.data.pit._common`` (bug.md P2-4).
from src.data.pit._common import to_iso_date as _to_iso_date  # noqa: E402
from src.data.pit._common import to_qlib_ticker as _to_qlib_ticker  # noqa: E402


class QlibBinBuilderError(RuntimeError):
    """Raised when bin construction fails."""


@dataclass(frozen=True)
class QlibBinBuilderResult:
    output_dir: Path
    calendar_days: int
    ticker_count: int
    delisted_ticker_count: int
    skipped_no_data: int  # tickers with no daily data in tushare_dir


class QlibBinBuilder:
    """Build a qlib provider directory from Tushare dumps + delisted registry.

    Construction is cheap; ``build()`` does the work. Idempotent given
    identical inputs.
    """

    def __init__(
        self,
        tushare_dir: Path,
        delisted_registry_path: Path,
        output_dir: Path,
    ) -> None:
        self._tushare_dir = tushare_dir
        self._delisted_registry_path = delisted_registry_path
        self._output_dir = output_dir

    # ------------------------------------------------------------------
    # Public orchestrator
    # ------------------------------------------------------------------

    def build(self) -> QlibBinBuilderResult:
        active_df = self._load_active_stocks()
        delisted_df = self._load_delisted_registry()

        active_tickers = self._tickers_from_active(active_df)
        delisted_tickers = self._tickers_from_delisted(delisted_df)
        universe = active_tickers | set(delisted_tickers.keys())
        _logger.info(
            "Universe: %d tickers (%d active, %d delisted)",
            len(universe), len(active_tickers), len(delisted_tickers),
        )

        # Load all per-ticker data into memory first so we can compute
        # the calendar (union of all observed trade dates) before
        # writing. For a real backfill the universe is ~5500 tickers
        # × ~6000 days ≈ 33M rows; with 6 float32 columns ≈ 800 MB.
        # Acceptable on a workstation; in a memory-constrained
        # environment we'd stream this differently.
        per_ticker: dict[str, pd.DataFrame] = {}
        # Per-ticker mask: which daily_basic fields exist for this
        # ticker. Empty set => no daily_basic parquet => emit only the
        # 6 OHLCV bins (backward-compat with pre-daily_basic bundles).
        per_ticker_daily_basic_fields: dict[str, tuple[str, ...]] = {}
        skipped = 0
        for qlib_ticker in sorted(universe):
            tushare_code = self._qlib_to_tushare(qlib_ticker)
            df = self._load_ticker_history(tushare_code)
            if df is None or df.empty:
                skipped += 1
                continue
            df = self._apply_adjustment(df, tushare_code)
            df, daily_basic_fields = self._merge_daily_basic(df, tushare_code)
            df = self._clip_to_listing_window(df, qlib_ticker, delisted_tickers)
            if df.empty:
                skipped += 1
                continue
            per_ticker[qlib_ticker] = df
            per_ticker_daily_basic_fields[qlib_ticker] = daily_basic_fields

        if not per_ticker:
            raise QlibBinBuilderError(
                "No ticker produced any rows after listing-window clipping. "
                "Check that Phase A.1 daily/ parquets are populated."
            )

        calendar = self._build_global_calendar(per_ticker)
        calendar_index = {d: i for i, d in enumerate(calendar)}

        # Write atomically: first to a temp dir, then rename.
        staging = self._output_dir.parent / f".{self._output_dir.name}.tmp"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)

        try:
            self._write_calendar(calendar, staging)
            self._write_instruments_all(active_df, delisted_df, staging)
            for qlib_ticker, df in per_ticker.items():
                self._write_one_ticker_bins(
                    qlib_ticker,
                    df,
                    calendar,
                    calendar_index,
                    staging,
                    daily_basic_fields=per_ticker_daily_basic_fields.get(qlib_ticker, ()),
                )
            # Promote staging to final location
            if self._output_dir.exists():
                # Backup pattern: rename existing to .bak before swap
                backup = self._output_dir.parent / f".{self._output_dir.name}.bak"
                if backup.exists():
                    shutil.rmtree(backup)
                self._output_dir.rename(backup)
            staging.rename(self._output_dir)
            # Clean up backup on success
            backup = self._output_dir.parent / f".{self._output_dir.name}.bak"
            if backup.exists():
                shutil.rmtree(backup)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            raise

        _logger.info(
            "Wrote provider %s: %d tickers across %d calendar days (skipped: %d)",
            self._output_dir, len(per_ticker), len(calendar), skipped,
        )
        return QlibBinBuilderResult(
            output_dir=self._output_dir,
            calendar_days=len(calendar),
            ticker_count=len(per_ticker),
            delisted_ticker_count=len(delisted_tickers),
            skipped_no_data=skipped,
        )

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------

    def _load_active_stocks(self) -> pd.DataFrame:
        path = self._tushare_dir / "active_stocks.parquet"
        if not path.exists():
            raise QlibBinBuilderError(
                f"Missing {path}; run Phase A.1 with --endpoints stock_basic."
            )
        df = pd.read_parquet(path)
        if "ts_code" not in df.columns:
            raise QlibBinBuilderError(
                f"{path} missing required column 'ts_code'"
            )
        return df

    def _load_delisted_registry(self) -> pd.DataFrame:
        path = self._delisted_registry_path
        if not path.exists():
            raise QlibBinBuilderError(
                f"Missing {path}; run Phase A.2 (02_build_delisted_registry.py)."
            )
        df = pd.read_parquet(path)
        required = {"ticker", "list_date", "delist_date"}
        missing = required - set(df.columns)
        if missing:
            raise QlibBinBuilderError(
                f"{path} missing required columns: {sorted(missing)}"
            )
        return df

    @staticmethod
    def _tickers_from_active(df: pd.DataFrame) -> set[str]:
        return {_to_qlib_ticker(str(t)) for t in df["ts_code"]}

    @staticmethod
    def _tickers_from_delisted(df: pd.DataFrame) -> dict[str, pd.Timestamp]:
        """Map qlib-style ticker -> delist_date Timestamp."""
        return {
            str(r["ticker"]): pd.Timestamp(r["delist_date"])
            for _, r in df.iterrows()
        }

    @staticmethod
    def _qlib_to_tushare(qlib_ticker: str) -> str:
        """``SH600519`` -> ``600519.SH``. Inverse of _to_qlib_ticker."""
        if len(qlib_ticker) >= 8 and qlib_ticker[:2].isalpha() and qlib_ticker[2:].isdigit():
            return f"{qlib_ticker[2:]}.{qlib_ticker[:2]}"
        return qlib_ticker  # already Tushare-style or unknown

    def _load_ticker_history(self, tushare_code: str) -> pd.DataFrame | None:
        """Load and concatenate all per-year daily parquets for one ticker.

        Returns None if no parquets exist (ticker may have been added to
        the universe but never pulled — common for short Phase A.1 runs).
        """
        daily_root = self._tushare_dir / "daily"
        if not daily_root.exists():
            raise QlibBinBuilderError(
                f"Missing {daily_root}; run Phase A.1 with --endpoints daily."
            )
        chunks: list[pd.DataFrame] = []
        for year_dir in sorted(daily_root.iterdir()):
            if not year_dir.is_dir():
                continue
            ticker_path = year_dir / f"{tushare_code}.parquet"
            if not ticker_path.exists():
                continue
            chunk = pd.read_parquet(ticker_path)
            if not chunk.empty:
                chunks.append(chunk)
        if not chunks:
            return None
        df = pd.concat(chunks, ignore_index=True)
        df["trade_date"] = df["trade_date"].astype(str)
        return df.drop_duplicates("trade_date").sort_values("trade_date").reset_index(drop=True)

    def _load_adj_factor(self, tushare_code: str) -> pd.DataFrame | None:
        """Same shape as _load_ticker_history but for the adj_factor dump."""
        adj_root = self._tushare_dir / "adj_factor"
        if not adj_root.exists():
            return None
        chunks: list[pd.DataFrame] = []
        for year_dir in sorted(adj_root.iterdir()):
            if not year_dir.is_dir():
                continue
            ticker_path = year_dir / f"{tushare_code}.parquet"
            if not ticker_path.exists():
                continue
            chunk = pd.read_parquet(ticker_path)
            if not chunk.empty:
                chunks.append(chunk)
        if not chunks:
            return None
        df = pd.concat(chunks, ignore_index=True)
        df["trade_date"] = df["trade_date"].astype(str)
        return df.drop_duplicates("trade_date").sort_values("trade_date").reset_index(drop=True)

    def _load_daily_basic(self, tushare_code: str) -> pd.DataFrame | None:
        """Same shape as _load_ticker_history but for the daily_basic dump.

        Returns None when EITHER the ``daily_basic/`` root does not
        exist (e.g. PIT bundle built before daily_basic ingest was
        wired up) OR no per-year parquet exists for this ticker.
        Either case is silent: the builder will simply skip the six
        daily_basic bins for that ticker. The OHLCV bins are still
        produced as before.
        """
        basic_root = self._tushare_dir / "daily_basic"
        if not basic_root.exists():
            return None
        chunks: list[pd.DataFrame] = []
        for year_dir in sorted(basic_root.iterdir()):
            if not year_dir.is_dir():
                continue
            ticker_path = year_dir / f"{tushare_code}.parquet"
            if not ticker_path.exists():
                continue
            chunk = pd.read_parquet(ticker_path)
            if not chunk.empty:
                chunks.append(chunk)
        if not chunks:
            return None
        df = pd.concat(chunks, ignore_index=True)
        df["trade_date"] = df["trade_date"].astype(str)
        return df.drop_duplicates("trade_date").sort_values("trade_date").reset_index(drop=True)

    # ------------------------------------------------------------------
    # Per-ticker transforms
    # ------------------------------------------------------------------

    def _apply_adjustment(
        self, daily: pd.DataFrame, tushare_code: str,
    ) -> pd.DataFrame:
        """Multiply OHLC by adj_factor (or 1.0 if adj_factor is missing).

        Forward-fills adj_factor across days where it's not reported,
        which matches qlib convention and avoids inserting NaN into
        price columns when only the factor row is sparse.
        """
        adj = self._load_adj_factor(tushare_code)
        out = daily.copy()
        if adj is None or adj.empty:
            # No adjustment data at all — keep raw prices (factor 1.0).
            out["adj_factor"] = 1.0
        else:
            # P1-10 (Phase 3 P3-1): validate the RAW adj_factor BEFORE the
            # ffill/fillna can mask a corrupt value. A present row whose factor
            # is non-finite (inf / NaN) or <= 0 is corrupt and fails loud here;
            # validating post-fill would miss a raw NaN entirely, since
            # ffill/fillna sanitizes it to a prior factor or 1.0 first (codex P2
            # on PR #230). A date simply ABSENT from the adj source is not a row
            # here, so it still falls through the left-merge and fills to 1.0
            # (the documented no-adjustment behavior). Mirrors the publisher,
            # which likewise validates its raw staged adj_factor.
            self._validate_adj_factor(adj, tushare_code)
            out = out.merge(
                adj[["trade_date", "adj_factor"]],
                on="trade_date", how="left",
            )
            out["adj_factor"] = out["adj_factor"].ffill().fillna(1.0)
        for col in ("open", "high", "low", "close"):
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce") * out["adj_factor"]
        # Volume in shares (Tushare returns lots).
        if "vol" in out.columns:
            out["volume"] = pd.to_numeric(out["vol"], errors="coerce") * TUSHARE_VOL_LOTS_TO_SHARES
        # Money in yuan (Tushare returns kyuan).
        if "amount" in out.columns:
            out["money"] = pd.to_numeric(out["amount"], errors="coerce") * TUSHARE_AMOUNT_KYUAN_TO_YUAN
        return out

    @staticmethod
    def _validate_adj_factor(adj_df: pd.DataFrame, tushare_code: str) -> None:
        """Fail-loud if any RAW adj_factor that will scale prices is non-finite
        (inf / NaN) or <= 0.

        Validates the raw adj_factor source (before the per-day ffill/fillna in
        ``_apply_adjustment``), so a present-row ``NaN`` is caught rather than
        silently sanitized to a prior factor / 1.0. A corrupt factor would
        otherwise multiply into the OHLC columns: inf -> inf prices, 0 -> zeroed,
        negative -> sign-flipped, NaN -> a wrong / unadjusted price, all written
        straight into the production PIT bins.

        Verbatim mirror of the operator-UI publisher's staged-adj validation
        (``src/data/tushare/provider_bundle/publisher.py``: the non-finite /
        non-positive adjustment-factor checks on the raw staged frame) — same
        predicate AND same raw input; only the control flow differs (the builder
        ORs the two conditions and raises immediately, having no
        error-accumulation framework). A date ABSENT from this source is not a
        row here, so it is NOT flagged — it falls through the left-merge and
        fills to 1.0 (the documented no-adjustment behavior).

        Kept as a DELIBERATE short-term duplicate rather than a shared validator
        while the publisher-retirement (builder unification) assessment is open;
        the production builder must NOT import the publisher (wrong dependency
        direction). P1-10 / Phase 3 P3-1.
        """
        factor = pd.to_numeric(adj_df["adj_factor"], errors="coerce")
        non_finite = ~np.isfinite(factor)
        non_positive = factor <= 0
        bad = non_finite | non_positive
        if not bool(bad.any()):
            return
        offenders = adj_df.loc[bad, ["trade_date", "adj_factor"]].head(5)
        sample = ", ".join(
            f"({td}: {val})"
            for td, val in offenders.itertuples(index=False, name=None)
        )
        raise QlibBinBuilderError(
            f"{tushare_code}: adj_factor must be finite and > 0, but "
            f"{int(bad.sum())} row(s) are non-finite (inf/NaN) or <= 0. A "
            "non-finite factor yields inf / unadjusted prices, 0 zeroes them, "
            "and a negative factor sign-flips them; all silently corrupt the "
            f"bins. First offenders (trade_date: adj_factor): {sample}. Refusing "
            "to write corrupt bins; fix the tushare adj_factor dump for this "
            "ticker."
        )

    def _merge_daily_basic(
        self, daily: pd.DataFrame, tushare_code: str,
    ) -> tuple[pd.DataFrame, tuple[str, ...]]:
        """Merge daily_basic columns into ``daily`` on ``trade_date``.

        Returns ``(merged_df, fields_actually_present)``.

        Backward-compatible: if no daily_basic parquet exists for this
        ticker (or the daily_basic root is absent), returns the
        unchanged daily df + an empty tuple of fields. The bin writer
        then emits only the 6 OHLCV bins for that ticker.

        Per-row daily_basic values are NOT forward-filled. The merge
        is left-join on ``trade_date`` so non-trading days fall away
        naturally, and a day where Tushare returned a row in
        ``daily/`` but no matching row in ``daily_basic/`` (rare but
        possible) gets NaN for the fundamental fields — the same
        no-fill / NaN-on-gap convention qlib uses for partial inputs.
        """
        basic = self._load_daily_basic(tushare_code)
        if basic is None or basic.empty:
            return daily, ()
        present = tuple(f for f in BIN_DAILY_BASIC_FIELDS if f in basic.columns)
        if not present:
            # daily_basic parquet exists but contains none of the six
            # canonical fields — treat as "no data" rather than
            # emitting all-NaN bins.
            return daily, ()
        merged = daily.merge(
            basic[["trade_date", *present]],
            on="trade_date", how="left",
        )
        for field in present:
            merged[field] = pd.to_numeric(merged[field], errors="coerce")
        return merged, present

    @staticmethod
    def _clip_to_listing_window(
        df: pd.DataFrame, qlib_ticker: str,
        delisted_tickers: dict[str, pd.Timestamp],
    ) -> pd.DataFrame:
        """Drop rows strictly after ``delist_date`` for delisted tickers.

        Active tickers pass through unchanged. Delisted tickers get
        their post-delist tail removed before the per-ticker DataFrame
        is reindexed against the global calendar — the reindex then
        naturally produces NaN for those post-delist calendar days.
        """
        if qlib_ticker not in delisted_tickers:
            return df
        delist_dt = delisted_tickers[qlib_ticker]
        ts = pd.to_datetime(df["trade_date"], format="%Y%m%d", errors="coerce")
        keep = ts <= delist_dt
        return df[keep].copy()

    # ------------------------------------------------------------------
    # Global calendar
    # ------------------------------------------------------------------

    @staticmethod
    def _build_global_calendar(
        per_ticker: dict[str, pd.DataFrame],
    ) -> list[str]:
        """Union of every observed trade_date across all tickers, ISO format."""
        dates: set[str] = set()
        for df in per_ticker.values():
            dates.update(df["trade_date"].astype(str))
        return sorted(_to_iso_date(d) for d in dates)

    # ------------------------------------------------------------------
    # Bin writers (matches src/data/tushare/provider_bundle/publisher
    #              ::_write_qlib_bundle)
    # ------------------------------------------------------------------

    @staticmethod
    def _write_calendar(calendar: list[str], output_dir: Path) -> None:
        calendars_dir = output_dir / "calendars"
        calendars_dir.mkdir(parents=True, exist_ok=True)
        (calendars_dir / "day.txt").write_text(
            "\n".join(calendar) + "\n", encoding="utf-8",
        )

    @staticmethod
    def _write_instruments_all(
        active_df: pd.DataFrame, delisted_df: pd.DataFrame, output_dir: Path,
    ) -> None:
        """Write ``instruments/all.txt`` inside the staging dir so the
        atomic rename swap produces a complete qlib provider in one
        shot. Without this, a normal ``04 -> 05 -> 06`` pipeline run
        loses Phase B.1's all.txt the moment B.2's swap fires, and
        the validator's sanity check then refuses to run. Codex P1
        review on PR #103.
        """
        rows: list[tuple[str, str, str]] = []
        for _, r in active_df.iterrows():
            ticker = _to_qlib_ticker(str(r["ts_code"]))
            ld = str(r.get("list_date", "")) if "list_date" in r else ""
            if len(ld) == 8 and ld.isdigit():
                rows.append((ticker, f"{ld[:4]}-{ld[4:6]}-{ld[6:8]}", "2099-12-31"))
            # Active rows with malformed list_date silently fall through —
            # the universe builder Phase B.1 (when run separately) would
            # surface this loudly; here we are defensive.
        for _, r in delisted_df.iterrows():
            ticker = str(r["ticker"])
            list_dt = pd.Timestamp(r["list_date"])
            delist_dt = pd.Timestamp(r["delist_date"])
            if pd.isna(list_dt) or pd.isna(delist_dt):
                continue
            rows.append((
                ticker,
                list_dt.strftime("%Y-%m-%d"),
                delist_dt.strftime("%Y-%m-%d"),
            ))
        rows.sort(key=lambda r: r[0])

        instr_dir = output_dir / "instruments"
        instr_dir.mkdir(parents=True, exist_ok=True)
        with (instr_dir / "all.txt").open("w", encoding="utf-8", newline="\n") as fh:
            for ticker, start, end in rows:
                fh.write(f"{ticker}\t{start}\t{end}\n")

    @staticmethod
    def _write_one_ticker_bins(
        qlib_ticker: str,
        df: pd.DataFrame,
        calendar: list[str],
        calendar_index: dict[str, int],
        output_dir: Path,
        daily_basic_fields: tuple[str, ...] = (),
    ) -> None:
        """Write the .day.bin files for one ticker, aligned to ``calendar``.

        Always emits the 6 OHLCV fields. Additionally emits the
        ``daily_basic_fields`` subset of ``BIN_DAILY_BASIC_FIELDS`` —
        passing ``()`` (the default) means "no daily_basic for this
        ticker, skip those bins entirely". Per-field NaN handling
        within the aligned window is identical to the OHLCV path: any
        calendar day that's in the aligned slice but missing from the
        source df becomes NaN, which means the PIT NaN-after-delist
        mask (the row drop in ``_clip_to_listing_window``) propagates
        to daily_basic bins for free.

        The bin file layout matches qlib's native format and
        ``src/data/tushare/provider_bundle/publisher.py``: first
        ``float32`` is ``start_index`` (offset into the calendar
        where this ticker's data begins), followed by one value per
        consecutive calendar day from ``start_index`` to ``start_index +
        len(values) - 1``.
        """
        # Reindex to ISO dates so we can map to calendar indices.
        iso_dates = [_to_iso_date(d) for d in df["trade_date"]]
        ticker_df = df.copy()
        ticker_df["iso_date"] = iso_dates
        ticker_df = ticker_df.drop_duplicates("iso_date").set_index("iso_date").sort_index()

        start = ticker_df.index.min()
        start_idx = calendar_index[start]
        # The bin extends from this ticker's first observed date to the
        # LAST calendar date, NaN-padding the tail. For delisted tickers
        # the tail is the post-delist NaN window — qlib reads NaN, not
        # "no data", so D.features returns the NaN row instead of an
        # empty DataFrame. For active tickers the tail is usually empty
        # (last observed date == last calendar date) so the NaN-padding
        # is a no-op. Discovered during Phase B smoke against real
        # Tushare: the prior end_idx = calendar_index[end] truncation
        # made qlib return empty for any post-delist query.
        date_slice = calendar[start_idx:]
        aligned = ticker_df.reindex(date_slice)

        feats_dir = output_dir / "features" / qlib_ticker.lower()
        feats_dir.mkdir(parents=True, exist_ok=True)

        start_index = float(start_idx)
        for field in BIN_FEATURE_FIELDS:
            if field in aligned.columns:
                values = pd.to_numeric(aligned[field], errors="coerce").astype("float32").to_numpy()
            else:
                # Field missing from source (e.g. no 'money' if Tushare
                # didn't return amount). Write NaN for every aligned day
                # so the bin shape is still consistent across tickers.
                values = np.full(len(aligned), np.nan, dtype="float32")
            payload = np.hstack([[start_index], values]).astype("<f4")
            payload.tofile(feats_dir / f"{field}.day.bin")

        # Optional daily_basic bins. Skip entirely when the per-ticker
        # mask is empty — backward-compat for bundles built before
        # daily_basic ingest landed. When the mask lists a subset of
        # BIN_DAILY_BASIC_FIELDS, only those bins are written; the
        # caller already verified each listed field is present in df.
        for field in daily_basic_fields:
            if field not in aligned.columns:
                # Defensive: caller should have screened this out, but
                # don't emit an all-NaN bin if it slipped through —
                # silently skip instead so the bundle stays minimal.
                continue
            values = pd.to_numeric(aligned[field], errors="coerce").astype("float32").to_numpy()
            payload = np.hstack([[start_index], values]).astype("<f4")
            payload.tofile(feats_dir / f"{field}.day.bin")
