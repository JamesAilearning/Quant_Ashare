"""Export helpers for operator UI pipeline result detail pages."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from web.operator_ui._path_guard import guard_output_path


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


def bundle_zip_bytes(run_dir: Path) -> bytes:
    guard_output_path(run_dir)
    if not run_dir.is_dir():
        raise ValueError(f"Run directory does not exist: {run_dir}")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(run_dir.rglob("*")):
            if path.is_file():
                guard_output_path(path)
                zf.write(path, path.relative_to(run_dir).as_posix())
    return buffer.getvalue()


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
