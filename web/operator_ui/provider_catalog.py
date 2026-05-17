"""Read-only catalog of UI-managed qlib provider bundles."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from web.operator_ui.job_manager import PROJECT_ROOT
from web.operator_ui.training_guards import ProviderMetadata, inspect_provider_metadata

RESULT_ROOT = PROJECT_ROOT / "output" / "operator_ui" / "results"


class ProviderCatalogError(RuntimeError):
    """Raised when a provider catalog mutation is unsafe or impossible."""


@dataclass(frozen=True)
class ProviderCatalogEntry:
    job_id: str
    provider_path: Path
    metadata: ProviderMetadata

    @property
    def provider_uri(self) -> str:
        return str(self.provider_path)

    @property
    def label(self) -> str:
        coverage = _format_coverage(self.metadata)
        health = self.metadata.health or "health unavailable"
        universes = ", ".join(self.metadata.instrument_universes) or "universes unavailable"
        return f"{self.job_id} | {coverage} | {health} | {universes}"


def list_provider_catalog_entries(
    result_root: Path | None = None,
) -> list[ProviderCatalogEntry]:
    """List reusable providers created by UI-managed Tushare ingest jobs."""
    root = result_root or RESULT_ROOT
    if not root.is_dir():
        return []

    entries: list[ProviderCatalogEntry] = []
    for result_dir in root.iterdir():
        if not result_dir.is_dir():
            continue
        provider_path = result_dir / "qlib_provider"
        if not provider_path.is_dir():
            continue
        entries.append(
            ProviderCatalogEntry(
                job_id=result_dir.name,
                provider_path=provider_path.resolve(),
                metadata=inspect_provider_metadata(str(provider_path)),
            )
        )

    return sorted(entries, key=lambda entry: entry.job_id, reverse=True)


def delete_provider_catalog_entry(
    job_id: str,
    result_root: Path | None = None,
) -> None:
    """Delete a UI-managed provider result directory."""
    root = result_root or RESULT_ROOT
    result_dir = _resolve_child_dir(root, job_id)
    provider_dir = result_dir / "qlib_provider"
    if not provider_dir.is_dir():
        raise ProviderCatalogError(
            f"Cannot delete saved provider {job_id!r}: qlib_provider directory not found."
        )
    shutil.rmtree(result_dir)


def _format_coverage(metadata: ProviderMetadata) -> str:
    if metadata.coverage_start_date and metadata.coverage_end_date:
        return f"{metadata.coverage_start_date} to {metadata.coverage_end_date}"
    return "coverage unavailable"


def _resolve_child_dir(root: Path, child_name: str) -> Path:
    name = str(child_name or "").strip()
    if not name or Path(name).name != name:
        raise ProviderCatalogError(f"Invalid provider result id: {child_name!r}.")
    resolved_root = root.resolve()
    resolved_path = (root / name).resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ProviderCatalogError(
            f"Refusing to delete path outside provider result root: {resolved_path}"
        ) from exc
    return resolved_path
