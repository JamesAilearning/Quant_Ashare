"""Export helpers for operator UI pipeline result detail pages."""

from __future__ import annotations

import csv
import io
import json
import tempfile
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from web.operator_ui._path_guard import guard_output_path

# Hard cap for in-process bundle building. A complete pipeline run can carry
# ``model.pkl`` (100s of MiB), ``predictions.parquet``, per-fold logs and
# generated charts; a full run is routinely 1-5 GiB. Building such a zip in
# the Streamlit server would OOM the process and take down every concurrent
# session. Above this limit we surface a ``BundleTooLargeError`` so the UI
# can tell the operator to package the directory from the filesystem.
DEFAULT_BUNDLE_SIZE_LIMIT_BYTES: int = 500 * 1024 * 1024


class BundleTooLargeError(ValueError):
    """Raised when a run directory exceeds the in-process zip size budget.

    Subclass of ``ValueError`` so existing call sites that already do
    ``except (OSError, ValueError)`` keep their fallback behaviour; new
    code that wants a tailored message can ``except BundleTooLargeError``
    first to read ``size_bytes`` / ``limit_bytes`` / ``run_dir``.
    """

    def __init__(
        self,
        *,
        size_bytes: int,
        limit_bytes: int,
        run_dir: Path,
    ) -> None:
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes
        self.run_dir = run_dir
        size_mib = size_bytes / (1024 * 1024)
        limit_mib = limit_bytes / (1024 * 1024)
        super().__init__(
            f"Run directory {run_dir} totals {size_mib:.0f} MiB, exceeding the "
            f"UI bundle limit of {limit_mib:.0f} MiB. Package this run from "
            f"the filesystem instead."
        )


def flatten_mapping(payload: Mapping[str, Any], *, prefix: str = "") -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for key, value in payload.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            rows.update(flatten_mapping(value, prefix=name))
        elif isinstance(value, list):
            rows[name] = json.dumps(value, ensure_ascii=False, default=str)
        else:
            rows[name] = value
    return rows


def metrics_csv_bytes(metrics: Mapping[str, Any]) -> bytes:
    flattened = flatten_mapping(metrics)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["metric", "value"])
    for key in sorted(flattened):
        writer.writerow([key, flattened[key]])
    return buffer.getvalue().encode("utf-8-sig")


def _sum_run_dir_bytes(run_dir: Path) -> int:
    """Sum the size of every regular file under ``run_dir``.

    Files we cannot stat (permission errors, dangling symlinks) are skipped;
    the size is a budget gate, not exact accounting.
    """

    total = 0
    for path in run_dir.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def bundle_zip_bytes(
    run_dir: Path,
    *,
    size_limit_bytes: int = DEFAULT_BUNDLE_SIZE_LIMIT_BYTES,
) -> bytes:
    """Build a download bundle for ``run_dir``.

    The zip is written to a temp file rather than an in-memory ``BytesIO``
    so the Streamlit server RSS does not double-buffer the payload (the
    old ``BytesIO`` path held the full zip plus the ``getvalue()`` copy at
    the high-water mark). Returns the final bytes — Streamlit's
    ``download_button`` requires bytes.

    Raises :class:`BundleTooLargeError` when the source directory exceeds
    ``size_limit_bytes`` (default 500 MiB) so the caller can render a
    "package from the filesystem" hint instead of OOM-ing the server.
    """

    guard_output_path(run_dir)
    if not run_dir.is_dir():
        raise ValueError(f"Run directory does not exist: {run_dir}")

    source_size = _sum_run_dir_bytes(run_dir)
    if source_size > size_limit_bytes:
        raise BundleTooLargeError(
            size_bytes=source_size,
            limit_bytes=size_limit_bytes,
            run_dir=run_dir,
        )

    # ``delete=False`` plus explicit ``unlink`` in ``finally``: on Windows
    # the tempfile cannot be reopened for ``read_bytes`` while still held by
    # ``NamedTemporaryFile``'s context manager (file locks differ from POSIX).
    tmp = tempfile.NamedTemporaryFile(
        prefix="qv2_bundle_", suffix=".zip", delete=False
    )
    tmp.close()
    tmp_path = Path(tmp.name)
    try:
        with zipfile.ZipFile(tmp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(run_dir.rglob("*")):
                if path.is_file():
                    guard_output_path(path)
                    zf.write(path, path.relative_to(run_dir).as_posix())
        return tmp_path.read_bytes()
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def summary_pdf_bytes(
    *,
    run_id: str,
    status: str,
    metrics: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> bytes:
    """Create a small PDF summary for download.

    The PDF is intentionally a presentation/export layer. It copies values from
    existing artifacts and never computes replacement official metrics.
    """

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError as exc:  # pragma: no cover - exercised by UI fallback.
        raise RuntimeError(
            "reportlab is not installed. Install the UI extra to enable PDF export."
        ) from exc

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 48
    pdf.setTitle(f"Pipeline Result {run_id}")
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(48, y, "Pipeline Result Summary")
    y -= 28
    pdf.setFont("Helvetica", 10)
    for label, value in (
        ("Run ID", run_id),
        ("Status", status),
        ("Started", metadata.get("started_at")),
        ("Finished", metadata.get("finished_at")),
        ("Duration seconds", metadata.get("duration_seconds")),
    ):
        pdf.drawString(48, y, f"{label}: {value if value not in (None, '') else 'N/A'}")
        y -= 16

    y -= 10
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(48, y, "Metrics")
    y -= 18
    pdf.setFont("Helvetica", 9)
    for key, value in sorted(flatten_mapping(metrics).items()):
        if y < 48:
            pdf.showPage()
            y = height - 48
            pdf.setFont("Helvetica", 9)
        pdf.drawString(48, y, str(key)[:70])
        pdf.drawRightString(width - 48, y, str(value)[:60])
        y -= 13
    pdf.save()
    return buffer.getvalue()
