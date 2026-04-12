"""Benchmark artifact publisher (qlib provider -> canonical csv + manifest).

Responsibilities
----------------
- Pull the `$close` series for a single caller-supplied ``benchmark_code`` from
  the pinned qlib provider over an explicit date range.
- Write a csv with header ``date,close`` that matches the shape
  :class:`BenchmarkArtifactLoader` already consumes.
- Write a sidecar manifest json with the required provenance metadata.
- Delegate final profile construction to :class:`BenchmarkArtifactLoader` so
  there is exactly one code path that interprets the artifact shape.

Non-responsibilities
--------------------
- This module does NOT call :func:`qlib.init`. Canonical qlib runtime
  initialization is a precondition and must happen via
  :func:`src.core.qlib_runtime.init_qlib_canonical` before any call to
  :meth:`BenchmarkArtifactPublisher.publish`.
- This module does NOT implement benchmark selection semantics. The
  ``benchmark_code`` argument is a caller-supplied label; there is no
  registry lookup, environment fallback, or default code.
- This module does NOT schedule, retry, or perform network IO.

Empty qlib query results are treated as a hard error: a silent empty csv
would look superficially healthy on disk and violate the V1 lesson
"avoid implicit fallback and silent bad output".
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional

from src.contracts.benchmark_data_contract import BenchmarkArtifactProfile
from src.core.qlib_runtime import is_canonical_qlib_initialized
from src.data.benchmark_artifact_loader import BenchmarkArtifactLoader
from src.data.trading_calendar import QlibTradingCalendar

BENCHMARK_PUBLISHER_SCHEMA_VERSION = "v1"
BENCHMARK_PUBLISHER_SOURCE_NAME_DEFAULT = "qlib-provider"


class BenchmarkArtifactPublisherError(RuntimeError):
    """Raised when the publisher cannot produce a valid benchmark artifact."""


@dataclass(frozen=True)
class BenchmarkPublishResult:
    """Summary returned by a successful publish call."""

    artifact_path: str
    manifest_path: str
    rows_written: int
    profile: BenchmarkArtifactProfile


class BenchmarkArtifactPublisher:
    """Canonical-shape benchmark artifact producer backed by the qlib provider."""

    EXPECTED_COLUMNS: tuple[str, ...] = ("date", "close")

    @classmethod
    def publish(
        cls,
        benchmark_code: str,
        start_time: str,
        end_time: str,
        artifact_path: str,
        manifest_path: str,
        *,
        source_name: str = BENCHMARK_PUBLISHER_SOURCE_NAME_DEFAULT,
        source_uri: Optional[str] = None,
        snapshot_at: Optional[str] = None,
        reference_date: Optional[str] = None,
    ) -> BenchmarkPublishResult:
        """Publish a benchmark artifact.

        Parameters
        ----------
        benchmark_code:
            Caller-supplied instrument code. The publisher does not resolve
            or validate this code; it is passed straight to the qlib provider.
        start_time, end_time:
            ISO date strings (inclusive). Forwarded to ``D.features``.
        artifact_path:
            Destination csv path.
        manifest_path:
            Destination manifest json path.
        source_name, source_uri, snapshot_at:
            Provenance fields written into the manifest.

            ``snapshot_at`` defaults to the **actual maximum row date**
            present in the published csv, NOT to ``end_time``. The
            request parameter ``end_time`` is an upper bound on the
            qlib query window; the actual max date is determined by
            what the qlib provider returned. If the caller explicitly
            supplies ``snapshot_at``, it MUST equal the actual max row
            date, otherwise the publisher raises
            :class:`BenchmarkArtifactPublisherError` at the boundary
            (rather than letting the loader's strict-equality check
            reject the artifact later, far from the cause).
        reference_date:
            Optional ISO date forwarded to the loader for stale/future checks
            on the round-trip profile.
        """
        cls._require_canonical_init()
        cls._require_non_empty_str(benchmark_code, "benchmark_code")
        cls._require_non_empty_str(start_time, "start_time")
        cls._require_non_empty_str(end_time, "end_time")
        cls._require_non_empty_str(artifact_path, "artifact_path")
        cls._require_non_empty_str(manifest_path, "manifest_path")

        # Strict ISO + ordering check BEFORE any qlib call. Garbage dates
        # forwarded to D.features surface as opaque qlib errors that
        # have nothing to do with "you passed a bad date string".
        start_d = cls._parse_iso_strict(start_time, "start_time")
        end_d = cls._parse_iso_strict(end_time, "end_time")
        if start_d > end_d:
            raise BenchmarkArtifactPublisherError(
                f"start_time '{start_time}' must be <= end_time '{end_time}'."
            )

        # Lazy import qlib.data here. Module-load time import would force
        # every test that touches src/data/* to depend on qlib being fully
        # initialized, which is not true for contract-only tests.
        try:
            from qlib.data import D  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise BenchmarkArtifactPublisherError(
                "qlib is not importable; cannot publish benchmark artifact."
            ) from exc

        raw = D.features(
            [benchmark_code],
            ["$close"],
            start_time=start_time,
            end_time=end_time,
        )

        rows = cls._flatten_close_frame(raw)
        if not rows:
            raise BenchmarkArtifactPublisherError(
                "qlib provider returned no rows for the supplied inputs: "
                f"benchmark_code={benchmark_code}, start_time={start_time}, end_time={end_time}."
            )

        # Derive snapshot_at from the actual data, not from the request
        # parameter end_time. end_time is an upper bound; the qlib
        # provider returns whatever trading days exist inside that
        # window, which is generally a subset. Treating end_time as the
        # snapshot date violates change-4's strict-equality invariant
        # whenever end_time falls on a non-trading day.
        actual_max_date = max(row[0] for row in rows)
        if snapshot_at is None:
            effective_snapshot_at = actual_max_date
        else:
            requested = str(snapshot_at).strip()
            if requested != actual_max_date:
                raise BenchmarkArtifactPublisherError(
                    "Explicit snapshot_at does not match the actual max "
                    f"row date in the published qlib data: snapshot_at='{requested}', "
                    f"actual_max_row_date='{actual_max_date}'. "
                    "snapshot_at must equal the real maximum row date so "
                    "downstream loaders can trust the manifest."
                )
            effective_snapshot_at = requested

        artifact_file = Path(artifact_path)
        manifest_file = Path(manifest_path)
        try:
            artifact_file.parent.mkdir(parents=True, exist_ok=True)
            manifest_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BenchmarkArtifactPublisherError(
                f"Cannot create output directories for artifact '{artifact_path}': {exc}"
            ) from exc

        cls._write_csv(artifact_file, rows)

        effective_source_uri = source_uri or f"qlib-provider://{benchmark_code}"
        manifest_payload: Mapping[str, Any] = {
            "benchmark_code": benchmark_code,
            "source_name": source_name,
            "source_uri": effective_source_uri,
            "snapshot_at": effective_snapshot_at,
            "schema_version": BENCHMARK_PUBLISHER_SCHEMA_VERSION,
        }
        cls._write_manifest(manifest_file, manifest_payload)

        # Inject a real trading calendar so the round-trip profile's
        # coverage_ratio uses the actual qlib calendar rather than the
        # 0.63 fallback. Publisher already hard-requires canonical qlib
        # init, so QlibTradingCalendar is a legitimate peer dependency.
        profile = BenchmarkArtifactLoader.load(
            artifact_path=str(artifact_file),
            manifest_path=str(manifest_file),
            reference_date=reference_date,
            calendar=QlibTradingCalendar(),
        )

        return BenchmarkPublishResult(
            artifact_path=str(artifact_file),
            manifest_path=str(manifest_file),
            rows_written=len(rows),
            profile=profile,
        )

    # -- Internal helpers --------------------------------------------------

    @staticmethod
    def _require_canonical_init() -> None:
        if not is_canonical_qlib_initialized():
            raise BenchmarkArtifactPublisherError(
                "Canonical qlib runtime is not initialized. "
                "Call src.core.qlib_runtime.init_qlib_canonical(...) before publishing."
            )

    @staticmethod
    def _require_non_empty_str(value: Any, field_name: str) -> None:
        if not str(value or "").strip():
            raise BenchmarkArtifactPublisherError(f"{field_name} is required.")

    @staticmethod
    def _parse_iso_strict(value: str, field_name: str) -> date:
        """Parse a strict ISO ``YYYY-MM-DD`` date or raise at the boundary."""
        try:
            return date.fromisoformat(str(value).strip())
        except ValueError as exc:
            raise BenchmarkArtifactPublisherError(
                f"{field_name} must be ISO date YYYY-MM-DD, got '{value}'."
            ) from exc

    @staticmethod
    def _flatten_close_frame(frame: Any) -> list[tuple[str, float]]:
        """Flatten a qlib MultiIndex (instrument, datetime) DataFrame.

        Returns a list of (iso_date, close_value) tuples sorted by date.
        Deliberately tolerant of shape differences across qlib versions:
        uses reset_index and column name fallback.
        """
        if frame is None:
            return []

        # Check emptiness. Only catch exceptions produced by the .empty
        # attribute itself (exotic pandas-like wrappers may raise TypeError
        # on property access); programmer errors must propagate.
        if hasattr(frame, "empty"):
            try:
                is_empty = bool(frame.empty)
            except TypeError:
                is_empty = True
            if is_empty:
                return []

        if not hasattr(frame, "reset_index"):
            # Input is not a DataFrame-like object.
            return []

        working = frame
        try:
            working = working.reset_index()
        except (TypeError, ValueError):
            # TypeError: reset_index argument shape mismatch in older pandas.
            # ValueError: index level conflict (duplicate level names).
            # AttributeError is intentionally NOT caught; it would mean
            # reset_index disappeared, which is a programmer error.
            return []

        # Identify datetime column.
        datetime_col = None
        for candidate in ("datetime", "date"):
            if candidate in working.columns:
                datetime_col = candidate
                break
        if datetime_col is None:
            return []

        # Identify close column. qlib returns "$close".
        close_col = None
        for candidate in ("$close", "close"):
            if candidate in working.columns:
                close_col = candidate
                break
        if close_col is None:
            return []

        rows: list[tuple[str, float]] = []
        for _, record in working.iterrows():
            raw_date = record[datetime_col]
            raw_close = record[close_col]
            try:
                iso_date = raw_date.strftime("%Y-%m-%d")
            except AttributeError:
                iso_date = str(raw_date)[:10]
            try:
                close_value = float(raw_close)
            except (TypeError, ValueError):
                continue
            if close_value != close_value:  # NaN filter
                continue
            rows.append((iso_date, close_value))

        rows.sort(key=lambda item: item[0])
        return rows

    @staticmethod
    def _write_csv(artifact_file: Path, rows: list[tuple[str, float]]) -> None:
        with artifact_file.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", "close"])
            for iso_date, close_value in rows:
                writer.writerow([iso_date, f"{close_value:.6f}"])

    @staticmethod
    def _write_manifest(manifest_file: Path, payload: Mapping[str, Any]) -> None:
        with manifest_file.open("w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
