"""Taxonomy artifact publisher (caller-supplied rows -> canonical csv + manifest).

Mirrors :mod:`src.data.universe_artifact_publisher` with
``industry_code`` as the second base column instead of
``in_universe``. See the universe publisher docstring for full
responsibilities and non-responsibilities.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from src.contracts.taxonomy_data_contract import (
    TAXONOMY_MODE_RANGE,
    TAXONOMY_MODE_STATIC,
    TAXONOMY_MODE_TRADE_DATE,
    TAXONOMY_SUPPORTED_TEMPORAL_MODES,
    TaxonomyArtifactProfile,
)
from src.data.taxonomy_artifact_loader import TaxonomyArtifactLoader
from src.data.trading_calendar import TradingCalendar

TAXONOMY_PUBLISHER_SCHEMA_VERSION = "v1"
TAXONOMY_PUBLISHER_SOURCE_NAME_DEFAULT = "explicit-rows"


class TaxonomyArtifactPublisherError(RuntimeError):
    """Raised when the publisher cannot produce a valid taxonomy artifact."""


@dataclass(frozen=True)
class TaxonomyPublishResult:
    """Summary returned by a successful publish call."""

    artifact_path: str
    manifest_path: str
    rows_written: int
    profile: TaxonomyArtifactProfile


class TaxonomyArtifactPublisher:
    """Canonical-shape taxonomy artifact producer backed by caller rows."""

    BASE_HEADER: tuple[str, ...] = ("instrument", "industry_code")
    TRADE_DATE_HEADER: tuple[str, ...] = BASE_HEADER + ("trade_date",)
    RANGE_HEADER: tuple[str, ...] = BASE_HEADER + ("effective_start", "effective_end")

    _EXPECTED_ARITY = {
        TAXONOMY_MODE_STATIC: 2,
        TAXONOMY_MODE_TRADE_DATE: 3,
        TAXONOMY_MODE_RANGE: 4,
    }

    @classmethod
    def publish(
        cls,
        taxonomy_name: str,
        temporal_mode: str,
        rows: Sequence[tuple],
        artifact_path: str,
        manifest_path: str,
        *,
        source_name: str = TAXONOMY_PUBLISHER_SOURCE_NAME_DEFAULT,
        source_uri: Optional[str] = None,
        snapshot_at: Optional[str] = None,
        reference_date: Optional[str] = None,
        calendar: Optional[TradingCalendar] = None,
    ) -> TaxonomyPublishResult:
        """Publish a taxonomy artifact from explicit row tuples."""
        cls._require_non_empty_str(taxonomy_name, "taxonomy_name")
        cls._require_non_empty_str(artifact_path, "artifact_path")
        cls._require_non_empty_str(manifest_path, "manifest_path")
        if temporal_mode not in TAXONOMY_SUPPORTED_TEMPORAL_MODES:
            raise TaxonomyArtifactPublisherError(
                f"Unsupported temporal_mode '{temporal_mode}'. "
                f"Allowed: {TAXONOMY_SUPPORTED_TEMPORAL_MODES}."
            )

        if not rows:
            raise TaxonomyArtifactPublisherError(
                "rows is empty; publisher refuses to emit an empty artifact "
                f"(taxonomy_name={taxonomy_name}, temporal_mode={temporal_mode})."
            )

        expected_arity = cls._EXPECTED_ARITY[temporal_mode]
        for idx, row in enumerate(rows):
            if not isinstance(row, tuple) or len(row) != expected_arity:
                raise TaxonomyArtifactPublisherError(
                    f"row {idx} for temporal_mode={temporal_mode} must be a "
                    f"{expected_arity}-tuple, got {row!r}."
                )

        max_trade_date: Optional[str] = None

        if temporal_mode == TAXONOMY_MODE_TRADE_DATE:
            parsed_trade_dates: list[date] = []
            for idx, row in enumerate(rows):
                _instrument, _industry_code, trade_date_text = row
                parsed = cls._parse_iso_strict(
                    trade_date_text, f"rows[{idx}].trade_date"
                )
                parsed_trade_dates.append(parsed)
            max_trade_date = max(parsed_trade_dates).isoformat()
        elif temporal_mode == TAXONOMY_MODE_RANGE:
            for idx, row in enumerate(rows):
                _instrument, _industry_code, eff_start_text, eff_end_text = row
                start_d = cls._parse_iso_strict(
                    eff_start_text, f"rows[{idx}].effective_start"
                )
                end_d = cls._parse_iso_strict(
                    eff_end_text, f"rows[{idx}].effective_end"
                )
                if start_d > end_d:
                    raise TaxonomyArtifactPublisherError(
                        f"rows[{idx}].effective_start '{eff_start_text}' must be "
                        f"<= effective_end '{eff_end_text}'."
                    )

        if temporal_mode == TAXONOMY_MODE_TRADE_DATE:
            if max_trade_date is None:
                raise TaxonomyArtifactPublisherError(
                    "Internal error: max_trade_date is None after validating non-empty rows. "
                    "This indicates a bug in the publisher's row-parsing logic."
                )
            if snapshot_at is None:
                effective_snapshot_at = max_trade_date
            else:
                requested = str(snapshot_at).strip()
                cls._parse_iso_strict(requested, "snapshot_at")
                if requested != max_trade_date:
                    raise TaxonomyArtifactPublisherError(
                        "Explicit snapshot_at does not match the actual max "
                        f"row trade_date: snapshot_at='{requested}', "
                        f"actual_max_trade_date='{max_trade_date}'."
                    )
                effective_snapshot_at = requested
        else:
            if snapshot_at is None or not str(snapshot_at).strip():
                raise TaxonomyArtifactPublisherError(
                    f"snapshot_at is required in temporal_mode={temporal_mode} "
                    "because there is no row date to derive it from."
                )
            cls._parse_iso_strict(str(snapshot_at).strip(), "snapshot_at")
            effective_snapshot_at = str(snapshot_at).strip()

        if temporal_mode == TAXONOMY_MODE_STATIC:
            cls._validate_unique_static_instruments(rows)

        artifact_file = Path(artifact_path)
        manifest_file = Path(manifest_path)
        try:
            artifact_file.parent.mkdir(parents=True, exist_ok=True)
            manifest_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise TaxonomyArtifactPublisherError(
                f"Cannot create output directories for artifact '{artifact_path}': {exc}"
            ) from exc

        cls._write_csv(artifact_file, temporal_mode, rows)

        effective_source_uri = source_uri or f"explicit-rows://{taxonomy_name}"
        manifest_payload: Mapping[str, Any] = {
            "taxonomy_name": taxonomy_name,
            "source_name": source_name,
            "source_uri": effective_source_uri,
            "snapshot_at": effective_snapshot_at,
            "schema_version": TAXONOMY_PUBLISHER_SCHEMA_VERSION,
            "temporal_mode": temporal_mode,
        }
        cls._write_manifest(manifest_file, manifest_payload)

        profile = TaxonomyArtifactLoader.load(
            artifact_path=str(artifact_file),
            manifest_path=str(manifest_file),
            temporal_mode=temporal_mode,
            reference_date=reference_date,
            calendar=calendar,
        )

        return TaxonomyPublishResult(
            artifact_path=str(artifact_file),
            manifest_path=str(manifest_file),
            rows_written=len(rows),
            profile=profile,
        )

    # -- Internal helpers --------------------------------------------------

    @staticmethod
    def _require_non_empty_str(value: Any, field_name: str) -> None:
        if not str(value or "").strip():
            raise TaxonomyArtifactPublisherError(f"{field_name} is required.")

    @staticmethod
    def _parse_iso_strict(value: Any, field_name: str) -> date:
        try:
            return date.fromisoformat(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise TaxonomyArtifactPublisherError(
                f"{field_name} must be ISO date YYYY-MM-DD, got '{value}'."
            ) from exc

    @staticmethod
    def _validate_unique_static_instruments(rows: Sequence[tuple]) -> None:
        seen: dict[str, str] = {}
        for idx, row in enumerate(rows):
            instrument = str(row[0]).strip()
            industry_code = str(row[1]).strip()
            if instrument in seen:
                raise TaxonomyArtifactPublisherError(
                    f"Duplicate instrument {instrument!r} in static taxonomy "
                    f"rows: first industry {seen[instrument]!r}, duplicate "
                    f"industry {industry_code!r} at row {idx}. Static taxonomy "
                    "artifacts must contain at most one row per instrument."
                )
            seen[instrument] = industry_code

    @classmethod
    def _write_csv(
        cls,
        artifact_file: Path,
        temporal_mode: str,
        rows: Sequence[tuple],
    ) -> None:
        if temporal_mode == TAXONOMY_MODE_STATIC:
            header: tuple[str, ...] = cls.BASE_HEADER
        elif temporal_mode == TAXONOMY_MODE_TRADE_DATE:
            header = cls.TRADE_DATE_HEADER
        else:
            header = cls.RANGE_HEADER

        with artifact_file.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(list(header))
            for row in rows:
                writer.writerow([str(cell) for cell in row])

    @staticmethod
    def _write_manifest(manifest_file: Path, payload: Mapping[str, Any]) -> None:
        with manifest_file.open("w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
