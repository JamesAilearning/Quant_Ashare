"""Taxonomy artifact loader (file IO -> contract-consumable profile).

Mirrors :mod:`src.data.universe_artifact_loader` with
``industry_code`` as the second base column instead of
``in_universe``. See the universe loader docstring for full
responsibilities and non-responsibilities.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional

from src.contracts.taxonomy_data_contract import (
    TAXONOMY_MODE_RANGE,
    TAXONOMY_MODE_STATIC,
    TAXONOMY_MODE_TRADE_DATE,
    TAXONOMY_SUPPORTED_TEMPORAL_MODES,
    TaxonomyArtifactProfile,
)
from src.data.trading_calendar import TradingCalendar


class TaxonomyArtifactLoaderError(ValueError):
    """Raised when the loader is called with structurally invalid arguments."""


@dataclass(frozen=True)
class _TaxonomyCsvReadOutcome:
    rows: int
    columns_present: tuple[str, ...]
    snapshot_start: Optional[str]
    snapshot_end: Optional[str]
    has_future_effective_data: bool
    max_trade_date: Optional[str]


class TaxonomyArtifactLoader:
    """Explicit path-based loader for taxonomy csv + manifest artifacts."""

    BASE_COLUMNS: tuple[str, ...] = ("instrument", "industry_code")
    TRADE_DATE_COLUMN: str = "trade_date"
    RANGE_COLUMNS: tuple[str, ...] = ("effective_start", "effective_end")

    @classmethod
    def load(
        cls,
        artifact_path: str,
        manifest_path: str,
        *,
        temporal_mode: str,
        reference_date: Optional[str] = None,
        calendar: Optional[TradingCalendar] = None,
    ) -> TaxonomyArtifactProfile:
        """Load an explicit artifact + manifest pair into a profile."""
        if not str(artifact_path or "").strip():
            raise TaxonomyArtifactLoaderError("artifact_path is required.")
        if not str(manifest_path or "").strip():
            raise TaxonomyArtifactLoaderError("manifest_path is required.")
        if temporal_mode not in TAXONOMY_SUPPORTED_TEMPORAL_MODES:
            raise TaxonomyArtifactLoaderError(
                f"Unsupported temporal_mode '{temporal_mode}'. "
                f"Allowed: {TAXONOMY_SUPPORTED_TEMPORAL_MODES}."
            )

        reference = cls._parse_iso_date(reference_date)

        artifact_file = Path(artifact_path)
        manifest_file = Path(manifest_path)
        artifact_present = artifact_file.is_file()
        manifest_present = manifest_file.is_file()

        metadata: Mapping[str, Any] = cls._read_manifest(manifest_file) if manifest_present else {}

        if artifact_present:
            outcome = cls._read_csv(artifact_file, temporal_mode, reference)
        else:
            outcome = _TaxonomyCsvReadOutcome(
                rows=0,
                columns_present=(),
                snapshot_start=None,
                snapshot_end=None,
                has_future_effective_data=False,
                max_trade_date=None,
            )

        stale_days: Optional[int] = None
        if reference is not None and outcome.snapshot_end is not None:
            end_date = cls._parse_iso_date(outcome.snapshot_end)
            if end_date is not None:
                stale_days = max((reference - end_date).days, 0)

        coverage_ratio: Optional[float] = None
        if (
            temporal_mode == TAXONOMY_MODE_TRADE_DATE
            and calendar is not None
            and outcome.rows > 0
            and outcome.snapshot_start is not None
            and outcome.snapshot_end is not None
        ):
            start_date = cls._parse_iso_date(outcome.snapshot_start)
            end_date = cls._parse_iso_date(outcome.snapshot_end)
            if start_date is not None and end_date is not None and end_date >= start_date:
                expected_rows = max(
                    calendar.count_trading_days(start_date, end_date), 1
                )
                distinct_trade_dates = cls._count_distinct_trade_dates(
                    artifact_file
                )
                coverage_ratio = min(distinct_trade_dates / expected_rows, 1.0)

        manifest_snapshot_at_text = str(metadata.get("snapshot_at", "")).strip()
        manifest_snapshot_at = cls._parse_iso_date(manifest_snapshot_at_text or None)

        has_future_known_metadata = False
        if reference is not None and manifest_snapshot_at is not None:
            if manifest_snapshot_at > reference:
                has_future_known_metadata = True

        has_snapshot_at_mismatch = False
        if (
            temporal_mode == TAXONOMY_MODE_TRADE_DATE
            and manifest_snapshot_at is not None
            and outcome.max_trade_date is not None
        ):
            artifact_max_date = cls._parse_iso_date(outcome.max_trade_date)
            if artifact_max_date is not None and manifest_snapshot_at != artifact_max_date:
                has_snapshot_at_mismatch = True

        return TaxonomyArtifactProfile(
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
            has_inconsistent_mappings=False,
            has_future_effective_data=outcome.has_future_effective_data,
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
    def _read_csv(
        cls,
        artifact_file: Path,
        temporal_mode: str,
        reference: Optional[date],
    ) -> _TaxonomyCsvReadOutcome:
        rows = 0
        has_future_effective_data = False
        header_normalized: tuple[str, ...] = ()
        min_trade_date: Optional[date] = None
        max_trade_date: Optional[date] = None
        min_effective_start: Optional[date] = None
        max_effective_end: Optional[date] = None

        try:
            with artifact_file.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                try:
                    header = next(reader)
                except StopIteration:
                    return _TaxonomyCsvReadOutcome(0, (), None, None, False, None)
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

                    if temporal_mode == TAXONOMY_MODE_TRADE_DATE and trade_date_idx >= 0 and trade_date_idx < len(record):
                        parsed = cls._parse_iso_date(str(record[trade_date_idx]).strip())
                        if parsed is not None:
                            if min_trade_date is None or parsed < min_trade_date:
                                min_trade_date = parsed
                            if max_trade_date is None or parsed > max_trade_date:
                                max_trade_date = parsed
                            if reference is not None and parsed > reference:
                                has_future_effective_data = True

                    if temporal_mode == TAXONOMY_MODE_RANGE:
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
        except OSError:
            return _TaxonomyCsvReadOutcome(0, (), None, None, False, None)

        recognised: list[str] = []
        for col in cls.BASE_COLUMNS:
            if col in header_normalized:
                recognised.append(col)
        if temporal_mode == TAXONOMY_MODE_TRADE_DATE:
            if cls.TRADE_DATE_COLUMN in header_normalized:
                recognised.append(cls.TRADE_DATE_COLUMN)
        elif temporal_mode == TAXONOMY_MODE_RANGE:
            for col in cls.RANGE_COLUMNS:
                if col in header_normalized:
                    recognised.append(col)

        if temporal_mode == TAXONOMY_MODE_TRADE_DATE:
            snapshot_start = min_trade_date.isoformat() if min_trade_date is not None else None
            snapshot_end = max_trade_date.isoformat() if max_trade_date is not None else None
        elif temporal_mode == TAXONOMY_MODE_RANGE:
            snapshot_start = min_effective_start.isoformat() if min_effective_start is not None else None
            snapshot_end = max_effective_end.isoformat() if max_effective_end is not None else None
        else:  # static
            snapshot_start = None
            snapshot_end = None

        return _TaxonomyCsvReadOutcome(
            rows=rows,
            columns_present=tuple(recognised),
            snapshot_start=snapshot_start,
            snapshot_end=snapshot_end,
            has_future_effective_data=has_future_effective_data,
            max_trade_date=max_trade_date.isoformat() if max_trade_date is not None else None,
        )

    @classmethod
    def _count_distinct_trade_dates(cls, artifact_file: Path) -> int:
        distinct: set[str] = set()
        try:
            with artifact_file.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                try:
                    header = next(reader)
                except StopIteration:
                    return 0
                header_normalized = tuple(
                    col.strip().lower() for col in header if col.strip()
                )
                try:
                    trade_date_idx = header_normalized.index(cls.TRADE_DATE_COLUMN)
                except ValueError:
                    return 0
                for record in reader:
                    if trade_date_idx < len(record):
                        text = str(record[trade_date_idx]).strip()
                        if text and cls._parse_iso_date(text) is not None:
                            distinct.add(text)
        except OSError:
            return 0
        return len(distinct)
