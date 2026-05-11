"""Publish a Shenwan L2 industry classification artifact via Tushare.

Pipeline
--------
::

    Tushare ``index_classify``  ->  list of L2 industry codes
                                ->  for each: ``index_member(index_code=...)``
    raw rows                    ->  ``(instrument, industry_name)`` tuples
                                ->  ``TaxonomyArtifactPublisher.publish``
                                ->  artifact csv + manifest json

Why this layering
-----------------
- :class:`TushareIndustryPublisher` is the only thing in the codebase
  that talks to Tushare's network APIs. Every other consumer reads the
  resulting on-disk artifact via :class:`TaxonomyArtifactLoader`. That
  isolates the vendor: swapping Tushare for, say, Wind or a CSV dump
  is a one-publisher change, not a fan-out.
- The actual artifact bytes are written by the existing
  :class:`TaxonomyArtifactPublisher` (``temporal_mode='static'``). We
  intentionally do NOT define a parallel artifact format — the project
  has one canonical taxonomy artifact shape and Tushare must conform to
  it.

Scope (v1)
----------
- Static snapshot only. ``temporal_mode='static'``.
- Shenwan L2 (~120 industries). The ``level`` and ``src`` parameters are
  exposed for completeness but the v1 contract is L2 / SW2021.
- ``industry_name`` (Chinese name, e.g. "白酒") is used as the artifact's
  ``industry_code`` column rather than Tushare's numeric ``index_code``
  ("850711.SI") so attribution reports render the human-readable name
  by default. If a stable numeric ID is needed later, a v2 mode can
  switch to ``index_code`` without changing the artifact shape.

Out of scope for v1
-------------------
- Time-varying classification (industry changes mid-period). Tushare's
  ``in_date`` / ``out_date`` columns are present in ``index_member`` but
  v1 only honours ``is_new='Y'`` (currently active members) and writes
  a flat snapshot.
- Network-level retries / rate-limit backoff. Tushare's Pro tier
  comfortably handles ~120 sequential calls; if a call fails we surface
  it loudly and let the operator re-run.
- Disk caching. v1 always re-fetches. v2 can layer a parquet cache
  underneath without changing the publisher contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from src.core.logger import get_logger
from src.data.taxonomy_artifact_publisher import (
    TaxonomyArtifactPublisher,
    TaxonomyArtifactPublisherError,
    TaxonomyPublishResult,
)
from src.data.tushare.client import TushareClient, TushareClientError

_logger = get_logger(__name__)


# Default taxonomy name written into the artifact manifest. Stable
# string so downstream consumers can branch on
# ``profile.taxonomy_name == "tushare_sw_l2"`` if they ever need to
# distinguish Tushare-published industries from a future alternative
# source.
SW_L2_TAXONOMY_NAME = "tushare_sw_l2"

# Default Tushare ``src`` parameter. SW2021 is the active Shenwan
# revision; SW2014 is the legacy one. Override at the publisher call
# site if you specifically need the legacy taxonomy for a
# back-compatibility study.
DEFAULT_SHENWAN_SRC = "SW2021"


class TushareIndustryPublisherError(RuntimeError):
    """Raised when the publisher cannot produce a valid industry artifact.

    Wraps both Tushare-side failures (:class:`TushareClientError`) and
    artifact-write failures (:class:`TaxonomyArtifactPublisherError`)
    so callers can ``except`` a single type.
    """


@dataclass(frozen=True)
class TushareIndustryPublishResult:
    """Summary returned by a successful publish call.

    Carries the lower-level :class:`TaxonomyPublishResult` so callers
    can introspect the artifact path / manifest path / row count, plus
    Tushare-specific provenance (industry count, source revision).
    """

    taxonomy_result: TaxonomyPublishResult
    industries_fetched: int
    instruments_classified: int
    shenwan_src: str
    level: str


class TushareIndustryPublisher:
    """Publish a static Shenwan industry-classification artifact.

    Usage::

        export TUSHARE_TOKEN='...'
        result = TushareIndustryPublisher.publish(
            artifact_path='output/taxonomy/sw_l2.csv',
            manifest_path='output/taxonomy/sw_l2.json',
            snapshot_at='2026-04-25',
        )
        # result.taxonomy_result.artifact_path is now a usable
        # canonical taxonomy artifact, readable by TaxonomyArtifactLoader.
    """

    @classmethod
    def publish(
        cls,
        *,
        artifact_path: str,
        manifest_path: str,
        snapshot_at: str,
        level: str = "L2",
        shenwan_src: str = DEFAULT_SHENWAN_SRC,
        taxonomy_name: str = SW_L2_TAXONOMY_NAME,
        client: TushareClient | None = None,
    ) -> TushareIndustryPublishResult:
        """Fetch the industry classification and persist it as an artifact.

        Parameters
        ----------
        artifact_path, manifest_path
            Destination paths. Forwarded to
            :meth:`TaxonomyArtifactPublisher.publish`.
        snapshot_at
            ISO date (YYYY-MM-DD) recording when the snapshot was taken.
            Required because the Tushare ``index_member`` call we use
            returns "currently active" members, not a date-stamped view —
            without an explicit snapshot date the artifact would have no
            way to declare *as of when* the membership is valid.
        level
            Shenwan level. v1 contract is "L2" but we expose the param
            for parity with the Tushare API. Other levels work but are
            not test-covered.
        shenwan_src
            Tushare ``src`` parameter. Defaults to "SW2021"; pass
            "SW2014" to publish against the legacy taxonomy.
        client
            Optional pre-built :class:`TushareClient`. Defaults to
            :meth:`TushareClient.from_environment`. Tests inject a
            stub client here to avoid the network.
        """
        if client is None:
            try:
                client = TushareClient.from_environment()
            except TushareClientError as exc:
                raise TushareIndustryPublisherError(str(exc)) from exc

        cls._validate_iso_date(snapshot_at, "snapshot_at")

        _logger.info(
            "Fetching Shenwan %s industry list (src=%s) from Tushare...",
            level, shenwan_src,
        )
        try:
            industry_df = client.call(
                "index_classify", level=level, src=shenwan_src,
            )
        except TushareClientError as exc:
            raise TushareIndustryPublisherError(
                f"Failed to fetch industry list: {exc}"
            ) from exc

        industries = cls._parse_industry_list(industry_df, level=level)
        _logger.info("Tushare returned %d industries.", len(industries))
        if not industries:
            raise TushareIndustryPublisherError(
                f"Tushare 'index_classify' returned zero industries for "
                f"level={level} src={shenwan_src}. Check account permissions "
                "or the level/src parameters."
            )

        rows: list[tuple[str, str]] = []
        for idx, (index_code, industry_name) in enumerate(industries):
            _logger.info(
                "[%d/%d] Fetching members for industry %s (%s)...",
                idx + 1, len(industries), industry_name, index_code,
            )
            try:
                members_df = client.call(
                    "index_member", index_code=index_code,
                )
            except TushareClientError as exc:
                raise TushareIndustryPublisherError(
                    f"Failed to fetch members for industry "
                    f"{index_code} ({industry_name}): {exc}. "
                    "Re-run after rate limit resets; the publisher does "
                    "not partial-write — either every industry succeeds "
                    "or the artifact is not produced."
                ) from exc

            for instrument in cls._parse_active_members(members_df):
                rows.append((instrument, industry_name))

        if not rows:
            raise TushareIndustryPublisherError(
                "No active members were returned for any industry. "
                "The artifact would be empty; refusing to publish."
            )

        _logger.info(
            "Classified %d (instrument, industry) pairs; writing artifact...",
            len(rows),
        )

        try:
            taxonomy_result = TaxonomyArtifactPublisher.publish(
                taxonomy_name=taxonomy_name,
                temporal_mode="static",
                rows=rows,
                artifact_path=artifact_path,
                manifest_path=manifest_path,
                source_name=f"tushare-{shenwan_src.lower()}-{level.lower()}",
                source_uri=f"tushare://index_classify?level={level}&src={shenwan_src}",
                snapshot_at=snapshot_at,
            )
        except TaxonomyArtifactPublisherError as exc:
            raise TushareIndustryPublisherError(
                f"Underlying TaxonomyArtifactPublisher failed: {exc}"
            ) from exc

        return TushareIndustryPublishResult(
            taxonomy_result=taxonomy_result,
            industries_fetched=len(industries),
            instruments_classified=len(rows),
            shenwan_src=shenwan_src,
            level=level,
        )

    # ------------------------------------------------------------------
    # Helpers — keep parsing isolated so tests can poke them directly.
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_iso_date(value: Any, field_name: str) -> None:
        """Reject non-ISO date strings up front."""
        try:
            date.fromisoformat(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise TushareIndustryPublisherError(
                f"{field_name} must be ISO date YYYY-MM-DD; got {value!r}."
            ) from exc

    @staticmethod
    def _parse_industry_list(
        df: Any, *, level: str,
    ) -> list[tuple[str, str]]:
        """Extract ``(index_code, industry_name)`` pairs from
        ``index_classify``'s DataFrame.

        Tushare's response shape: columns include ``index_code``,
        ``industry_name``, ``level``, plus several others. We filter on
        ``level`` to be safe even when the API returned a mixed-level
        result (sometimes happens with custom permissions). Rows
        missing either of the two columns we need are skipped with a
        WARNING — partial response should not crash but should be
        visible.
        """
        if df is None or len(df) == 0:
            return []

        for col in ("index_code", "industry_name"):
            if col not in df.columns:
                raise TushareIndustryPublisherError(
                    f"Tushare 'index_classify' response missing required "
                    f"column {col!r}. Columns present: {list(df.columns)}."
                )

        import pandas as pd  # local import — avoids module-level dep

        out: list[tuple[str, str]] = []
        for _, row in df.iterrows():
            row_level = row.get("level", "")
            if row_level and str(row_level).strip().upper() != level.upper():
                continue
            index_code = row.get("index_code")
            industry_name = row.get("industry_name")
            # ``pd.isna`` catches both ``None`` and ``np.nan``. The
            # previous ``is None`` check let NaN values through, which
            # then became the literal string ``"nan"`` after
            # ``str(np.nan).strip()`` and triggered confusing
            # ``"Failed to fetch members for industry nan"`` errors
            # downstream when the publisher tried to fetch members for
            # the bogus industry.
            if pd.isna(index_code) or pd.isna(industry_name):
                _logger.warning(
                    "Skipping industry row with missing fields: %r", dict(row)
                )
                continue
            out.append((str(index_code).strip(), str(industry_name).strip()))
        return out

    @staticmethod
    def _parse_active_members(df: Any) -> list[str]:
        """Extract currently-active members from ``index_member``'s DataFrame.

        Tushare returns ``con_code`` like ``"600000.SH"``; we convert to
        the qlib-compatible ``"SH600000"`` format used everywhere else
        in the codebase. Members with ``is_new`` other than ``'Y'`` are
        skipped (they have left the industry).

        ``is_new`` may be missing in rare partial responses — we treat
        absent as "active" rather than dropping silently.
        """
        if df is None or len(df) == 0:
            return []
        if "con_code" not in df.columns:
            raise TushareIndustryPublisherError(
                f"Tushare 'index_member' response missing required column "
                f"'con_code'. Columns present: {list(df.columns)}."
            )

        out: list[str] = []
        for _, row in df.iterrows():
            is_new = row.get("is_new", "Y")
            if is_new is not None and str(is_new).strip().upper() != "Y":
                continue
            con_code = row.get("con_code")
            if con_code is None:
                continue
            converted = _tushare_to_qlib_instrument(str(con_code).strip())
            if converted is None:
                continue
            out.append(converted)
        return out


def _tushare_to_qlib_instrument(con_code: str) -> str | None:
    """Convert Tushare's ``"600000.SH"`` to qlib's ``"SH600000"``.

    Returns ``None`` for codes that do not match the dotted exchange-
    suffix shape (e.g. an empty string, or a Hong Kong code like
    ``"00700.HK"`` if it ever leaked through). ``None`` is dropped by
    the caller; we don't raise so a single weird row in a 5000-row
    response doesn't kill the whole publish.

    Supported suffixes mirror what's in ``board_heuristic``:
    ``.SH`` / ``.SZ`` / ``.BJ`` (北交所). Any other suffix returns
    ``None``.
    """
    if "." not in con_code:
        return None
    code, _, suffix = con_code.rpartition(".")
    suffix = suffix.upper()
    if suffix not in ("SH", "SZ", "BJ"):
        return None
    if not code.isdigit() or len(code) != 6:
        return None
    return f"{suffix}{code}"
