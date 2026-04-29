"""Read a published taxonomy CSV into the ``{instrument: industry}``
dict shape that ``risk_constraints`` and ``performance_attribution``
already accept.

Why a separate loader
---------------------
:class:`src.data.taxonomy_artifact_loader.TaxonomyArtifactLoader` is the
canonical artifact-validity check (file presence, snapshot dates,
staleness flags). It returns a :class:`TaxonomyArtifactProfile` whose
``rows`` field is a *row count*, not the row contents — perfect for
contract validation, useless for "give me the actual mapping".

Downstream consumers want the ``dict[str, str]`` mapping; this module
fills that gap. It deliberately reads the same CSV header that
:class:`TaxonomyArtifactPublisher` writes (``instrument`` /
``industry_code``) so swapping the publishing source (Tushare today,
Wind / a CSV dump tomorrow) does not propagate.

Validation kept here
--------------------
- Missing CSV → loud error.
- Wrong header (no ``instrument`` / ``industry_code`` columns) → loud
  error. A silent KeyError later would be much harder to debug.
- Duplicate ``instrument`` keys → loud error. Two industries claiming
  the same stock would silently overwrite in a plain dict, hiding a
  bad publish.
- Empty CSV (header only) → loud error. The publisher refuses to write
  empty artifacts; an empty one on disk indicates corruption or a
  hand-edited file.

Validation explicitly NOT here
------------------------------
- Manifest schema / staleness / snapshot date — that's
  :class:`TaxonomyArtifactLoader`'s job. Callers that care about
  those invariants should run both loaders.
- Industry-name shape (Chinese text? ASCII? specific length?). The
  publisher uses Tushare's ``industry_name`` as-is; downstream code
  treats it as an opaque string identifier.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Mapping


_INSTRUMENT_COLUMN = "instrument"
_INDUSTRY_COLUMN = "industry_code"


class IndustryMapLoaderError(ValueError):
    """Raised on malformed / missing / duplicated industry artifact."""


def load_industry_map(artifact_path: str | Path) -> dict[str, str]:
    """Read a static taxonomy CSV into a ``{instrument: industry}`` dict.

    The CSV must carry the header
    ``instrument,industry_code[,...optional...]``. Any extra trailing
    columns are tolerated (so a v2 publisher can add fields like
    ``industry_name_en`` without breaking v1 consumers); only the two
    base columns are read.
    """
    p = Path(artifact_path)
    if not p.exists():
        raise IndustryMapLoaderError(
            f"Industry artifact file does not exist: {p}. "
            "Run scripts/ingest_tushare_industry.py first to publish it."
        )

    with p.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise IndustryMapLoaderError(
                f"Industry artifact at {p} is completely empty (no header)."
            )

        try:
            instrument_idx = header.index(_INSTRUMENT_COLUMN)
            industry_idx = header.index(_INDUSTRY_COLUMN)
        except ValueError as exc:
            raise IndustryMapLoaderError(
                f"Industry artifact at {p} is missing a required column. "
                f"Expected '{_INSTRUMENT_COLUMN}' and '{_INDUSTRY_COLUMN}' "
                f"in header; got {header!r}."
            ) from exc

        mapping: dict[str, str] = {}
        for line_no, row in enumerate(reader, start=2):
            if not row or all(not cell.strip() for cell in row):
                # Tolerate fully blank lines (rare in well-formed CSV
                # but harmless if a hand-edit introduced one).
                continue
            if max(instrument_idx, industry_idx) >= len(row):
                raise IndustryMapLoaderError(
                    f"Row {line_no} in {p} has fewer columns than the "
                    f"header declared (got {len(row)}, expected at least "
                    f"{max(instrument_idx, industry_idx) + 1})."
                )
            instrument = row[instrument_idx].strip()
            industry = row[industry_idx].strip()
            if not instrument or not industry:
                raise IndustryMapLoaderError(
                    f"Row {line_no} in {p} has an empty instrument "
                    f"({instrument!r}) or industry ({industry!r}). "
                    "The publisher rejects empty rows; an empty one on "
                    "disk means manual edit or corruption."
                )
            if instrument in mapping:
                raise IndustryMapLoaderError(
                    f"Duplicate instrument {instrument!r} on row {line_no} "
                    f"of {p} — first seen with industry {mapping[instrument]!r}, "
                    f"now {industry!r}. A static taxonomy must have one "
                    "row per instrument."
                )
            mapping[instrument] = industry

    if not mapping:
        raise IndustryMapLoaderError(
            f"Industry artifact at {p} has a header but zero data rows. "
            "Re-run the publisher to regenerate."
        )

    return mapping


def coerce_industry_map(mapping: Mapping[str, str]) -> dict[str, str]:
    """Cast a ``Mapping[str, str]`` to a plain ``dict[str, str]``.

    Convenience for call sites that received a ``Mapping`` (which is
    the contract type used by ``RiskConstraintConfig.industry_map``)
    and need a mutable / inspectable dict for diagnostics. Pure type
    coercion — no validation.
    """
    return {str(k): str(v) for k, v in mapping.items()}
