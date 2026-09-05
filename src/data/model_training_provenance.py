"""Factual model/run evidence shared by bootstrap and ensemble serving.

This binds bytes and training dates, not registered-family or source policy.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Any


class ModelTrainingProvenanceError(RuntimeError):
    """The declared training facts cannot be bound to producer evidence."""


# The producer layout: ``Pipeline.run`` writes the model to
# ``<run_dir>/artifacts/model.pkl`` and the RESOLVED config to
# ``<run_dir>/config.yaml``.
_RUN_ARTIFACTS_DIRNAME = "artifacts"
_RUN_CONFIG_NAME = "config.yaml"


def read_member_run_config(pkl_path: Path) -> tuple[Any, str] | None:
    """The PRODUCER's resolved config for this member's training run,
    with the sha256 of its exact bytes.

    Resolved from the exact known layout, never by searching upward
    (codex #392 r9): an upward search hits
    ``<run>/artifacts/config.yaml`` first, so a stale or hand-copied
    file there would be validated INSTEAD of the run config the
    pipeline actually wrote — letting a same-date but retuned member
    through. A model outside the layout, a missing run config, or a
    second config sitting in the artifacts dir all return ``None``,
    which the caller turns into a refusal (an unbindable member cannot
    become production).

    Single read (codex #392 r14): the parsed config and the digest
    the provenance check binds come from the SAME bytes."""
    import yaml

    artifacts_dir = pkl_path.parent
    if artifacts_dir.name != _RUN_ARTIFACTS_DIRNAME:
        return None
    try:
        if (artifacts_dir / _RUN_CONFIG_NAME).exists():
            # Ambiguous evidence: only the run root may carry the config.
            return None
        candidate = artifacts_dir.parent / _RUN_CONFIG_NAME
        if not candidate.is_file():
            return None
        raw = candidate.read_bytes()
        return (yaml.safe_load(raw.decode("utf-8")),
                hashlib.sha256(raw).hexdigest())
    except (OSError, yaml.YAMLError, ValueError):
        # ValueError also covers UTF-8 decoding and PyYAML timestamp
        # construction (e.g. February 30), neither is usable evidence.
        return None  # fallback-ok: unbindable evidence; callers refuse, never default.


def check_run_config_provenance(
    label: str, *, run_config_sha256: str, sidecar: Any,
) -> None:
    """Bind the run config the semantic gate reads to the gated chain.

    ``<run>/config.yaml`` is a mutable, uncommitted file (codex #392
    r14): an operator could train with retuned settings and edit the
    YAML back to the pre-registered values afterwards. The run's own
    result serializer therefore stamps the persisted config's digest
    into the trainer sidecar — which IS digest-bound end-to-end
    (manifest ``meta_sha256`` → member gate → serving loader). This
    check closes the loop: the config bytes on disk must be exactly
    the ones the run persisted, or the member cannot be promoted."""
    if not isinstance(sidecar, dict):
        raise ModelTrainingProvenanceError(
            f"{label}: trainer sidecar is not an object — cannot bind "
            "the run config to the gated chain, refusing")
    declared = sidecar.get("run_config_sha256")
    if (not isinstance(declared, str) or len(declared) != 64
            or any(c not in "0123456789abcdef" for c in declared)):
        raise ModelTrainingProvenanceError(
            f"{label}: trainer sidecar carries no run_config_sha256 — "
            "the run predates the config-binding serializer or was not "
            "produced by the pipeline; its run config cannot be "
            "trusted, refusing")
    if declared != run_config_sha256:
        raise ModelTrainingProvenanceError(
            f"{label}: the run config on disk (sha256 "
            f"{run_config_sha256}) is NOT the config the run persisted "
            f"({declared}) — post-training edits to config.yaml do not "
            "re-authorize a member, refusing")


def check_member_training_window(
    label: str, run_config: Any, *, fit_start: str, fit_end: str,
) -> None:
    """Require exact producer/manifest date agreement, not just equal spans."""
    if not isinstance(run_config, dict):
        raise ModelTrainingProvenanceError(
            f"{label}: training run config is not an object — refusing")
    for key, expected in (("train_start", fit_start), ("train_end", fit_end)):
        actual = run_config.get(key)
        try:
            if not isinstance(actual, str) or date.fromisoformat(actual).isoformat() != actual:
                raise ValueError("not a canonical ISO date")
        except ValueError as exc:
            raise ModelTrainingProvenanceError(
                f"{label}: training config {key}={actual!r} is not a "
                "YYYY-MM-DD date — refusing") from exc
        if actual != expected:
            raise ModelTrainingProvenanceError(
                f"{label}: training config {key}={actual!r} != manifest "
                f"fit boundary {expected!r} — declared dates do not describe "
                "the bound training run, refusing")


def check_member_gate_provenance(
    label: str, *, pkl_path: Path, sidecar: Any, pkl_sha256: str,
    fit_start: str, fit_end: str, valid_start: str, valid_end: str,
) -> None:
    """Bind gate dates to a caller-bound pickle/sidecar/config chain.

    The runner supplies its actual pickle digest and single-read sidecar.
    Rotation verifies the sidecar against the staged manifest before calling,
    and retains the strict loader's subsequent actual-pickle verification.
    This proves facts, not registered-family or source policy.
    """
    if not isinstance(sidecar, dict) or sidecar.get("pkl_sha256") != pkl_sha256:
        raise ModelTrainingProvenanceError(
            f"{label}: trainer sidecar pkl_sha256 does not match the "
            "bound member pickle — refusing")
    resolved = read_member_run_config(pkl_path)
    if resolved is None:
        raise ModelTrainingProvenanceError(
            f"{label}: training run config is missing, unreadable or "
            "outside the unambiguous producer layout — refusing")
    run_config, digest = resolved
    check_run_config_provenance(label, run_config_sha256=digest, sidecar=sidecar)
    check_member_training_window(label, run_config, fit_start=fit_start, fit_end=fit_end)
    for key, expected in (("valid_start", valid_start), ("valid_end", valid_end)):
        actual = run_config.get(key)
        try:
            if not isinstance(actual, str) or date.fromisoformat(actual).isoformat() != actual:
                raise ValueError("not a canonical ISO date")
        except ValueError as exc:
            raise ModelTrainingProvenanceError(
                f"{label}: training config {key}={actual!r} is not a "
                "YYYY-MM-DD date — refusing") from exc
        if actual != expected:
            raise ModelTrainingProvenanceError(
                f"{label}: training config {key}={actual!r} != gate "
                f"validation boundary {expected!r} — this is not the "
                "member's own validation window, refusing")
