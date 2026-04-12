"""Shared base for TemporalArtifactLoader implementations.

Universe and Taxonomy loaders are structurally identical — the only
differences are:

* the second base column (``in_universe`` vs ``industry_code``)
* the mode constants and supported-modes tuple
* the profile dataclass returned
* the error class raised

This module factors out 100% of the CSV-parsing and coverage-computation
logic. Concrete subclasses supply the five class-level attributes listed
in :class:`TemporalArtifactLoaderBase` and override ``_build_profile`` to
construct the domain-specific profile object.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional

from src.data.trading_calendar import TradingCalendar


# ---------------------------------------------------------------------------
# Shared internal CSV read result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _CsvReadOutcome:
    rows: int
    columns_present: tuple[str, ...]
    snapshot_start: Optional[str]
    snapshot_end: Optional[str]
    has_future_effective_data: bool
    max_trade_date: Optional[str]
    # Distinct trade-date count is computed in one pass (no second file read)
    distinct_trade_date_count: int = 0


# ---------------------------------------------------------------------------
# Base loader
# ---------------------------------------------------------------------------

class TemporalArtifactLoaderBase:
    """Abstract-ish base for universe/taxonomy artifact loaders.

    Subclasses MUST set these class-level attributes:

    * ``BASE_COLUMNS`` — e.g. ``("instrument", "in_universe")``
    * ``TRADE_DATE_COLUMN`` — always ``"trade_date"``
    * ``RANGE_COLUMNS``    — always ``("effective_start", "effective_end")``
    * ``MODE_TRADE_DATE``  — e.g. ``"trade_date"``
    * ``MODE_RANGE``       — e.g. ``"range"``
    * ``SUPPORTED_MODES``  — tuple of valid mode strings
    * ``_ERROR_CLASS``     — exception class to raise on structural misuse
    """

    BASE_COLUMNS: tuple[str, ...] = ()
    TRADE_DATE_COLUMN: str = "trade_date"
    RANGE_COLUMNS: tuple[str, ...] = ("effective_start", "effective_end")
    MODE_TRADE_DATE: str = "trade_date"
    MODE_RANGE: str = "range"
    SUPPORTED_MODES: tuple[str, ...] = ()
    _ERROR_CLASS: type = ValueError

    # ------------------------------------------------------------------
    # Public entry point (called by subclass .load())
    # ------------------------------------------------------------------

    @classmethod
    def _load_impl(
        cls,
        artifact_path: str,
        manifest_path: str,
        temporal_mode: str,
        reference_date: Optional[str],
        calendar: Optional[TradingCalendar],
    ) -> tuple[_CsvReadOutcome, Mapping[str, Any], bool, bool, Optional[int], Optional[float]]:
        """Run the shared load logic and return computed values.

        Returns
        -------
        (outcome, metadata, artifact_present, manifest_present,
         stale_days, coverage_ratio)
        """
        if not str(artifact_path or "").strip():
            raise cls._ERROR_CLASS("artifact_path is required.")
        if not str(manifest_path or "").strip():
            raise cls._ERROR_CLASS("manifest_path is required.")
        if temporal_mode not in cls.SUPPORTED_MODES:
            raise cls._ERROR_CLASS(
                f"Unsupported temporal_mode '{temporal_mode}'. "
                f"Allowed: {cls.SUPPORTED_MODES}."
            )

        reference = cls._parse_iso_date(reference_date)

        artifact_file = Path(artifact_path)
        manifest_file = Path(manifest_path)
        artifact_present = artifact_file.is_file()
        manifest_present = manifest_file.is_file()

        metadata: Mapping[str, Any] = (
            cls._read_manifest(manifest_file) if manifest_present else {}
        )

        if artifact_present:
            outcome = cls._read_csv(artifact_file, temporal_mode, reference)
        else:
            outcome = _CsvReadOutcome(
                rows=0, columns_present=(), snapshot_start=None,
                snapshot_end=None, has_future_effective_data=False,
                max_trade_date=None, distinct_trade_date_count=0,
            )

        # stale_days
        stale_days: Optional[int] = None
        if reference is not None and outcome.snapshot_end is not None:
            end_date = cls._parse_iso_date(outcome.snapshot_end)
            if end_date is not None:
                stale_days = max((reference - end_date).days, 0)

        # coverage_ratio
        coverage_ratio: Optional[float] = None
        if (
            temporal_mode == cls.MODE_TRADE_DATE
            and calendar is not None
            and outcome.rows > 0
            and outcome.snapshot_start is not None
            and outcome.snapshot_end is not None
        ):
            start_date = cls._parse_iso_date(outcome.snapshot_start)
            end_date = cls._parse_iso_date(outcome.snapshot_end)
            if start_date is not None and end_date is not None and end_date >= start_date:
                expected_rows = max(calendar.count_trading_days(start_date, end_date), 1)
                # distinct_trade_date_count is populated in the single CSV pass
                coverage_ratio = min(
                    outcome.distinct_trade_date_count / expected_rows, 1.0
                )

        return outcome, metadata, artifact_present, manifest_present, stale_days, coverage_ratio

    # ------------------------------------------------------------------
    # CSV parsing — single pass collects everything including distinct dates
    # ------------------------------------------------------------------

    @classmethod
    def _read_csv(
        cls,
        artifact_file: Path,
        temporal_mode: str,
        reference: Optional[date],
    ) -> _CsvReadOutcome:
        rows = 0
        has_future_effective_data = False
        header_normalized: tuple[str, ...] = ()
        min_trade_date: Optional[date] = None
        max_trade_date: Optional[date] = None
        min_effective_start: Optional[date] = None
        max_effective_end: Optional[date] = None
        distinct_trade_dates: set[str] = set()

        try:
            with artifact_file.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                try:
                    header = next(reader)
                except StopIteration:
                    return _CsvReadOutcome(0, (), None, None, False, None, 0)

                header_normalized = tuple(
                    col.strip().lower() for col in header if col.strip()
                )

                def _index(column: str) -> int:
                    try:
                        return header_normalized.index(column)
                    except ValueError:
                        return -1

                trade_date_idx = _index(cls.TRADE_DATE_COLUMN)
                eff_start_idx = _index(cls.RANGE_COLUMNS[0])
                eff_end_idx = _index(cls.RANGE_COLUMNS[1])

                for record in reader:
                    if not record or all(not str(cell).strip() for cell in record):
                        continue
                    rows += 1

                    if (
                        temporal_mode == cls.MODE_TRADE_DATE
                        and trade_date_idx >= 0
                        and trade_date_idx < len(record)
                    ):
                        raw = str(record[trade_date_idx]).strip()
                        parsed = cls._parse_iso_date(raw)
                        if parsed is not None:
                            if min_trade_date is None or parsed < min_trade_date:
                                min_trade_date = parsed
                            if max_trade_date is None or parsed > max_trade_date:
                                max_trade_date = parsed
                            if reference is not None and parsed > reference:
                                has_future_effective_data = True
                            # Accumulate for coverage computation (no second read)
                            distinct_trade_dates.add(raw)

                    if temporal_mode == cls.MODE_RANGE:
                        if eff_start_idx >= 0 and eff_start_idx < len(record):
                            parsed = cls._parse_iso_date(str(record[eff_start_idx]).strip())
                            if parsed is not None:
                                if min_effective_start is None or parsed < min_effective_start:
                                    min_effective_start = parsed
                        if eff_end_idx >= 0 and eff_end_idx < len(record):
                            parsed = cls._parse_iso_date(str(record[eff_end_idx]).strip())
                            if parsed is not None:
                                if max_effective_end is None or parsed > max_effective_end:
                                    max_effective_end = parsed
                                if reference is not None and parsed > reference:
                                    has_future_effective_data = True

        except FileNotFoundError:
            return _CsvReadOutcome(0, (), None, None, False, None, 0)
        except OSError as exc:
            raise cls._ERROR_CLASS(
                f"Cannot read artifact CSV at '{artifact_path}': {exc}"
            ) from exc

        # Build columns_present restricted to columns the contract cares about
        recognised: list[str] = []
        for col in cls.BASE_COLUMNS:
            if col in header_normalized:
                recognised.append(col)
        if temporal_mode == cls.MODE_TRADE_DATE:
            if cls.TRADE_DATE_COLUMN in header_normalized:
                recognised.append(cls.TRADE_DATE_COLUMN)
        elif temporal_mode == cls.MODE_RANGE:
            for col in cls.RANGE_COLUMNS:
                if col in header_normalized:
                    recognised.append(col)

        if temporal_mode == cls.MODE_TRADE_DATE:
            snapshot_start = min_trade_date.isoformat() if min_trade_date else None
            snapshot_end = max_trade_date.isoformat() if max_trade_date else None
        elif temporal_mode == cls.MODE_RANGE:
            snapshot_start = min_effective_start.isoformat() if min_effective_start else None
            snapshot_end = max_effective_end.isoformat() if max_effective_end else None
        else:  # static
            snapshot_start = None
            snapshot_end = None

        return _CsvReadOutcome(
            rows=rows,
            columns_present=tuple(recognised),
            snapshot_start=snapshot_start,
            snapshot_end=snapshot_end,
            has_future_effective_data=has_future_effective_data,
            max_trade_date=max_trade_date.isoformat() if max_trade_date else None,
            distinct_trade_date_count=len(distinct_trade_dates),
        )

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_iso_date(value: Optional[str]) -> Optional[date]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None

    @staticmethod
    def _read_manifest(manifest_file: Path) -> Mapping[str, Any]:
        try:
            with manifest_file.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return payload
