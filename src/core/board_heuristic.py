"""A-share *board* heuristic — the only shared source of board buckets.

Why this module exists
----------------------
Two call sites previously carried identical 30-line implementations of
the same function:

* ``src.core.risk_constraints._code_based_sector_map``
* ``src.core.performance_attribution._code_based_sector_map``

Both classified A-share instruments into six buckets by code prefix
and both called the result ``sector``. But those buckets are **boards**,
not industries — the Shanghai Main Board alone contains banks, real
estate, utilities, steel, pharmaceuticals, and so on. Calling it
``sector`` invited a dangerous misreading of every risk constraint
and Brinson attribution that flowed from it: "industry concentration
limit" or "sector allocation effect" computed off this map are really
*board* statistics, and treating them as industry statistics leaks
into downstream decisions.

The dedup here serves three goals:

1. **One implementation**. Fixing a prefix rule (new board code, etc.)
   is a one-line change in one place.
2. **Honest names**. Bucket ids are prefixed with ``board_`` so they
   can never be confused with an industry-source classification.
   Downstream dashboards and log lines carry the prefix through,
   making the coarseness visible at every reading layer.
3. **Explicit taxonomy label**. :data:`BOARD_HEURISTIC_TAXONOMY_ID`
   is a string constant callers can stamp onto their result objects
   (e.g. ``AttributionResult.sector_taxonomy``), so consumers that
   receive the output can filter / flag it.

When a real industry classification (Shenwan L1/L2, CITIC) becomes
available, build a separate module that returns the same dict shape
with a different taxonomy id; callers can pick the source at the
config layer. Do **not** repurpose this module for that — its entire
reason to exist is "the only thing this tells you is which board a
code is listed on".
"""

from __future__ import annotations

from typing import Mapping


# Stable identifier for the taxonomy produced here. Stamp this onto
# result objects that expose a "sector" classification so consumers
# can tell the code-prefix heuristic apart from a real industry map.
BOARD_HEURISTIC_TAXONOMY_ID: str = "a_share_board_heuristic"


# Bucket labels — deliberately prefixed with ``board_`` so they can
# never be misread as industry names. Exposed so callers can iterate
# the full set (e.g. to allocate colors in a chart) without repeating
# the list.
BOARD_SH_MAIN: str = "board_SH_Main"
BOARD_SZ_MAIN: str = "board_SZ_Main"
BOARD_SME: str = "board_SME"
BOARD_CHINEXT: str = "board_ChiNext"
BOARD_STAR: str = "board_STAR"
BOARD_OTHER: str = "board_Other"

ALL_BOARDS: tuple[str, ...] = (
    BOARD_SH_MAIN,
    BOARD_SZ_MAIN,
    BOARD_SME,
    BOARD_CHINEXT,
    BOARD_STAR,
    BOARD_OTHER,
)


def classify_instrument(instrument: str) -> str:
    """Return the board bucket for a single A-share instrument code.

    ``instrument`` is an exchange-prefixed code like ``"SH600000"`` or
    ``"SZ300001"``. The SH/SZ prefix is stripped before matching the
    numeric prefix rules:

    * ``688xxx`` → ``board_STAR``
    * ``300xxx`` / ``301xxx`` → ``board_ChiNext``
    * ``002xxx`` → ``board_SME``
    * ``600xxx`` / ``601xxx`` / ``603xxx`` / ``605xxx`` → ``board_SH_Main``
    * ``000xxx`` / ``001xxx`` → ``board_SZ_Main``
    * anything else → ``board_Other``

    Unknown or malformed inputs bucket into ``board_Other`` rather than
    raising — this function is called on entire universes of instruments
    and a single bad code should not abort the whole classification.
    Callers that need strict validation should check upstream.
    """
    code = instrument.replace("SH", "").replace("SZ", "")
    if code.startswith("688"):
        return BOARD_STAR
    if code.startswith(("300", "301")):
        return BOARD_CHINEXT
    if code.startswith("002"):
        return BOARD_SME
    if code.startswith(("600", "601", "603", "605")):
        return BOARD_SH_MAIN
    if code.startswith(("000", "001")):
        return BOARD_SZ_MAIN
    return BOARD_OTHER


def classify_instruments(instruments: list[str]) -> dict[str, str]:
    """Return ``{instrument: board_bucket}`` for a list of codes.

    Thin convenience wrapper around :func:`classify_instrument`.
    """
    return {inst: classify_instrument(inst) for inst in instruments}


def is_board_bucket(label: str) -> bool:
    """Return True iff ``label`` is one of the board bucket ids this
    module produces. Downstream consumers can use this to tell the
    heuristic-derived buckets apart from a real industry classification
    that may be layered on later.
    """
    return label in ALL_BOARDS
