"""Benchmark artifact loader (file IO -> contract-consumable profile).

Responsibilities
----------------
- Read a benchmark artifact csv (columns: ``date,close``).
- Read a sidecar manifest json.
- Produce a :class:`BenchmarkArtifactProfile` that the existing
  :class:`BenchmarkDataContract` can validate unchanged.

Non-responsibilities
--------------------
- This loader does NOT implement benchmark selection semantics. It only
  materializes profile data from explicit paths. There is no registry
  lookup, no environment fallback, no implicit default.
- This loader does NOT call ``qlib.init`` or read from a qlib data
  provider. A separate future change will add a qlib-provider-backed
  publisher that produces the canonical csv + manifest shape that this
  loader consumes.

Data-level issues (missing rows, NaN close, schema gaps, temporal
leakage) are intentionally surfaced via the contract status produced by
``BenchmarkDataContract.validate_and_build_status``; they do NOT raise.
Only structural misuse (for example missing path arguments) raises.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional

from src.contracts.benchmark_data_contract import BenchmarkArtifactProfile
from src.data.trading_calendar import TradingCalendar


class BenchmarkArtifactLoaderError(ValueError):
    """Raised when the loader is called with structurally invalid arguments."""


@dataclass(frozen=True)
class _CsvReadOutcome:
    rows: int
    columns_present: tuple[str, ...]
    snapshot_start: Optional[str]
    snapshot_end: Optional[str]
    has_future_data: bool


class BenchmarkArtifactLoader:
    """Explicit path-based loader for benchmark csv + manifest artifacts."""

    EXPECTED_COLUMNS: tuple[str, ...] = ("date", "close")

    @classmethod
    def load(
        cls,
        artifact_path: str,
        manifest_path: str,
        reference_date: Optional[str] = None,
        calendar: Optional[TradingCalendar] = None,
    ) -> BenchmarkArtifactProfile:
        """Load an explicit artifact + manifest pair into a profile.

        Parameters
        ----------
        artifact_path:
            Absolute or repo-relative path to the benchmark csv.
        manifest_path:
            Absolute or repo-relative path to the sidecar manifest json.
        reference_date:
            ISO date string used to compute ``stale_days`` and detect
            future-dated rows. If ``None``, those fields are left as
            ``None`` / ``False``.
        calendar:
            Optional :class:`TradingCalendar`. When supplied, the
            ``coverage_ratio`` denominator is the real number of trading
            days inside ``[snapshot_start, snapshot_end]``. When omitted,
            the loader falls back to a calendar-free approximation
            (``span_days * 0.63``) so existing callers and tests do not
            need to construct a calendar.
        """
        if not str(artifact_path or "").strip():
            raise BenchmarkArtifactLoaderError("artifact_path is required.")
        if not str(manifest_path or "").strip():
            raise BenchmarkArtifactLoaderError("manifest_path is required.")

        reference = cls._parse_iso_date(reference_date)

        artifact_file = Path(artifact_path)
        manifest_file = Path(manifest_path)
        artifact_present = artifact_file.is_file()
        manifest_present = manifest_file.is_file()

        metadata: Mapping[str, Any] = cls._read_manifest(manifest_file) if manifest_present else {}

        if artifact_present:
            outcome = cls._read_csv(artifact_file, reference)
        else:
            outcome = _CsvReadOutcome(
                rows=0,
                columns_present=(),
                snapshot_start=None,
                snapshot_end=None,
                has_future_data=False,
            )

        stale_days: Optional[int] = None
        if reference is not None and outcome.snapshot_end is not None:
            end_date = cls._parse_iso_date(outcome.snapshot_end)
            if end_date is not None:
                stale_days = max((reference - end_date).days, 0)

        coverage_ratio: Optional[float] = None
        if (
            outcome.rows > 0
            and outcome.snapshot_start is not None
            and outcome.snapshot_end is not None
        ):
            start_date = cls._parse_iso_date(outcome.snapshot_start)
            end_date = cls._parse_iso_date(outcome.snapshot_end)
            if start_date is not None and end_date is not None and end_date >= start_date:
                if calendar is not None:
                    # Calendar-aware path: ask the injected TradingCalendar
                    # for the real number of trading days inside the
                    # inclusive [snapshot_start, snapshot_end] window.
                    expected_rows = max(
                        calendar.count_trading_days(start_date, end_date), 1
                    )
                else:
                    # Calendar-free fallback. A-share trading calendar
                    # yields roughly 230-245 trading days per year
                    # (weekends + public holidays), i.e. ~0.63 of calendar
                    # days. Using 5/7 over-counted and produced false
                    # "incomplete_coverage" warnings for realistic
                    # month-long windows that include holidays.
                    # Pass a TradingCalendar to ``load`` for accurate
                    # accounting; this fallback exists so contract-only
                    # tests and diagnostic scripts can still call the
                    # loader without constructing a calendar.
                    span_days = (end_date - start_date).days + 1
                    _A_SHARE_TRADING_DAY_RATIO = 0.63
                    expected_rows = max(int(round(span_days * _A_SHARE_TRADING_DAY_RATIO)), 1)
                coverage_ratio = min(outcome.rows / expected_rows, 1.0)

        manifest_snapshot_at_text = str(metadata.get("snapshot_at", "")).strip()
        manifest_snapshot_at = cls._parse_iso_date(manifest_snapshot_at_text or None)

        has_future_known_metadata = False
        if reference is not None and manifest_snapshot_at is not None:
            if manifest_snapshot_at > reference:
                has_future_known_metadata = True

        # snapshot_at vs csv-max-row-date strict-equality check.
        # Only fires when both values are present and parseable. Missing
        # snapshot_at is reported by the contract as schema_mismatch, missing
        # artifact is reported as missing_artifact_file -- both belong to
        # other error codes and must not contaminate this check.
        has_snapshot_at_mismatch = False
        if manifest_snapshot_at is not None and outcome.snapshot_end is not None:
            artifact_max_date = cls._parse_iso_date(outcome.snapshot_end)
            if artifact_max_date is not None and manifest_snapshot_at != artifact_max_date:
                has_snapshot_at_mismatch = True

        return BenchmarkArtifactProfile(
            artifact_path=str(artifact_path),
            manifest_path=str(manifest_path),
            artifact_present=artifact_present,
            manifest_present=manifest_present,
            metadata=metadata,
            rows=outcome.rows if artifact_present else None,
            columns_present=outcome.columns_present,
            snapshot_start=outcome.snapshot_start,
            snapshot_end=outcome.snapshot_end,
            stale_days=stale_days,
            coverage_ratio=coverage_ratio,
            has_future_data=outcome.has_future_data,
            has_future_known_metadata=has_future_known_metadata,
            has_snapshot_at_mismatch=has_snapshot_at_mismatch,
        )

    # -- Internal helpers --------------------------------------------------

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

    @classmethod
    def _read_csv(cls, artifact_file: Path, reference: Optional[date]) -> _CsvReadOutcome:
        rows = 0
        has_future_data = False
        close_has_nan = False
        header_normalized: tuple[str, ...] = ()
        min_date: Optional[date] = None
        max_date: Optional[date] = None

        try:
            with artifact_file.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                try:
                    header = next(reader)
                except StopIteration:
                    return _CsvReadOutcome(0, (), None, None, False)
                header_normalized = tuple(col.strip().lower() for col in header if col.strip())

                try:
                    date_idx = header_normalized.index("date")
                except ValueError:
                    date_idx = -1
                try:
                    close_idx = header_normalized.index("close")
                except ValueError:
                    close_idx = -1

                for record in reader:
                    if not record or all(not str(cell).strip() for cell in record):
                        continue
                    rows += 1

                    if close_idx >= 0 and close_idx < len(record):
                        close_text = str(record[close_idx]).strip()
                        if not close_text or close_text.lower() in ("nan", "null", "none"):
                            close_has_nan = True
                        else:
                            try:
                                close_value = float(close_text)
                                if math.isnan(close_value):
                                    close_has_nan = True
                            except ValueError:
                                close_has_nan = True

                    if date_idx >= 0 and date_idx < len(record):
                        date_text = str(record[date_idx]).strip()
                        parsed = cls._parse_iso_date(date_text)
                        if parsed is not None:
                            if min_date is None or parsed < min_date:
                                min_date = parsed
                            if max_date is None or parsed > max_date:
                                max_date = parsed
                            if reference is not None and parsed > reference:
                                has_future_data = True
        except OSError:
            return _CsvReadOutcome(0, (), None, None, False)

        # Emit ``close`` in columns_present only if it is both declared in the
        # header and free of NaN-like values. This lets the benchmark data
        # contract surface ``schema_mismatch`` for NaN-contaminated files
        # without the loader needing to raise.
        effective_columns: list[str] = []
        if "date" in header_normalized:
            effective_columns.append("date")
        if "close" in header_normalized and not close_has_nan:
            effective_columns.append("close")

        return _CsvReadOutcome(
            rows=rows,
            columns_present=tuple(effective_columns),
            snapshot_start=min_date.isoformat() if min_date is not None else None,
            snapshot_end=max_date.isoformat() if max_date is not None else None,
            has_future_data=has_future_data,
        )
