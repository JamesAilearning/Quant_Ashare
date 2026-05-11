"""Universe artifact loader (file IO -> contract-consumable profile).

Responsibilities
----------------
- Read a universe artifact csv whose schema depends on ``temporal_mode``:

  * ``static``:    columns ``(instrument, in_universe)``
  * ``trade_date``: columns ``(instrument, in_universe, trade_date)``
  * ``range``:     columns ``(instrument, in_universe, effective_start,
    effective_end)``

- Read a sidecar manifest json.
- Produce a :class:`UniverseArtifactProfile` that the existing
  :class:`UniverseDataContract` can validate unchanged.

Non-responsibilities
--------------------
- This loader does NOT implement universe selection semantics.
- This loader does NOT compute membership-consistency invariants.
  ``has_inconsistent_membership`` is always ``False`` from the loader.

Data-level issues are surfaced via the contract status; they do NOT raise.
Only structural misuse (empty paths, unknown temporal_mode) raises.

Implementation note
-------------------
The CSV parsing logic lives in :mod:`src.data._temporal_artifact_loader_base`
and is shared with :mod:`src.data.taxonomy_artifact_loader`. The only
substantive differences are the second base column (``in_universe`` vs
``industry_code``) and the profile/error types.
"""

from __future__ import annotations

from src.contracts.universe_data_contract import (
    UNIVERSE_MODE_RANGE,
    UNIVERSE_MODE_TRADE_DATE,
    UNIVERSE_SUPPORTED_TEMPORAL_MODES,
    UniverseArtifactProfile,
)
from src.data._temporal_artifact_loader_base import TemporalArtifactLoaderBase
from src.data.trading_calendar import TradingCalendar


class UniverseArtifactLoaderError(ValueError):
    """Raised when the loader is called with structurally invalid arguments."""


class UniverseArtifactLoader(TemporalArtifactLoaderBase):
    """Explicit path-based loader for universe csv + manifest artifacts."""

    BASE_COLUMNS: tuple[str, ...] = ("instrument", "in_universe")
    TRADE_DATE_COLUMN: str = "trade_date"
    RANGE_COLUMNS: tuple[str, ...] = ("effective_start", "effective_end")
    MODE_TRADE_DATE: str = UNIVERSE_MODE_TRADE_DATE
    MODE_RANGE: str = UNIVERSE_MODE_RANGE
    SUPPORTED_MODES: tuple[str, ...] = UNIVERSE_SUPPORTED_TEMPORAL_MODES
    _ERROR_CLASS: type = UniverseArtifactLoaderError

    @classmethod
    def load(
        cls,
        artifact_path: str,
        manifest_path: str,
        *,
        temporal_mode: str,
        reference_date: str | None = None,
        calendar: TradingCalendar | None = None,
    ) -> UniverseArtifactProfile:
        """Load an explicit artifact + manifest pair into a profile.

        Parameters
        ----------
        artifact_path, manifest_path:
            Explicit file paths. Missing files are reported via profile
            flags, not exceptions.
        temporal_mode:
            One of ``static`` / ``trade_date`` / ``range``. Unknown
            values raise :class:`UniverseArtifactLoaderError`.
        reference_date:
            Optional ISO date. Drives ``stale_days``,
            ``has_future_effective_data``, and ``has_future_known_metadata``.
        calendar:
            Optional :class:`TradingCalendar`. Used only in
            ``trade_date`` mode to compute ``coverage_ratio``.
        """
        (
            outcome,
            metadata,
            artifact_present,
            manifest_present,
            stale_days,
            coverage_ratio,
        ) = cls._load_impl(artifact_path, manifest_path, temporal_mode, reference_date, calendar)

        reference = cls._parse_iso_date(reference_date)

        manifest_snapshot_at_text = str(metadata.get("snapshot_at", "")).strip()
        manifest_snapshot_at = cls._parse_iso_date(manifest_snapshot_at_text or None)

        has_future_known_metadata = (
            reference is not None
            and manifest_snapshot_at is not None
            and manifest_snapshot_at > reference
        )

        has_snapshot_at_mismatch = False
        if (
            temporal_mode == UNIVERSE_MODE_TRADE_DATE
            and manifest_snapshot_at is not None
            and outcome.max_trade_date is not None
        ):
            artifact_max_date = cls._parse_iso_date(outcome.max_trade_date)
            if artifact_max_date is not None and manifest_snapshot_at != artifact_max_date:
                has_snapshot_at_mismatch = True

        return UniverseArtifactProfile(
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
            has_inconsistent_membership=False,
            has_future_effective_data=outcome.has_future_effective_data,
            has_future_known_metadata=has_future_known_metadata,
            has_snapshot_at_mismatch=has_snapshot_at_mismatch,
        )
