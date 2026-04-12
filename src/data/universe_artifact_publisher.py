"""Universe artifact publisher (caller-supplied rows -> canonical csv + manifest).

Responsibilities
----------------
- Take caller-supplied per-row tuples plus provenance metadata and
  write a canonical universe csv + sidecar manifest json consumable
  by :class:`UniverseArtifactLoader` and ultimately by
  :class:`UniverseDataContract`.
- Validate row arity, ISO date fields, and mode-specific
  ``snapshot_at`` rules BEFORE any file is written so partial
  artifacts are never left on disk.
- Delegate final profile construction to
  :class:`UniverseArtifactLoader`, keeping producer/consumer on one
  code path.

Non-responsibilities
--------------------
- This publisher does NOT query any data source. Unlike the benchmark
  publisher there is no single canonical qlib API for membership
  rosters, so the publisher accepts in-memory rows from the caller.
- This publisher does NOT implement universe-selection semantics. The
  ``universe_name`` argument is a caller-supplied label.
- This publisher does NOT call :func:`qlib.init`.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from src.contracts.universe_data_contract import (
    UNIVERSE_MODE_RANGE,
    UNIVERSE_MODE_STATIC,
    UNIVERSE_MODE_TRADE_DATE,
    UNIVERSE_SUPPORTED_TEMPORAL_MODES,
    UniverseArtifactProfile,
)
from src.data.trading_calendar import TradingCalendar
from src.data.universe_artifact_loader import UniverseArtifactLoader

UNIVERSE_PUBLISHER_SCHEMA_VERSION = "v1"
UNIVERSE_PUBLISHER_SOURCE_NAME_DEFAULT = "explicit-rows"


class UniverseArtifactPublisherError(RuntimeError):
    """Raised when the publisher cannot produce a valid universe artifact."""


@dataclass(frozen=True)
class UniversePublishResult:
    """Summary returned by a successful publish call."""

    artifact_path: str
    manifest_path: str
    rows_written: int
    profile: UniverseArtifactProfile


class UniverseArtifactPublisher:
    """Canonical-shape universe artifact producer backed by caller rows."""

    BASE_HEADER: tuple[str, ...] = ("instrument", "in_universe")
    TRADE_DATE_HEADER: tuple[str, ...] = BASE_HEADER + ("trade_date",)
    RANGE_HEADER: tuple[str, ...] = BASE_HEADER + ("effective_start", "effective_end")

    _EXPECTED_ARITY = {
        UNIVERSE_MODE_STATIC: 2,
        UNIVERSE_MODE_TRADE_DATE: 3,
        UNIVERSE_MODE_RANGE: 4,
    }

    @classmethod
    def publish(
        cls,
        universe_name: str,
        temporal_mode: str,
        rows: Sequence[tuple],
        artifact_path: str,
        manifest_path: str,
        *,
        source_name: str = UNIVERSE_PUBLISHER_SOURCE_NAME_DEFAULT,
        source_uri: Optional[str] = None,
        snapshot_at: Optional[str] = None,
        reference_date: Optional[str] = None,
        calendar: Optional[TradingCalendar] = None,
    ) -> UniversePublishResult:
        """Publish a universe artifact from explicit row tuples.

        Parameters
        ----------
        universe_name:
            Caller-supplied label. Not resolved via registry; written
            verbatim into the manifest.
        temporal_mode:
            One of ``static`` / ``trade_date`` / ``range``.
        rows:
            Sequence of per-row tuples. Arity must match
            ``temporal_mode`` -- 2 / 3 / 4 respectively. Any date
            fields must be strict ISO ``YYYY-MM-DD`` strings.
        artifact_path, manifest_path:
            Destination paths. Parent directories are created.
        source_name, source_uri, snapshot_at:
            Provenance fields. ``snapshot_at`` defaults to
            ``max(row.trade_date)`` in ``trade_date`` mode and is
            REQUIRED in ``static`` / ``range`` modes. Explicit values
            in ``trade_date`` mode must strictly equal the computed
            max.
        reference_date, calendar:
            Forwarded to the loader for the round-trip profile.
        """
        cls._require_non_empty_str(universe_name, "universe_name")
        cls._require_non_empty_str(artifact_path, "artifact_path")
        cls._require_non_empty_str(manifest_path, "manifest_path")
        if temporal_mode not in UNIVERSE_SUPPORTED_TEMPORAL_MODES:
            raise UniverseArtifactPublisherError(
                f"Unsupported temporal_mode '{temporal_mode}'. "
                f"Allowed: {UNIVERSE_SUPPORTED_TEMPORAL_MODES}."
            )

        if not rows:
            raise UniverseArtifactPublisherError(
                "rows is empty; publisher refuses to emit an empty artifact "
                f"(universe_name={universe_name}, temporal_mode={temporal_mode})."
            )

        # Validate arity + ISO date fields in one pass. No IO yet.
        expected_arity = cls._EXPECTED_ARITY[temporal_mode]
        for idx, row in enumerate(rows):
            if not isinstance(row, tuple) or len(row) != expected_arity:
                raise UniverseArtifactPublisherError(
                    f"row {idx} for temporal_mode={temporal_mode} must be a "
                    f"{expected_arity}-tuple, got {row!r}."
                )

        max_trade_date: Optional[str] = None

        if temporal_mode == UNIVERSE_MODE_TRADE_DATE:
            parsed_trade_dates: list[date] = []
            for idx, row in enumerate(rows):
                _instrument, _flag, trade_date_text = row
                parsed = cls._parse_iso_strict(
                    trade_date_text, f"rows[{idx}].trade_date"
                )
                parsed_trade_dates.append(parsed)
            max_trade_date = max(parsed_trade_dates).isoformat()
        elif temporal_mode == UNIVERSE_MODE_RANGE:
            for idx, row in enumerate(rows):
                _instrument, _flag, eff_start_text, eff_end_text = row
                start_d = cls._parse_iso_strict(
                    eff_start_text, f"rows[{idx}].effective_start"
                )
                end_d = cls._parse_iso_strict(
                    eff_end_text, f"rows[{idx}].effective_end"
                )
                if start_d > end_d:
                    raise UniverseArtifactPublisherError(
                        f"rows[{idx}].effective_start '{eff_start_text}' must be "
                        f"<= effective_end '{eff_end_text}'."
                    )

        # Resolve snapshot_at per mode.
        if temporal_mode == UNIVERSE_MODE_TRADE_DATE:
            if max_trade_date is None:
                raise UniverseArtifactPublisherError(
                    "Internal error: max_trade_date is None after validating non-empty rows. "
                    "This indicates a bug in the publisher's row-parsing logic."
                )
            if snapshot_at is None:
                effective_snapshot_at = max_trade_date
            else:
                requested = str(snapshot_at).strip()
                # Validate the caller's explicit value parses as ISO
                # before comparing, so a malformed explicit snapshot_at
                # surfaces as a clear parse error instead of a silent
                # string inequality.
                cls._parse_iso_strict(requested, "snapshot_at")
                if requested != max_trade_date:
                    raise UniverseArtifactPublisherError(
                        "Explicit snapshot_at does not match the actual max "
                        f"row trade_date: snapshot_at='{requested}', "
                        f"actual_max_trade_date='{max_trade_date}'."
                    )
                effective_snapshot_at = requested
        else:
            # static / range: snapshot_at is required from the caller.
            if snapshot_at is None or not str(snapshot_at).strip():
                raise UniverseArtifactPublisherError(
                    f"snapshot_at is required in temporal_mode={temporal_mode} "
                    "because there is no row date to derive it from."
                )
            cls._parse_iso_strict(str(snapshot_at).strip(), "snapshot_at")
            effective_snapshot_at = str(snapshot_at).strip()

        # All validation passed. Now do IO.
        artifact_file = Path(artifact_path)
        manifest_file = Path(manifest_path)
        try:
            artifact_file.parent.mkdir(parents=True, exist_ok=True)
            manifest_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise UniverseArtifactPublisherError(
                f"Cannot create output directories for artifact '{artifact_path}': {exc}"
            ) from exc

        cls._write_csv(artifact_file, temporal_mode, rows)

        effective_source_uri = source_uri or f"explicit-rows://{universe_name}"
        manifest_payload: Mapping[str, Any] = {
            "universe_name": universe_name,
            "source_name": source_name,
            "source_uri": effective_source_uri,
            "snapshot_at": effective_snapshot_at,
            "schema_version": UNIVERSE_PUBLISHER_SCHEMA_VERSION,
            "temporal_mode": temporal_mode,
        }
        cls._write_manifest(manifest_file, manifest_payload)

        profile = UniverseArtifactLoader.load(
            artifact_path=str(artifact_file),
            manifest_path=str(manifest_file),
            temporal_mode=temporal_mode,
            reference_date=reference_date,
            calendar=calendar,
        )

        return UniversePublishResult(
            artifact_path=str(artifact_file),
            manifest_path=str(manifest_file),
            rows_written=len(rows),
            profile=profile,
        )

    # -- Internal helpers --------------------------------------------------

    @staticmethod
    def _require_non_empty_str(value: Any, field_name: str) -> None:
        if not str(value or "").strip():
            raise UniverseArtifactPublisherError(f"{field_name} is required.")

    @staticmethod
    def _parse_iso_strict(value: Any, field_name: str) -> date:
        """Parse a strict ISO ``YYYY-MM-DD`` date or raise at the boundary."""
        try:
            return date.fromisoformat(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise UniverseArtifactPublisherError(
                f"{field_name} must be ISO date YYYY-MM-DD, got '{value}'."
            ) from exc

    @classmethod
    def _write_csv(
        cls,
        artifact_file: Path,
        temporal_mode: str,
        rows: Sequence[tuple],
    ) -> None:
        if temporal_mode == UNIVERSE_MODE_STATIC:
            header: tuple[str, ...] = cls.BASE_HEADER
        elif temporal_mode == UNIVERSE_MODE_TRADE_DATE:
            header = cls.TRADE_DATE_HEADER
        else:
            header = cls.RANGE_HEADER

        with artifact_file.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(list(header))
            for row in rows:
                serialised = [cls._serialise_cell(cell) for cell in row]
                writer.writerow(serialised)

    @staticmethod
    def _serialise_cell(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    @staticmethod
    def _write_manifest(manifest_file: Path, payload: Mapping[str, Any]) -> None:
        with manifest_file.open("w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
