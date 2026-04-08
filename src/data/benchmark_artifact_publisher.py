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
from pathlib import Path
from typing import Any, Mapping, Optional

from src.contracts.benchmark_data_contract import BenchmarkArtifactProfile
from src.core.qlib_runtime import is_canonical_qlib_initialized
from src.data.benchmark_artifact_loader import BenchmarkArtifactLoader

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
            Provenance fields written into the manifest. ``snapshot_at``
            defaults to ``end_time`` for determinism.
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

        artifact_file = Path(artifact_path)
        manifest_file = Path(manifest_path)
        artifact_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_file.parent.mkdir(parents=True, exist_ok=True)

        cls._write_csv(artifact_file, rows)

        effective_snapshot_at = snapshot_at or end_time
        effective_source_uri = source_uri or f"qlib-provider://{benchmark_code}"
        manifest_payload: Mapping[str, Any] = {
            "benchmark_code": benchmark_code,
            "source_name": source_name,
            "source_uri": effective_source_uri,
            "snapshot_at": effective_snapshot_at,
            "schema_version": BENCHMARK_PUBLISHER_SCHEMA_VERSION,
        }
        cls._write_manifest(manifest_file, manifest_payload)

        profile = BenchmarkArtifactLoader.load(
            artifact_path=str(artifact_file),
            manifest_path=str(manifest_file),
            reference_date=reference_date,
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
    def _flatten_close_frame(frame: Any) -> list[tuple[str, float]]:
        """Flatten a qlib MultiIndex (instrument, datetime) DataFrame.

        Returns a list of (iso_date, close_value) tuples sorted by date.
        Deliberately tolerant of shape differences across qlib versions:
        uses reset_index and column name fallback.
        """
        if frame is None:
            return []
        try:
            if hasattr(frame, "empty") and frame.empty:
                return []
        except Exception:
            return []

        working = frame
        try:
            working = working.reset_index()
        except Exception:
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
