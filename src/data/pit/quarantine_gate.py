"""Verify the one explicit raw-data exception without changing completeness."""

from pathlib import Path

from src.contracts.suspension_quarantine import (
    SuspensionQuarantine,
    has_quarantine,
    qualified_quarantine,
    validate_policy,
)
from src.data.tushare.fetch_manifest import FetchManifest, all_holes, covered_endpoints
from src.data.tushare.suspension_quarantine import verify_quarantine_evidence


def validate_scoped_quarantine(
    manifest: FetchManifest | None,
    policy: str | None,
    raw_dir: Path,
    *,
    required_endpoints: tuple[str, ...],
) -> SuspensionQuarantine | None:
    """Return verified incident evidence, or refuse an unqualified exception.

    Ordinary data without this policy retains the existing caller's gate. A
    policy selected for a clean, repaired manifest is a no-op, not an override.
    """
    validate_policy(policy)
    if manifest is None:
        if policy is not None:
            raise ValueError("suspension quarantine cannot authorize a missing manifest")
        return None
    holes = all_holes(manifest)
    if not has_quarantine(holes):
        if policy is not None and holes:
            raise ValueError("suspension quarantine cannot authorize other fetch holes")
        return None
    evidence = qualified_quarantine(holes, policy)
    if evidence is None:
        raise ValueError("suspension quarantine requires its explicit policy and no other holes")
    if set(required_endpoints) - covered_endpoints(manifest):
        raise ValueError("suspension quarantine cannot authorize missing core coverage")
    coverage = manifest.endpoints.get("suspend_d")
    if coverage is None or coverage.status != "holes" or (
        coverage.coverage_start_date != evidence.query_start_date
        or coverage.coverage_end_date != evidence.query_end_date
    ):
        raise ValueError("suspension quarantine evidence does not match manifest coverage")
    verify_quarantine_evidence(raw_dir, evidence)
    return evidence
