"""CSI800 sleeve-grouping loader for attribution (expansion (b) Step 3).

Why this module exists
----------------------
A csi800 run mixes two very different books: the csi300 large-cap sleeve
and the csi500 mid-cap sleeve. Aggregate excess return over SH000906TR
cannot show WHERE alpha (or slippage inflation) lives — the honest
anti-inflation report is a Brinson decomposition whose buckets are the
SLEEVES, not industries. The attribution engine already accepts an
arbitrary ``{instrument: group}`` mapping via
``AttributionConfig.industry_map_override`` + ``industry_taxonomy_id``;
this module builds that mapping from the bundle's PIT membership span
files (``instruments/csi300.txt`` / ``instruments/csi500.txt``, produced
by ``IndexMembershipResolver``), so the sleeve report reuses the whole
existing Brinson path instead of growing a parallel engine.

Semantics
---------
- Membership is resolved AS-OF one date (the attribution period's first
  day, mirroring the ``market_cap`` bench-weight as-of-T0 convention):
  Brinson consumes ONE static grouping per run, so a mid-window index
  rebalance cannot be represented anyway — the as-of date is stamped on
  the resolution for honest reporting.
- The span files are PIT products (membership intervals, re-entries as
  separate rows); resolving them as-of a historical date is not a
  lookahead risk — grouping feeds post-hoc analysis only, never signals.
- An instrument in BOTH sleeves as-of the same date is a data-integrity
  violation (CSI300 and CSI500 are disjoint by construction) and FAILS
  LOUD rather than silently picking a side.
- Instruments outside both sleeves are NOT labeled here: the attribution
  engine's documented fallback buckets unmapped instruments as
  ``"unknown"`` — for a csi800 run that bucket should be ~empty, and a
  visibly fat ``unknown`` row in the report is itself a loud signal.

This is STEP-3 PREPARATION: no pipeline / walk-forward config plumbing
(that wiring belongs to a future csi800 campaign spec). The Step-4 probe
brief consumes this loader post-hoc.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

SLEEVE_TAXONOMY_ID = "csi800_sleeve_v1"
SLEEVE_CSI300 = "csi300_sleeve"
SLEEVE_CSI500 = "csi500_sleeve"

_SLEEVE_FILES: tuple[tuple[str, str], ...] = (
    ("csi300.txt", SLEEVE_CSI300),
    ("csi500.txt", SLEEVE_CSI500),
)


class SleeveResolutionError(RuntimeError):
    """Raised on any failure while building the sleeve grouping map."""


@dataclass(frozen=True)
class SleeveResolution:
    """Frozen result: the grouping map + provenance for honest reporting."""

    sleeve_map: dict[str, str]
    taxonomy_id: str
    as_of: str
    n_csi300: int
    n_csi500: int


def _parse_iso(value: str, context: str) -> date:
    # date.fromisoformat accepts the compact "YYYYMMDD" form on 3.11+;
    # the span files and run configs are dashed — enforce the dashed
    # shape so a compact/typo'd date cannot silently parse.
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        raise SleeveResolutionError(
            f"{context}: {value!r} is not an ISO date (YYYY-MM-DD)."
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SleeveResolutionError(
            f"{context}: {value!r} is not an ISO date (YYYY-MM-DD)."
        ) from exc


def _members_as_of(path: Path, as_of: date) -> set[str]:
    """Instruments with any membership span containing ``as_of``."""
    if not path.is_file():
        raise SleeveResolutionError(
            f"membership span file missing: {path} — the bundle must carry "
            "the PIT membership products (IndexMembershipResolver output) "
            "before a sleeve report can be built."
        )
    members: set[str] = set()
    for lineno, raw in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            raise SleeveResolutionError(
                f"{path}:{lineno}: expected 'INSTRUMENT\\tSTART\\tEND', "
                f"got {raw!r}."
            )
        inst, start_s, end_s = parts
        start = _parse_iso(start_s, f"{path}:{lineno} START")
        end = _parse_iso(end_s, f"{path}:{lineno} END")
        if start > end:
            raise SleeveResolutionError(
                f"{path}:{lineno}: span start {start_s} > end {end_s}."
            )
        if start <= as_of <= end:
            members.add(inst)
    return members


def resolve_sleeve_map(provider_dir: Path | str,
                       as_of: str) -> SleeveResolution:
    """Build ``{instrument: sleeve}`` as-of ``as_of`` from the bundle's
    PIT membership span files. Feed the result into
    ``AttributionConfig(industry_map_override=resolution.sleeve_map,
    industry_taxonomy_id=resolution.taxonomy_id)``.

    Fails loud on: missing span files, malformed rows, an instrument in
    both sleeves (disjointness violation), or an empty sleeve (an as-of
    outside the membership data's coverage is a misconfiguration, not an
    empty index)."""
    root = Path(provider_dir) / "instruments"
    as_of_date = _parse_iso(as_of, "as_of")
    sleeve_map: dict[str, str] = {}
    counts: dict[str, int] = {}
    seen: dict[str, str] = {}
    for filename, label in _SLEEVE_FILES:
        members = _members_as_of(root / filename, as_of_date)
        if not members:
            raise SleeveResolutionError(
                f"no {label} members as-of {as_of} in {root / filename} — "
                "an as-of outside the membership coverage is a "
                "misconfiguration, not an empty index."
            )
        for inst in members:
            if inst in seen:
                raise SleeveResolutionError(
                    f"{inst} is a member of BOTH {seen[inst]} and {label} "
                    f"as-of {as_of} — CSI300/CSI500 are disjoint by "
                    "construction; refusing to silently pick a side."
                )
            seen[inst] = label
            sleeve_map[inst] = label
        counts[label] = len(members)
    return SleeveResolution(
        sleeve_map=sleeve_map,
        taxonomy_id=SLEEVE_TAXONOMY_ID,
        as_of=as_of,
        n_csi300=counts[SLEEVE_CSI300],
        n_csi500=counts[SLEEVE_CSI500],
    )
