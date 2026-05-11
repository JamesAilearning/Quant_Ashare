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

The ``purpose`` enum
--------------------
:func:`resolve_industry_taxonomy` accepts a required ``purpose``
parameter (:data:`PURPOSE_TRAINING` or :data:`PURPOSE_ATTRIBUTION`)
that decides whether the contract's
``has_future_known_metadata``-style temporal-leakage checks fire.
Walk-forward attribution intentionally uses a "today" snapshot to
bucket historical positions for *post-hoc analysis*; that is not a
lookahead-bias risk because it never feeds back into trading
signals. Training, on the other hand, must reject future-dated
metadata because lookahead-bias *is* the dominant failure mode there.

Putting the choice in the function signature (rather than letting
callers pass ``reference_date=None`` to silently bypass the check)
means a future caller that wants leakage protection cannot get it
"by accident": they must explicitly opt out via ``purpose`` and the
review notes will catch it.

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
- :data:`PURPOSE_TRAINING`, :data:`PURPOSE_ATTRIBUTION`,
  :data:`SUPPORTED_PURPOSES` — the purpose enum and validation set.
"""

from __future__ import annotations

from dataclasses import dataclass

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

# Purpose enum: which validation policy applies when calling
# :func:`resolve_industry_taxonomy`. ``"training"`` runs the full
# contract suite including the temporal-leakage check (a future-dated
# manifest used to label training rows would leak labels). ``"attribution"``
# is post-hoc analysis of an already-completed backtest, so a future
# manifest is acceptable — using "today's" Shenwan classification to
# bucket historical positions is the standard practice for industry
# attribution. Putting this choice in the function signature (instead
# of bypassing via ``reference_date=None``) means a future caller has
# to *say what they want* and a code reviewer can catch a wrong
# choice.
PURPOSE_TRAINING: str = "training"
PURPOSE_ATTRIBUTION: str = "attribution"
SUPPORTED_PURPOSES: frozenset[str] = frozenset({
    PURPOSE_TRAINING, PURPOSE_ATTRIBUTION,
})


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
    artifact_path: str | None,
    manifest_path: str | None,
    taxonomy_id: str | None,
    temporal_mode: str | None,
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
    purpose: str,
    reference_date: str | None = None,
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

    Parameters
    ----------
    purpose
        Either :data:`PURPOSE_TRAINING` or :data:`PURPOSE_ATTRIBUTION`.
        Required to make the temporal-leakage policy explicit at the
        call site rather than implicit in whether ``reference_date``
        is passed.

        - ``"training"``: the full contract runs, including
          ``has_future_known_metadata`` (a manifest dated after
          ``reference_date`` raises). This is the right policy when
          the artifact is used to label training data — lookahead bias
          is real there.
        - ``"attribution"``: post-hoc analysis of an already-completed
          backtest. Uses the most recent Shenwan classification to
          bucket historical positions, which is the industry-standard
          practice; a future-dated manifest is *not* a leakage risk
          because it never feeds back into trading signals. The
          contract runs without a ``reference_date`` so the
          future-snapshot check does not fire.
    reference_date
        Used only for ``purpose='training'``. For ``purpose='attribution'``
        we deliberately ignore the operator-provided value to keep the
        leakage check from firing on every fold of a backwards-looking
        walk-forward.
    """
    if purpose not in SUPPORTED_PURPOSES:
        raise IndustryTaxonomyLoadError(
            f"Unknown purpose {purpose!r}. Supported: "
            f"{sorted(SUPPORTED_PURPOSES)}."
        )

    # Attribution is post-hoc: we drop ``reference_date`` to opt out of
    # the temporal-leakage check. Pin the choice here so a future
    # caller cannot accidentally re-enable the check (or accidentally
    # disable it for training) by passing the wrong value.
    effective_reference_date = (
        reference_date if purpose == PURPOSE_TRAINING else None
    )

    try:
        profile = TaxonomyArtifactLoader.load(
            artifact_path=artifact_path,
            manifest_path=manifest_path,
            temporal_mode=temporal_mode,
            reference_date=effective_reference_date,
        )
        status = TaxonomyDataContract.validate_and_build_status(
            TaxonomyContractInput(
                taxonomy_name=taxonomy_id,
                temporal_mode=temporal_mode,
                profile=profile,
                reference_date=effective_reference_date,
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
