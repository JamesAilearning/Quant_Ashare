"""Shared industry-taxonomy loader for attribution pipelines.

Why this module exists
----------------------
``Pipeline._build_attribution_config`` (single-fold) and
``WalkForwardEngine._run_single_fold`` (rolling) both want the same
behaviour:

1. Reject partial config (artifact path set but manifest missing,
   etc.) before doing any IO.
2. Load the artifact + manifest through ``TaxonomyArtifactLoader`` so
   contract checks (snapshot dates, staleness, schema) run.
3. Run ``TaxonomyDataContract.validate_and_build_status`` and refuse
   to proceed on any error; surface warnings via the supplied logger.
4. Match the manifest's ``taxonomy_name`` against the config-declared
   ``taxonomy_id`` so a typo cannot silently feed the wrong taxonomy
   into attribution.
5. Read the artifact CSV into a ``{instrument: industry}`` dict via
   :func:`load_industry_map`.

Without this shared helper the walk-forward engine would either
re-implement those four steps (drift) or skip them (silent
mis-classification). Co-locating the logic here keeps the contract
checks consistent across both call sites and makes the wiring
unit-testable.

Public surface
--------------
- :class:`IndustryTaxonomyLoadError` — raised on any failure during
  load + validation. Callers catch it and re-raise as their own
  domain-specific error (``PipelineError`` /
  ``WalkForwardError``) without losing the underlying message.
- :class:`IndustryTaxonomyResolution` — frozen result tuple carrying
  the loaded ``industry_map``, the resolved ``taxonomy_id``, and any
  contract warnings the caller should log.
- :func:`assert_industry_config_complete_or_empty` — boundary check
  used at config ``__post_init__`` time. Caller-supplied error class
  so ``PipelineError`` and ``WalkForwardError`` stay separate at the
  validation layer.
- :func:`resolve_industry_taxonomy` — happy path. Returns the
  resolution tuple or raises :class:`IndustryTaxonomyLoadError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.contracts.taxonomy_data_contract import (
    TAXONOMY_MODE_STATIC,
    TaxonomyContractInput,
    TaxonomyDataContract,
    TaxonomyDataContractError,
)
from src.data.industry_map_loader import IndustryMapLoaderError, load_industry_map
from src.data.taxonomy_artifact_loader import (
    TaxonomyArtifactLoader,
    TaxonomyArtifactLoaderError,
)


class IndustryTaxonomyLoadError(RuntimeError):
    """Raised on any failure resolving the industry taxonomy artifact.

    Wraps both :class:`TaxonomyArtifactLoaderError`,
    :class:`TaxonomyDataContractError`, and
    :class:`IndustryMapLoaderError` so callers (Pipeline, walk-forward)
    can ``except`` a single type and re-raise it under their own
    domain error.
    """


@dataclass(frozen=True)
class IndustryTaxonomyResolution:
    """Successful load of an industry taxonomy artifact.

    Attributes
    ----------
    industry_map
        ``{instrument: industry_name}`` mapping ready to feed into
        ``AttributionConfig.industry_map_override``.
    taxonomy_id
        Stable taxonomy id, validated to match the manifest's
        ``taxonomy_name``. Stamped onto the attribution result so
        downstream consumers can tell taxonomies apart.
    warnings
        Non-fatal contract warnings (stale snapshot, future-effective
        rows, etc.). Caller should log these so a soft drift surfaces
        in run output rather than going invisible.
    """

    industry_map: dict[str, str]
    taxonomy_id: str
    warnings: list[str]


def assert_industry_config_complete_or_empty(
    artifact_path: Optional[str],
    manifest_path: Optional[str],
    taxonomy_id: Optional[str],
    temporal_mode: Optional[str],
    *,
    error_class: type,
    error_prefix: str,
) -> None:
    """Validate the four industry-taxonomy fields are *all* set or
    *all* empty — partial config is rejected.

    Why this is the boundary contract
    ---------------------------------
    A user who set ``industry_artifact_path`` but forgot
    ``industry_manifest_path`` would otherwise hit the load step and
    get a confusing "No such file" deep in the loader. Catching the
    partial state at config construction surfaces the real mistake
    at the boundary.

    The caller supplies the exception class (``PipelineError`` /
    ``WalkForwardError``) so the validation message lands as the
    domain error type the rest of that subsystem expects.

    ``error_prefix`` is woven into the message so the operator sees
    "PipelineConfig industry attribution taxonomy ..." vs
    "WalkForwardConfig industry attribution taxonomy ...". Without
    that the error would say "config" generically and the operator
    would have to grep to find which dataclass tripped it.
    """
    bits = (
        bool(str(artifact_path or "").strip()),
        bool(str(manifest_path or "").strip()),
        bool(str(taxonomy_id or "").strip()),
    )
    if any(bits) and not all(bits):
        raise error_class(
            f"{error_prefix} industry attribution taxonomy must be configured "
            "as an explicit triple: industry_artifact_path, "
            "industry_manifest_path, and industry_taxonomy_id must be set "
            "together. Leave all three empty to use the board heuristic."
        )

    if str(temporal_mode or "").strip() != TAXONOMY_MODE_STATIC:
        raise error_class(
            f"{error_prefix}.industry_temporal_mode currently supports only "
            f"{TAXONOMY_MODE_STATIC!r} for attribution maps; got "
            f"{temporal_mode!r}."
        )


def resolve_industry_taxonomy(
    *,
    artifact_path: str,
    manifest_path: str,
    taxonomy_id: str,
    temporal_mode: str,
    reference_date: Optional[str] = None,
) -> IndustryTaxonomyResolution:
    """Load and validate an industry taxonomy artifact.

    Pipeline:

    1. ``TaxonomyArtifactLoader.load`` — file presence + schema +
       staleness checks.
    2. ``TaxonomyDataContract.validate_and_build_status`` — contract
       errors / warnings.
    3. Manifest's ``taxonomy_name`` vs caller's ``taxonomy_id``.
    4. :func:`load_industry_map` — the actual CSV → dict read.

    Any failure in steps 1-4 raises :class:`IndustryTaxonomyLoadError`
    with a message that names which step failed and why.

    ``reference_date`` is forwarded to the loader so staleness flags
    are computed against the run's evaluation period. Pass the
    backtest's ``end`` date for a single-fold run, or each fold's
    ``test_end`` for walk-forward.
    """
    try:
        profile = TaxonomyArtifactLoader.load(
            artifact_path=artifact_path,
            manifest_path=manifest_path,
            temporal_mode=temporal_mode,
            reference_date=reference_date,
        )
        status = TaxonomyDataContract.validate_and_build_status(
            TaxonomyContractInput(
                taxonomy_name=taxonomy_id,
                temporal_mode=temporal_mode,
                profile=profile,
                reference_date=reference_date,
            )
        )
    except (TaxonomyArtifactLoaderError, TaxonomyDataContractError) as exc:
        raise IndustryTaxonomyLoadError(
            f"Industry taxonomy contract validation failed: {exc}"
        ) from exc

    if status.errors:
        raise IndustryTaxonomyLoadError(
            "Industry taxonomy contract validation failed with errors: "
            f"{list(status.errors)}. Refusing to use artifact "
            f"{artifact_path!r} for attribution."
        )

    manifest_taxonomy = str(profile.metadata.get("taxonomy_name", "")).strip()
    if manifest_taxonomy != taxonomy_id:
        raise IndustryTaxonomyLoadError(
            "Industry taxonomy manifest taxonomy_name does not match the "
            "config-declared taxonomy_id: "
            f"manifest={manifest_taxonomy!r}, config={taxonomy_id!r}."
        )

    try:
        industry_map = load_industry_map(artifact_path)
    except IndustryMapLoaderError as exc:
        raise IndustryTaxonomyLoadError(
            f"Industry taxonomy map loading failed: {exc}"
        ) from exc

    return IndustryTaxonomyResolution(
        industry_map=industry_map,
        taxonomy_id=taxonomy_id,
        warnings=list(status.warnings),
    )
